"""Live Preview and the agent graph.

Two views of the same question - "what is happening right now?" - and a toggle between them:

* **Game** - an iframe of the latest approved Godot web export, served from
  ``/preview/{project}/index.html``.
* **Graph** - the agent roster as a directed graph: who is working, who is idle, and which
  handoff edge is carrying a task right now.

The graph is computed from the task queue rather than kept as separate state. A graph that can
disagree with the queue is a lie with edges.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from ...agents.registry import registry
from ...core.events import bus
from ...core.models import Status
from ...orchestration import memory, projects
from ...runtime import runtime
from ..deps import guarded, require_project
from ..schemas import PreviewToggleRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/preview", tags=["preview"])

#: How the roster hands work to each other. Read this as "who is waiting on whom", not as a
#: strict pipeline: several of these run in parallel by design.
HANDOFF_EDGES: list[tuple[str, str, str]] = [
    ("planning_agent", "prompter", "signed-off design"),
    ("prompter", "programmer", "implementation task"),
    ("prompter", "researcher", "research task"),
    ("prompter", "story_writer", "narrative task"),
    ("prompter", "image_generator", "art task"),
    ("prompter", "audio_curator", "audio task"),
    ("researcher", "documenter", "findings"),
    ("transcriptor", "documenter", "transcript"),
    ("documenter", "programmer", "design detail"),
    ("programmer", "tester", "build to test"),
    ("tester", "auditor", "evidence"),
    ("story_writer", "auditor", "narrative"),
    ("image_generator", "auditor", "assets"),
    ("audio_curator", "auditor", "audio"),
    ("auditor", "planning_agent", "verdict"),
]


@router.get("/graph")
@guarded("read agent graph")
def graph() -> dict[str, Any]:
    """Nodes and edges for the React Flow view, with live activity on both."""
    context = require_project()
    tasks = context.bus.all()
    states = memory.read_agent_states(context.path)

    in_flight: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        if task.status in (Status.IN_PROGRESS, Status.SUBMITTED, Status.AUDITING, Status.NEEDS_HUMAN_REVIEW):
            in_flight.setdefault(task.assigned_to, []).append(task)

    nodes: list[dict[str, Any]] = []
    for agent in registry.agents.values():
        active = in_flight.get(agent.id, [])
        state = states.get(agent.id)
        status = "idle"
        if active:
            status = {
                Status.IN_PROGRESS: "working",
                Status.SUBMITTED: "handing_off",
                Status.AUDITING: "auditing",
                Status.NEEDS_HUMAN_REVIEW: "waiting_on_human",
            }.get(active[0].status, "working")
        elif state and state.status in ("blocked", "error"):
            status = state.status
        nodes.append(
            {
                "id": agent.id,
                "label": agent.name,
                "role": agent.role,
                "colour": agent.color,
                "status": status,
                "enabled": agent.enabled,
                "primary": f"{agent.primary.provider}/{agent.primary.model}",
                "active_task": active[0].task_id if active else "",
                "active_title": active[0].title if active else "",
                "provider": state.active_provider if state else "",
                "tasks_completed": state.tasks_completed if state else 0,
                "avg_latency_ms": state.avg_latency_ms if state else 0,
            }
        )

    edges: list[dict[str, Any]] = []
    for source, target, label in HANDOFF_EDGES:
        carrying = _carrying_tasks(tasks, source, target)
        edges.append(
            {
                "id": f"{source}->{target}",
                "source": source,
                "target": target,
                "label": label,
                "animated": bool(carrying),
                "carrying": carrying,
            }
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "active_count": sum(1 for node in nodes if node["status"] == "working"),
        "review_count": len(context.bus.by_status(Status.NEEDS_HUMAN_REVIEW)),
        "run_state": runtime.as_dict(),
        "updated_at": memory.read_agent_states(context.path) and _now(),
    }


@router.get("/state")
@guarded("read preview state")
def preview_state() -> dict[str, Any]:
    context = require_project()
    build = projects.web_build_path(context.record)
    milestones = projects.read_milestones(context.record)
    index_file = build / "index.html"
    return {
        "project_id": context.project_id,
        "mode": "game" if index_file.exists() else "graph",
        "game": {
            "available": index_file.exists(),
            "url": f"/preview/{context.project_id}/index.html",
            "path": str(build),
            "bytes": sum(path.stat().st_size for path in build.rglob("*") if path.is_file()) if build.exists() else 0,
        },
        "milestones": milestones,
        "last_milestone": milestones[-1] if milestones else None,
        "explanation": _explanation(index_file.exists(), milestones),
    }


@router.post("/mode")
@guarded("switch preview mode")
def set_mode(payload: PreviewToggleRequest) -> dict[str, Any]:
    """Remember the toggle so a reload keeps the view the user chose."""
    from ...core.config import update_config

    update_config(ui={"show_agent_graph": payload.mode == "graph"})
    bus.publish("preview_mode", {"mode": payload.mode})
    return {"mode": payload.mode}


@router.post("/export")
@guarded("export web build")
def export_web(force: bool = Query(default=False)) -> dict[str, Any]:
    """Regenerate the Godot web export.

    Normally this only happens after an approved phase milestone. ``force`` exists for the case
    where the user wants to look at the game right now and accepts that it may be mid-phase.
    """
    context = require_project()
    from ...core.config import load_config
    from ...mcp.godot_mcp_client import GodotMCPClient

    config = load_config()
    if not config.godot.executable:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "godot_missing",
                "message": (
                    "No Godot executable is configured, so the game cannot be exported. Set the "
                    "path in Settings > Godot."
                ),
                "action": "open_settings",
            },
        )
    if not force:
        gate = projects.read_milestones(context.record)
        if not gate:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "no_milestone",
                    "message": (
                        "The web export is regenerated after a phase is approved. Nothing has "
                        "been approved yet, so the preview would show an empty project."
                    ),
                    "action": "force_export",
                },
            )
    client = GodotMCPClient(
        executable=config.godot.executable,
        project_path=context.path / "godot_project",
    )
    try:
        result = client.export_web(projects.web_build_path(context.record))
    finally:
        client.close()
    payload = {
        "ok": result.ok,
        "message": result.message,
        "transport": result.transport,
        "url": f"/preview/{context.project_id}/index.html",
        "forced": force,
    }
    bus.publish("web_export", {"project_id": context.project_id, **payload})
    return payload


@router.get("/godot-window")
@guarded("read window state")
def godot_window() -> dict[str, Any]:
    """Whether a Godot window is on screen, which is what decides the screenshot mode."""
    from ...mcp import screenshot_mcp

    return {
        "modes": screenshot_mcp.describe_modes(),
        "note": "The Tester picks the mode automatically; this is shown so the choice is never a mystery.",
    }


def _carrying_tasks(tasks, source: str, target: str) -> list[str]:
    """Which task ids are currently on this handoff edge."""
    carrying: list[str] = []
    for task in tasks:
        if task.assigned_to == target and task.created_by == source:
            if task.status in (Status.PENDING, Status.IN_PROGRESS, Status.SUBMITTED, Status.AUDITING):
                carrying.append(task.task_id)
    return carrying


def _explanation(available: bool, milestones: list[dict[str, Any]]) -> str:
    if available:
        last = milestones[-1] if milestones else None
        if last:
            return (
                f"Showing the export from phase {last.get('phase')} "
                f"({last.get('label', '')}), approved {str(last.get('at', ''))[:10]}."
            )
        return "Showing the web export in web_build/."
    return (
        "The game preview appears after a phase is approved and the web export is regenerated. "
        "Until then this pane shows the agent graph, which is the more useful view anyway."
    )


@router.get("/media/{path:path}", include_in_schema=False)
def media(path: str) -> Any:
    """Serve a project file (screenshots, generated art) to the UI, within the project folder."""
    context = require_project()
    from ...mcp.filesystem_mcp import resolve_in_project

    try:
        target = resolve_in_project(context.path, path)
    except PermissionError:
        return JSONResponse(status_code=403, content={"error": "outside_project", "message": "Refused."})
    if not target.exists() or not target.is_file():
        return JSONResponse(status_code=404, content={"error": "missing", "message": path})
    return FileResponse(target)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["router", "media"]
