"""Settings: config, agents.yaml, Godot, git identity, notifications, Telegram setup.

One panel at a time. The Settings screen sends only the section the user changed, so a stale
browser tab cannot roll back an unrelated change - which is the failure mode of "PUT the whole
config" APIs.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ...core import paths
from ...core.config import load_config, update_config
from ...core.events import bus
from ...notifications import service as notifications
from ...orchestration.git_ops import detect_identity, git_available, set_global_identity
from ...orchestration import projects
from ...runtime import runtime
from ..deps import guarded
from ..schemas import GitSettingsRequest, GodotSettingsRequest, SettingsRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict[str, Any]:
    config = load_config()
    return {
        "config": config.model_dump(mode="json"),
        "paths": {
            "studio_home": str(paths.studio_home()),
            "projects_root": str(paths.projects_root(config.projects_root or None)),
            "config_file": str(paths.config_file()),
            "agents_file": str(paths.agents_file()),
            "theme_file": str(paths.theme_file()),
            "vault_file": str(paths.vault_file()),
        },
        "git": {
            "available": git_available(),
            "detected": detect_identity(),
            "project": _project_git(),
        },
        "godot": {
            "configured": config.godot.executable,
            "version": config.godot.version,
            "detected": _godot_candidates(),
            "ffmpeg": _ffmpeg(),
        },
    }


@router.put("")
@guarded("save settings")
def update_settings(payload: SettingsRequest) -> dict[str, Any]:
    """Merge one or more sections into ``config.yaml``."""
    changes: dict[str, Any] = payload.model_dump(exclude_none=True, exclude_unset=True)
    if not changes:
        return {"config": load_config().model_dump(mode="json"), "changed": []}
    if "projects_root" in changes:
        root = Path(changes["projects_root"]).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "projects_root_unwritable",
                    "message": f"Could not create {root}: {exc}",
                },
            ) from exc
        changes["projects_root"] = str(root)
    config = update_config(**changes)
    needs_restart = any(key in changes for key in ("runtime", "godot"))
    if needs_restart:
        runtime.refresh_dispatcher()
    bus.publish("settings_saved", {"sections": sorted(changes.keys())})
    return {
        "config": config.model_dump(mode="json"),
        "changed": sorted(changes.keys()),
        "note": (
            "Parallelism and Godot changes apply from the next dispatch tick."
            if needs_restart
            else ""
        ),
    }


@router.get("/godot")
def godot() -> dict[str, Any]:
    """Godot status: what is configured, what was detected, and whether the path works."""
    config = load_config()
    from ...mcp.godot_mcp_client import GodotMCPClient

    status: dict[str, Any] = {
        "configured": config.godot.executable,
        "version": config.godot.version,
        "detected": _godot_candidates(),
        "works": False,
    }
    if config.godot.executable:
        client = GodotMCPClient(executable=config.godot.executable)
        try:
            version = client.version()
            status.update({"works": bool(version), "reported_version": version})
        finally:
            client.close()
        if not status["works"]:
            status["message"] = (
                "That path did not report a version. It should be the Godot 4 editor executable "
                "(Godot_v4.x-stable_win64.exe), not a shortcut or the project manager."
            )
    else:
        status["message"] = (
            "Point this at your Godot 4 executable. The studio runs it headless for tests and "
            "windowed when you want to watch the game."
        )
    return status


@router.get("/godot/detect")
def godot_detect() -> dict[str, Any]:
    return {"candidates": _godot_candidates(), "configured": load_config().godot.executable}


@router.put("/godot")
@guarded("save godot settings")
def save_godot(payload: GodotSettingsRequest) -> dict[str, Any]:
    changes = payload.model_dump(exclude_none=True)
    if "executable" in changes:
        candidate = Path(changes["executable"]).expanduser()
        if changes["executable"] and not candidate.exists():
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "godot_path_missing",
                    "message": f"{candidate} does not exist. Pick the Godot executable file.",
                },
            )
        if changes["executable"]:
            from ...mcp.godot_mcp_client import GodotMCPClient

            client = GodotMCPClient(executable=str(candidate))
            try:
                version = client.version()
            finally:
                client.close()
            if version:
                changes["version"] = version
    config = update_config(godot=changes)
    runtime.refresh_dispatcher()
    return {"godot": config.godot.model_dump(mode="json")}


@router.get("/git")
def git_settings() -> dict[str, Any]:
    return {
        "global": detect_identity(),
        "project": _project_git(),
        "available": git_available(),
        "config": load_config().git.model_dump(mode="json"),
    }


@router.put("/git")
@guarded("save git settings")
def save_git(payload: GitSettingsRequest) -> dict[str, Any]:
    changes = payload.model_dump(exclude_none=True)
    config = update_config(git=changes)
    if changes.get("user_name") and changes.get("user_email"):
        repo = _active_repo()
        if repo is not None:
            repo.set_identity(changes["user_name"], changes["user_email"])
    return {"git": config.git.model_dump(mode="json")}


@router.post("/git/global-identity")
@guarded("set global git identity")
def set_identity(name: str = Query(min_length=1, max_length=120), email: str = Query(min_length=3, max_length=200)) -> dict[str, Any]:
    """Set the machine-wide git identity, but only when the user explicitly asks for it."""
    ok, detail = set_global_identity(name, email)
    if ok:
        update_config(git={"user_name": name, "user_email": email})
    return {"ok": ok, "detail": detail, "detected": detect_identity()}


@router.get("/notifications")
def notification_settings() -> dict[str, Any]:
    return notifications.status()


@router.post("/telegram/chat-id")
@guarded("detect telegram chat id")
def telegram_chat_id() -> dict[str, Any]:
    return notifications.detect_telegram_chat_id()


@router.post("/telegram/test")
@guarded("test telegram")
def telegram_test() -> dict[str, Any]:
    ok, detail = notifications.send_telegram(
        "PulseG Studio", "This is a test message from your studio. Nothing to do."
    )
    return {"ok": ok, "detail": detail}


@router.get("/agents-file")
def agents_file() -> dict[str, Any]:
    """The raw ``agents.yaml``. Editing it by hand is supported, not discouraged."""
    path = paths.agents_file()
    return {
        "path": str(path),
        "exists": path.exists(),
        "content": path.read_text(encoding="utf-8") if path.exists() else "",
        "note": "Any edit here needs a reload to take effect; Settings > Agents has a Reload button.",
    }


@router.put("/agents-file")
@guarded("write agents file")
def write_agents_file(payload: dict[str, str]) -> dict[str, Any]:
    """Write ``agents.yaml`` directly, then reload the registry against the new content."""
    from ...agents.registry import registry
    from ...core.atomic import atomic_write_text

    content = str(payload.get("content", ""))
    if not content.strip():
        raise HTTPException(
            status_code=422,
            detail={"error": "empty", "message": "Refusing to write an empty agents.yaml."},
        )
    try:
        import yaml

        parsed = yaml.safe_load(content)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_yaml", "message": f"That is not valid YAML: {exc}"},
        ) from exc
    if not isinstance(parsed, list) or not parsed:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_shape",
                "message": "agents.yaml must be a list of agent definitions.",
            },
        )
    atomic_write_text(paths.agents_file(), content)
    registry.reload()
    return {"ok": True, "agents": len(registry.agents), "problems": registry.problems}


@router.post("/agents/reload")
@guarded("reload agents")
def reload_agents() -> dict[str, Any]:
    from ...agents.registry import registry

    registry.reload()
    return {"agents": len(registry.agents), "problems": registry.problems}


@router.get("/providers-file")
def providers_file() -> dict[str, Any]:
    path = paths.providers_file()
    return {
        "path": str(path),
        "exists": path.exists(),
        "content": path.read_text(encoding="utf-8") if path.exists() else "",
    }


@router.post("/reset")
@guarded("reset settings")
def reset(section: str = Query(default="ui")) -> dict[str, Any]:
    """Reset one settings section to its shipped default. Never touches keys or projects."""
    allowed = {"ui", "runtime", "notifications", "git", "godot"}
    if section not in allowed:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unknown_section",
                "message": f"'{section}' cannot be reset. Choose one of: {', '.join(sorted(allowed))}.",
            },
        )
    from ...core.config import (
        GitSettings,
        GodotSettings,
        NotificationSettings,
        RuntimeSettings,
        UISettings,
    )

    defaults = {
        "ui": UISettings(),
        "runtime": RuntimeSettings(demo_mode=load_config().runtime.demo_mode),
        "notifications": NotificationSettings(
            telegram_chat_id=load_config().notifications.telegram_chat_id,
            telegram_enabled=load_config().notifications.telegram_enabled,
        ),
        "git": GitSettings(
            user_name=load_config().git.user_name,
            user_email=load_config().git.user_email,
        ),
        "godot": GodotSettings(
            executable=load_config().godot.executable,
            version=load_config().godot.version,
        ),
    }
    config = update_config(**{section: defaults[section].model_dump(mode="json")})
    return {"config": config.model_dump(mode="json"), "reset": section}


@router.post("/export")
def export_config() -> dict[str, Any]:
    """Everything except keys, so a user can move their setup to another machine.

    Keys are deliberately excluded. Exporting them would put secrets in a plain file the user
    will email to themselves.
    """
    config = load_config()
    return {
        "config": config.model_dump(mode="json"),
        "note": "API keys are not included. Re-enter them in Settings on the new machine.",
    }


@router.get("/storage")
def storage() -> dict[str, Any]:
    """Where the disk space went, so "why is this folder 4 GB" has an answer."""
    home = paths.studio_home()
    root = paths.projects_root(load_config().projects_root or None)

    def size_of(path: Path, limit: int = 4000) -> int:
        if not path.exists():
            return 0
        total = 0
        for index, item in enumerate(path.rglob("*")):
            if index > limit:
                break
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    projects_rows = []
    for record in projects.list_projects():
        path = projects.project_path_of(record)
        projects_rows.append(
            {
                "project_id": record.get("project_id"),
                "name": record.get("name"),
                "path": str(path),
                "bytes": size_of(path),
                "exists": path.exists(),
            }
        )
    return {
        "studio_home": {"path": str(home), "bytes": size_of(home)},
        "projects_root": {"path": str(root), "bytes": size_of(root)},
        "projects": projects_rows,
        "note": "Screenshots and web exports are usually the largest folders in a project.",
    }


def _godot_candidates() -> list[str]:
    from ...mcp.godot_mcp_client import detect_godot_candidates

    return detect_godot_candidates()[:12]


def _ffmpeg() -> dict[str, Any]:
    from ...mcp import ffmpeg_mcp

    return ffmpeg_mcp.describe()


def _active_repo():
    record = runtime.record
    if record is None:
        return None
    from ...orchestration.git_ops import GitRepo

    return GitRepo(projects.project_path_of(record))


def _project_git() -> dict[str, Any]:
    repo = _active_repo()
    if repo is None:
        return {"initialised": False, "note": "No project is open."}
    return {
        "initialised": repo.initialised,
        "identity": repo.identity(),
        "branch": repo.current_branch() if repo.initialised else "",
        "status": repo.status() if repo.initialised else {},
    }


def _which(name: str) -> str:
    return shutil.which(name) or ""
