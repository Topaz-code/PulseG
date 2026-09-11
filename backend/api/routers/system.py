"""System: health, version, first-run state, and the Setup Wizard.

The wizard is deliberately a backend concern, not a frontend form: it writes ``config.yaml``,
detects Godot, checks git identity and stores any keys the user pastes. Doing that in the UI
would mean the browser held secrets, which is exactly what the vault exists to avoid.
"""
from __future__ import annotations

import logging
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from ... import __app_name__, __version__
from ...core import paths
from ...core.atomic import read_json
from ...core.config import load_config, update_config
from ...core.events import bus
from ...core.index import index
from ...core.vault import Vault, load_meta
from ...mcp import ffmpeg_mcp, screenshot_mcp
from ...mcp.godot_mcp_client import detect_godot_candidates
from ...notifications import service as notifications
from ...orchestration.git_ops import detect_identity, git_available
from ...orchestration import projects
from ...runtime import runtime
from ..deps import guarded
from ..schemas import SetupWizardRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    """Cheap liveness probe. Also what the Tauri shell polls while the sidecar boots."""
    return {
        "ok": True,
        "app": __app_name__,
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "first_run_complete": load_config().first_run_complete,
    }


@router.get("/system/info")
def system_info() -> dict[str, Any]:
    """Everything the Settings > About panel and the wizard's checks need."""
    config = load_config()
    return {
        "app": {"name": __app_name__, "version": __version__, "python": sys.version.split()[0]},
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "is_windows": platform.system() == "Windows",
        },
        "paths": {
            "studio_home": str(paths.studio_home()),
            "projects_root": str(paths.projects_root(config.projects_root or None)),
            "config_file": str(paths.config_file()),
            "agents_file": str(paths.agents_file()),
            "theme_file": str(paths.theme_file()),
            "vault_file": str(paths.vault_file()),
            "log_file": str(paths.studio_log_file()),
            "sqlite_file": str(paths.sqlite_file()),
        },
        "tools": {
            "git": {"available": git_available(), "executable": shutil.which("git") or ""},
            "godot": {
                "configured": config.godot.executable,
                "detected": detect_godot_candidates()[:8],
                "version": config.godot.version,
            },
            "ffmpeg": ffmpeg_mcp.describe(),
            "screenshots": screenshot_mcp.describe_modes(),
            "node": shutil.which("node") or "",
            "npx": shutil.which("npx") or "",
        },
        "config": config.model_dump(mode="json"),
        "run_state": runtime.as_dict(),
        "events": bus.stats(),
        "vault": {"providers_with_keys": Vault().providers(), "meta": load_meta()},
        "project_count": len(projects.list_projects()),
        "unread_notifications": notifications.unread_count(),
    }


@router.get("/system/first-run")
def first_run() -> dict[str, Any]:
    """Whether the Setup Wizard should run, and what it still needs to know."""
    config = load_config()
    projects_root = paths.projects_root(config.projects_root or None)
    return {
        "first_run_complete": config.first_run_complete,
        "projects_root": str(projects_root),
        "projects_root_exists": projects_root.exists(),
        "projects_root_writable": _writable(projects_root),
        "godot_configured": bool(config.godot.executable),
        "godot_detected": detect_godot_candidates()[:8],
        "git_available": git_available(),
        "git_identity": detect_identity(),
        "has_any_key": bool(Vault().providers()),
        "has_project": bool(projects.list_projects()),
        "keyless_providers": _keyless(),
        "steps": [
            {"id": "welcome", "label": "Welcome", "required": True},
            {"id": "folders", "label": "Where your games live", "required": True},
            {"id": "godot", "label": "Godot 4.x location", "required": False},
            {"id": "keys", "label": "Model providers (optional)", "required": False},
            {"id": "git", "label": "Git identity", "required": False},
            {"id": "notifications", "label": "Notifications", "required": False},
            {"id": "done", "label": "Finish", "required": True},
        ],
    }


