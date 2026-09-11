"""Filesystem locations used by PulseG Studio.

Two roots matter:

* ``~/.pulsegstudio`` - global, shared across every project (vault, agents.yaml,
  theme.json, config.yaml, projects.json, index.db, logs).
* ``<projects_root>/<slug>`` - one isolated folder per game project, itself a git repo.

Everything that touches the disk should resolve paths through this module so that
tests can redirect the whole tree with the ``PULSEG_HOME`` / ``PULSEG_PROJECTS_ROOT``
environment variables instead of monkeypatching ``Path.home``.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- Environment overrides (used by tests, CI and portable installs) -----------------

HOME_ENV = "PULSEG_HOME"
PROJECTS_ENV = "PULSEG_PROJECTS_ROOT"


def studio_home() -> Path:
    """Global config/secret root. ``~/.pulsegstudio`` unless overridden."""
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".pulsegstudio"


def _default_projects_root() -> Path:
    """Default place to keep game projects.

    Windows users get ``%USERPROFILE%/PulseGStudioProjects``; other platforms get
    ``~/PulseGStudioProjects``. On first run the Setup Wizard asks the user to
    confirm or change this, and the answer is persisted in ``config.yaml``.
    """
    return Path.home() / "PulseGStudioProjects"


def projects_root(config_value: str | None = None) -> Path:
    override = os.environ.get(PROJECTS_ENV)
    if override:
        return Path(override).expanduser().resolve()
    if config_value:
        return Path(config_value).expanduser().resolve()
    return _default_projects_root()


# --- Global files --------------------------------------------------------------------

def config_file() -> Path:
    return studio_home() / "config.yaml"


def agents_file() -> Path:
    return studio_home() / "agents.yaml"


def providers_file() -> Path:
    return studio_home() / "providers.yaml"


def theme_file() -> Path:
    return studio_home() / "theme.json"


def vault_file() -> Path:
    """Fernet-encrypted secret store (JSON inside)."""
    return studio_home() / "keys.enc"


def vault_key_file() -> Path:
    """Machine-bound vault key. On Windows this file is DPAPI-encrypted."""
    return studio_home() / ".vaultkey"


def projects_index_file() -> Path:
    return studio_home() / "projects.json"


def sqlite_file() -> Path:
    return studio_home() / "index.db"


def logs_dir() -> Path:
    return studio_home() / "logs"


def studio_log_file() -> Path:
    return logs_dir() / "studio.log"


# --- Per-project files ----------------------------------------------------------------

PROJECT_SUBDIRS = (
    "memory",
    "memory/context_snapshots",
    "knowledge",
    "story",
    "assets",
    "assets/sprites",
    "assets/tiles",
    "assets/ui",
    "assets/backgrounds",
    "assets/audio",
    "assets/audio/bgm",
    "assets/audio/sfx",
    "assets/references",
    "reports",
    "screenshots",
    "godot_project",
    "web_build",
    "planning",
)

MEMORY_FILES = {
    "progress": "memory/progress.md",
    "gdd": "memory/gdd.md",
    "task_queue": "memory/task_queue.json",
    "completed_tasks": "memory/completed_tasks.json",
    "agent_states": "memory/agent_states.json",
    "audit_history": "memory/audit_history.json",
    "planning_chat": "planning/chat.json",
    "planning_draft": "planning/gdd_draft.md",
    "asset_requests": "assets/asset_requests.json",
    "knowledge_index": "knowledge/index.json",
    "context_snapshot": "memory/context_snapshots",
}


def ensure_studio_home() -> Path:
    """Create the global tree if missing and lock it down. Returns the root."""
    root = studio_home()
    for path in (root, logs_dir()):
        path.mkdir(parents=True, exist_ok=True)
    restrict_permissions(root)
    return root


def ensure_project_tree(project_path: Path) -> Path:
    """Create every folder a project needs. Idempotent."""
    project_path.mkdir(parents=True, exist_ok=True)
    for sub in PROJECT_SUBDIRS:
        (project_path / sub).mkdir(parents=True, exist_ok=True)
    return project_path


def restrict_permissions(path: Path) -> None:
    """Best-effort 0700 on POSIX. No-op on Windows (ACLs are inherited instead)."""
    if os.name == "nt":
        return
    try:
        path.chmod(0o700)
    except OSError:  # pragma: no cover - unusual filesystems
        pass


def log_home_overview() -> str:
    """Human-readable summary used by the diagnostics endpoint and bug reports."""
    home = studio_home()
    root = projects_root()
    lines = [
        f"studio_home     : {home} ({'exists' if home.exists() else 'missing'})",
        f"projects_root   : {root} ({'exists' if root.exists() else 'missing'})",
        f"vault           : {'present' if vault_file().exists() else 'not created'}",
        f"agents.yaml     : {'present' if agents_file().exists() else 'not created'}",
        f"config.yaml     : {'present' if config_file().exists() else 'not created'}",
    ]
    return "\n".join(lines)
