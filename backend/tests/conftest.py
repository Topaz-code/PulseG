"""Shared fixtures. Every test runs against a temporary studio home.

Two reasons this matters: the real ``~/.pulsegstudio`` must never be touched by tests, and a
test that writes global config would make the next test order-dependent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def isolated_studio(tmp_path, monkeypatch):
    """Point the studio at a temporary home and projects root for every test."""
    home = tmp_path / "studio-home"
    projects = tmp_path / "projects"
    home.mkdir(parents=True, exist_ok=True)
    projects.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PULSEG_HOME", str(home))
    monkeypatch.setenv("PULSEG_PROJECTS_ROOT", str(projects))
    monkeypatch.setenv("PULSEG_DEMO", "1")
    # Modules cache paths; dropping the caches is cheaper than re-importing the world.
    for name in list(sys.modules):
        if name.startswith("backend.providers.router"):
            continue
    from backend.core import config as config_module
    from backend.core import paths as paths_module

    config_module.load_config.cache_clear() if hasattr(config_module.load_config, "cache_clear") else None
    yield {"home": paths_module.studio_home(), "projects": paths_module.projects_root()}


@pytest.fixture
def studio_home(isolated_studio) -> Path:
    return isolated_studio["home"]


@pytest.fixture
def projects_root(isolated_studio) -> Path:
    return isolated_studio["projects"]


@pytest.fixture
def project(projects_root) -> dict:
    """A created fresh-mode project with a Godot skeleton on disk."""
    from backend.orchestration import projects

    record = projects.create_project(name="Test Game", mode="fresh", genre="platformer")
    return record


@pytest.fixture
def bus(project):
    from backend.orchestration import projects

    return projects.bus_for(project)
