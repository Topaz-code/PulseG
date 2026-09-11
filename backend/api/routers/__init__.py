"""API routers.

One module per screen group, so the route table reads like the sidebar: system, projects,
tasks, agents, providers, planning, assets, knowledge, logs, git, design, settings, preview.
The frontend's api client mirrors these paths one-to-one, which is why the naming is boring.
"""
from __future__ import annotations

from . import (
    agents,
    assets,
    design,
    git,
    knowledge,
    logs,
    planning,
    preview,
    projects as projects_router,
    providers,
    settings,
    system,
    tasks,
)

ALL_ROUTERS = [
    system.router,
    settings.router,
    projects_router.router,
    tasks.router,
    agents.router,
    providers.router,
    planning.router,
    assets.router,
    knowledge.router,
    logs.router,
    git.router,
    design.router,
    preview.router,
]

__all__ = ["ALL_ROUTERS"]
