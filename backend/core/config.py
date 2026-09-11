"""Global configuration: ``config.yaml``, ``agents.yaml``, ``providers.yaml``, ``projects.json``.

Everything here is user-editable from the Settings screen. Defaults are seeded on first
run and never overwritten afterwards (a user's edits always win). Adding an agent or a
provider is a config change only - no code changes - which is PROCESS.md rule 6.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from . import paths
from .atomic import atomic_write_text, file_lock, read_json, read_text

log = logging.getLogger(__name__)

CONFIG_VERSION = 1


# --- config.yaml ----------------------------------------------------------------------


class GodotSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    executable: str = ""
    version: str = ""              # detected user_version, e.g. "4.3.stable"
    verify_on_launch: bool = True
    prefer_headless: bool = True   # headless for automated cycles, windowed for live play
    extra_args: list[str] = Field(default_factory=list)


class GitSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    user_name: str = ""
    user_email: str = ""
    auto_init: bool = True
    branch_per_phase: bool = True
    commit_on_approval_only: bool = True  # enforced in code; kept here for transparency


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    windows_toasts: bool = True
    telegram_enabled: bool = False
    telegram_bot_token_set: bool = False
    telegram_chat_id: str = ""
    notify_on: list[str] = Field(
        default_factory=lambda: [
            "needs_review",
            "needs_intervention",
            "pipeline_stalled",
            "phase_complete",
        ]
    )
    quiet_hours: str = ""          # "22:00-07:00" or empty


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_parallel_agents: int = 3
    dispatch_interval_s: float = 2.0
    max_retries_per_slot: int = 2
    request_timeout_s: int = 180
    keep_raw_scrapes_days: int = 14
    demo_mode: bool = False
    telemetry: bool = False        # never phones home; present so users can see it is off


class UISettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    theme: str = "dark"
    show_agent_graph: bool = True
    sidebar_collapsed: bool = False
    activity_feed_limit: int = 250


class StudioConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int = CONFIG_VERSION
    first_run_complete: bool = False
    setup_wizard_completed_at: str = ""
    projects_root: str = ""
    active_project_id: str = ""
    godot: GodotSettings = Field(default_factory=GodotSettings)
    git: GitSettings = Field(default_factory=GitSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    ui: UISettings = Field(default_factory=UISettings)
    created_at: str = Field(default_factory=lambda: _now())
    updated_at: str = Field(default_factory=lambda: _now())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- providers.yaml -------------------------------------------------------------------


class ProviderOverride(BaseModel):
    """Per-provider user override merged over :mod:`backend.providers.specs` defaults."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    base_url: str = ""
    default_model: str = ""
    daily_request_limit: int = 0      # 0 = use spec default
    monthly_request_limit: int = 0
    notes: str = ""


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int = CONFIG_VERSION
    providers: dict[str, ProviderOverride] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=lambda: _now())


# --- file access ----------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    text = read_text(path)
    if not text.strip():
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        log.error("Could not parse %s: %s. Using defaults; file left untouched.", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _dump_yaml(path: Path, payload: dict[str, Any], header: str) -> None:
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)
    atomic_write_text(path, f"{header}\n{body}")


def load_config() -> StudioConfig:
    """Read config.yaml, creating it with defaults when absent."""
    path = paths.config_file()
    if not path.exists():
        config = StudioConfig(projects_root=str(paths.projects_root()))
        save_config(config)
        return config
    data = _load_yaml(path)
    try:
        return StudioConfig.model_validate(data)
    except Exception as exc:  # pragma: no cover - corrupted user file
        log.error("config.yaml invalid (%s); falling back to defaults", exc)
        return StudioConfig(projects_root=str(paths.projects_root()))


def save_config(config: StudioConfig) -> StudioConfig:
    config.updated_at = _now()
    paths.ensure_studio_home()
    with file_lock(paths.config_file()):
        _dump_yaml(
            paths.config_file(),
            config.model_dump(mode="json"),
            "# PulseG Studio global configuration. Edited by the app; safe to hand-edit.\n"
            "# Secrets are NOT stored here - see keys.enc (encrypted vault).",
        )
    return config


def update_config(**changes: Any) -> StudioConfig:
    """Patch top-level fields and persist. Nested objects are merged, not replaced."""
    config = load_config()
    for key, value in changes.items():
        if value is None:
            continue
        current = getattr(config, key, None)
        if isinstance(current, BaseModel) and isinstance(value, dict):
            merged = current.model_dump()
            merged.update(value)
            setattr(config, key, type(current).model_validate(merged))
        else:
            setattr(config, key, value)
    return save_config(config)


# --- agents.yaml ----------------------------------------------------------------------


def load_agents_raw() -> list[dict[str, Any]]:
    """Raw roster from agents.yaml (seeded from the code defaults on first run).

    The file is the user's; this function never rewrites it except to seed or repair it.
    """
    path = paths.agents_file()
    if not path.exists():
        from ..agents.roster import default_agents_payload

        payload = default_agents_payload()
        save_agents_raw(payload)
        return payload["agents"]
    data = _load_yaml(path)
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        from ..agents.roster import default_agents_payload

        log.warning("agents.yaml had no usable 'agents' list; re-seeding defaults")
        payload = default_agents_payload()
        save_agents_raw(payload)
        return payload["agents"]
    return agents


def save_agents_raw(agents: list[dict[str, Any]]) -> None:
    paths.ensure_studio_home()
    payload = {"version": CONFIG_VERSION, "updated_at": _now(), "agents": agents}
    with file_lock(paths.agents_file()):
        _dump_yaml(
            paths.agents_file(),
            payload,
            "# PulseG Studio agent roster. Every field is editable from Settings > Agents.\n"
            "# Adding a 13th agent needs no code change: copy a block, change id/primary/fallbacks.\n"
            "# Provider ids must exist in providers.yaml / backend/providers/specs.py.",
        )


def load_providers() -> ProvidersConfig:
    path = paths.providers_file()
    if not path.exists():
        config = ProvidersConfig()
        save_providers(config)
        return config
    data = _load_yaml(path)
    try:
        return ProvidersConfig.model_validate(data)
    except Exception as exc:  # pragma: no cover
        log.error("providers.yaml invalid (%s); using defaults", exc)
        return ProvidersConfig()


def save_providers(config: ProvidersConfig) -> ProvidersConfig:
    config.updated_at = _now()
    paths.ensure_studio_home()
    with file_lock(paths.providers_file()):
        _dump_yaml(
            paths.providers_file(),
            config.model_dump(mode="json"),
            "# Per-provider overrides. Anything omitted falls back to the verified defaults\n"
            "# in backend/providers/specs.py (base URLs, free-tier limits, docs links).",
        )
    return config


# --- projects.json --------------------------------------------------------------------


def load_projects_index() -> dict[str, Any]:
    data = read_json(paths.projects_index_file(), default=None)
    if not isinstance(data, dict):
        data = {"version": CONFIG_VERSION, "projects": [], "active_project_id": ""}
        save_projects_index(data)
    data.setdefault("projects", [])
    return data


def save_projects_index(payload: dict[str, Any]) -> dict[str, Any]:
    paths.ensure_studio_home()
    payload["updated_at"] = _now()
    from .atomic import atomic_write_json

    with file_lock(paths.projects_index_file()):
        atomic_write_json(paths.projects_index_file(), payload)
    return payload


def new_id(prefix: str, length: int = 8) -> str:
    return f"{prefix}_{secrets.token_hex(length // 2)}"


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