@router.post("/system/setup")
@guarded("finish setup")
def finish_setup(payload: SetupWizardRequest) -> dict[str, Any]:
    """Apply the wizard's answers. Safe to run again: it only overwrites what it is given."""
    root = Path(payload.projects_root).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "projects_root_unwritable",
                "message": (
                    f"Could not create {root}: {exc}. Pick a folder you can write to, such as "
                    "your Documents folder."
                ),
            },
        ) from exc
    if not _writable(root):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "projects_root_unwritable",
                "message": f"{root} is not writable. Pick another folder.",
            },
        )

    stored_keys: list[str] = []
    vault = Vault()
    for provider_id, key in (payload.keys or {}).items():
        if key and key.strip():
            vault.set(provider_id.strip(), key.strip())
            stored_keys.append(provider_id.strip())

    git_report: dict[str, Any] = {"configured": False}
    if payload.git_name and payload.git_email:
        if payload.configure_git_globally:
            from ...orchestration.git_ops import set_global_identity

            ok, detail = set_global_identity(payload.git_name, payload.git_email)
            git_report = {"configured": ok, "global": True, "detail": detail}
        else:
            git_report = {
                "configured": True,
                "global": False,
                "detail": "Saved for new projects only; your global git config was not touched.",
            }

    updates: dict[str, Any] = {
        "projects_root": str(root),
        "first_run_complete": True,
        "setup_wizard_completed_at": _now(),
        "git": {
            "user_name": payload.git_name,
            "user_email": payload.git_email,
        },
    }
    if payload.godot_executable:
        updates["godot"] = {"executable": payload.godot_executable}
    if payload.notifications is not None:
        updates["notifications"] = payload.notifications.model_dump(exclude_none=True)
    config = update_config(**updates)

    # Verify the Godot path the user gave us, and record the version we actually found.
    godot_report: dict[str, Any] = {"path": payload.godot_executable, "ok": False}
    if payload.godot_executable:
        from ...mcp.godot_mcp_client import GodotMCPClient

        client = GodotMCPClient(executable=payload.godot_executable)
        try:
            version = client.version()
            godot_report.update({"ok": bool(version), "version": version})
            if version:
                update_config(godot={"version": version})
        finally:
            client.close()
        if not godot_report["ok"]:
            godot_report["message"] = (
                "That file did not report a version. Check it is the Godot 4 editor or console "
                "executable, not the project manager's shortcut."
            )

    try:
        index.rebuild(projects.list_projects())
    except Exception as exc:  # pragma: no cover - index rebuild is best effort
        log.info("Index rebuild skipped: %s", exc)

    bus.publish("first_run_complete", {"projects_root": str(root)})
    return {
        "ok": True,
        "config": config.model_dump(mode="json"),
        "keys_saved": stored_keys,
        "git": git_report,
        "godot": godot_report,
    }


@router.post("/system/rebuild-index")
@guarded("rebuild index")
def rebuild_index() -> dict[str, Any]:
    """Rebuild the SQLite cache from the project files. The files always win."""
    count = index.rebuild(projects.list_projects())
    return {"ok": True, "projects": count, "stats": index.stats()}


@router.get("/system/diagnostics")
def diagnostics() -> dict[str, Any]:
    """The bundle a user attaches when something goes wrong. No secrets, by construction."""
    config = load_config()
    vault = Vault()
    return {
        "version": __version__,
        "platform": platform.platform(),
        "python": sys.version,
        "config": config.model_dump(mode="json"),
        "paths": {
            "studio_home": str(paths.studio_home()),
            "projects_root": str(paths.projects_root(config.projects_root or None)),
        },
        "providers": {
            "configured": vault.providers(),
            "masked": {provider: vault.describe(provider).display for provider in vault.providers()},
        },
        "tools": {
            "git": git_available(),
            "ffmpeg": ffmpeg_mcp.available(),
            "godot": bool(config.godot.executable),
            "node": bool(shutil.which("node")),
        },
        "run_state": runtime.as_dict(),
        "events": bus.stats(),
        "index": index.stats(),
        "projects": [
            {
                "project_id": record.get("project_id"),
                "name": record.get("name"),
                "mode": record.get("mode"),
                "phase": record.get("phase"),
                "exists": Path(record.get("path", "")).exists(),
            }
            for record in projects.list_projects()
        ],
        "note": (
            "This report contains no API keys and no file contents. It is safe to share when "
            "asking for help."
        ),
    }


@router.post("/system/shutdown")
def shutdown() -> dict[str, Any]:
    """Stop the dispatch loop. The Tauri shell kills the process on window close."""
    runtime.stop(wait=True)
    return {"ok": True, "run_state": runtime.as_dict()}


@router.get("/system/notifications")
def notifications_status() -> dict[str, Any]:
    return notifications.status()


@router.post("/system/notifications/test")
@guarded("send test notification")
def notifications_test() -> dict[str, Any]:
    return notifications.test_all()


@router.get("/system/notifications/log")
def notifications_log(limit: int = 100) -> dict[str, Any]:
    return {"items": notifications.read_log(limit=max(1, min(limit, 300))), "unread": notifications.unread_count()}


@router.post("/system/notifications/read")
def notifications_read(event_id: str = "") -> dict[str, Any]:
    return {"marked": notifications.mark_read(event_id)}


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".pulseg-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _keyless() -> list[dict[str, Any]]:
    from ...providers.specs import PROVIDERS

    return [
        {
            "id": spec.id,
            "name": spec.name,
            "note": spec.free_tier,
            "docs_url": spec.docs_url,
        }
        for spec in PROVIDERS.values()
        if not spec.requires_key
    ]


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# `/system/settings` handling lives in settings.py, but the wizard's own state endpoint is here
# because it is part of first-run rather than day-to-day configuration.
@router.get("/system/wizard-state")
def wizard_state() -> dict[str, Any]:
    config = load_config()
    payload = read_json(paths.studio_home() / "wizard_state.json", default={}) or {}
    return {"config": config.model_dump(mode="json"), "saved": payload}


@router.post("/system/wizard-state")
def save_wizard_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Remember answers between wizard steps so a closed window does not lose progress."""
    from ...core.atomic import atomic_write_json

    atomic_write_json(paths.studio_home() / "wizard_state.json", payload or {})
    return {"ok": True}


__all__ = ["router"]
