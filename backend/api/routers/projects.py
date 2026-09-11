"""Projects: create, adopt, list, switch, inspect.

Project creation is the one place the studio writes outside its own folder tree, so it is also
the place with the most guardrails: an existing folder is never overwritten, the mode
(fresh vs existing) changes what gets scaffolded, and creating a project makes it active in the
same call so the UI cannot end up showing one project while writing to another.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ...core.events import bus
from ...core.index import index
from ...mcp.filesystem_mcp import list_project_files, read_project_file, write_project_file
from ...orchestration import memory, projects
from ...orchestration.git_ops import GitRepo
from ...orchestration.projects import PHASES
from ...runtime import runtime
from ..deps import board_payload, guarded, require_project
from ..schemas import CreateProjectRequest, UpdateProjectRequest, WriteProjectFileRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects() -> dict[str, Any]:
    records = projects.list_projects()
    active = projects.active_project()
    return {
        "projects": records,
        "active_project_id": (active or {}).get("project_id", ""),
        "phases": PHASES,
    }


@router.post("")
@guarded("create project")
def create_project(payload: CreateProjectRequest) -> dict[str, Any]:
    if payload.mode == "existing" and not payload.existing_path:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "existing_path_required",
                "message": "Pick the folder of the Godot project you want to open.",
            },
        )
    record = projects.create_project(
        name=payload.name,
        mode=payload.mode,
        existing_path=payload.existing_path,
        genre=payload.genre,
        art_style=payload.art_style,
        perspective=payload.perspective,
        godot_version=payload.godot_version,
        parent_dir=payload.parent_dir,
        git_identity=payload.git_identity,
    )
    path = projects.project_path_of(record)
    if payload.concept.strip():
        # Starting the intake immediately means the Planning rail opens with real questions
        # rather than an empty box, which is the difference between "what do I do now" and a
        # one-sentence paste.
        from ...agents import planning_agent

        planning_agent.begin(path, payload.concept, title=payload.name)
    runtime.set_active(str(record["project_id"]))
    bus.publish("project_created", {"project_id": record["project_id"], "name": record["name"]})
    return {"project": record, "overview": projects.project_overview(record)}


@router.get("/active")
@guarded("read active project")
def active_project() -> dict[str, Any]:
    context = require_project()
    return {
        "project": context.record,
        "overview": projects.project_overview(context.record),
        "board": board_payload(context),
        "run_state": runtime.as_dict(),
    }


@router.post("/{project_id}/activate")
@guarded("switch project")
def activate(project_id: str) -> dict[str, Any]:
    record = projects.set_active(project_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_project", "message": f"No project with id {project_id}."},
        )
    runtime.set_active(project_id)
    return {"project": record, "run_state": runtime.as_dict()}


@router.get("/{project_id}")
@guarded("read project")
def get_project(project_id: str) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_project", "message": f"No project with id {project_id}."},
        )
    return {"project": record, "overview": projects.project_overview(record)}


@router.patch("/{project_id}")
@guarded("update project")
def update_project(project_id: str, payload: UpdateProjectRequest) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    changes = payload.model_dump(exclude_none=True)
    record.update(changes)
    if changes:
        projects._update_record(record)
    return {"project": record}


@router.delete("/{project_id}")
@guarded("remove project")
def remove_project(project_id: str, delete_files: bool = Query(default=False)) -> dict[str, Any]:
    """Remove a project from the studio. The folder is kept unless delete_files is explicit."""
    result = projects.forget_project(project_id, delete_files=delete_files)
    if not result.get("removed"):
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    if (runtime.record or {}).get("project_id") == project_id:
        runtime.set_active("")
    return result


@router.get("/{project_id}/overview")
@guarded("read overview")
def overview(project_id: str) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    summary = projects.project_overview(record)
    task_bus = projects.bus_for(record)
    summary["review_queue"] = []
    if task_bus is not None:
        from ...orchestration.approvals import review_queue

        summary["review_queue"] = review_queue(task_bus)
        from ..deps import ProjectContext

        summary["board"] = board_payload(
            ProjectContext(record=record, path=projects.project_path_of(record), bus=task_bus)
        )
    summary["milestones"] = projects.read_milestones(record)
    summary["agent_states"] = memory.read_agent_states(projects.project_path_of(record))
    return summary


@router.get("/{project_id}/files")
@guarded("list project files")
def files(project_id: str, pattern: str = Query(default="**/*"), limit: int = Query(default=500, le=2000)) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    path = projects.project_path_of(record)
    return {"files": list_project_files(path, pattern=pattern, limit=limit)}


@router.get("/{project_id}/file")
@guarded("read project file")
def read_file(project_id: str, path: str = Query(min_length=1)) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    root = projects.project_path_of(record)
    try:
        content = read_project_file(root, path)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "file_not_readable",
                "message": f"{exc} Only files inside the project folder can be opened.",
            },
        ) from exc
    return {"path": path, "content": content, "bytes": len(content)}


@router.put("/{project_id}/file")
@guarded("edit a project document")
def write_file(project_id: str, payload: WriteProjectFileRequest) -> dict[str, Any]:
    """Save a document the human edited by hand, most often the design or a story file.

    Three refusals are on purpose. A path outside the project is rejected by the resolver, a file
    an in-flight task is working on is rejected here, and a commit is never made: the edit lands on
    disk immediately (files win) and enters the project history with the next approved task.
    """
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    path = projects.project_path_of(record)

    for task in projects.bus_for(record).open_tasks():
        if payload.path in task.file_claims and task.status.value in {"IN_PROGRESS", "SUBMITTED", "AUDITING"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "file_in_flight",
                    "message": (
                        f"{payload.path} is being written by {task.task_id} right now. "
                        "Decide that task first, then edit the file."
                    ),
                },
            )

    try:
        write_project_file(path, payload.path, payload.content)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "file_not_writable", "message": f"{exc}"},
        ) from exc

    memory.append_progress(
        path,
        f"human edited {payload.path} by hand",
        agent="human",
        status="EDITED",
    )
    bus.publish(
        "design_edited",
        {"project_id": project_id, "path": payload.path, "bytes": len(payload.content.encode("utf-8"))},
    )
    return {"path": payload.path, "bytes": len(payload.content.encode("utf-8")), "committed": False}


@router.get("/{project_id}/phases")
@guarded("read phases")
def phases(project_id: str) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    task_bus = projects.bus_for(record)
    from ...orchestration.approvals import phase_gate

    gates = [phase_gate(task_bus, int(item["id"])) for item in PHASES] if task_bus else []
    return {"phases": PHASES, "gates": gates, "current": task_bus.phase_progress() if task_bus else {}}


@router.get("/{project_id}/git")
@guarded("read git status")
def git_status(project_id: str) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    repo = GitRepo(projects.project_path_of(record))
    return {
        "initialised": repo.initialised,
        "status": repo.status() if repo.initialised else {},
        "identity": repo.identity(),
    }


@router.post("/{project_id}/git/init")
@guarded("initialise git")
def git_init(project_id: str) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    repo = GitRepo(projects.project_path_of(record))
    identity = repo.identity()
    ok = repo.init(user_name=identity.get("user_name", ""), user_email=identity.get("user_email", ""))
    repo.ensure_gitignore()
    return {"ok": ok, "status": repo.status()}


@router.get("/{project_id}/milestones")
@guarded("read milestones")
def milestones(project_id: str) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    return {"milestones": projects.read_milestones(record)}


@router.get("/{project_id}/search")
@guarded("search project")
def search(project_id: str, q: str = Query(min_length=1, max_length=200), limit: int = Query(default=25, le=100)) -> dict[str, Any]:
    """Search the SQLite cache. The files are still the truth; this is a shortcut."""
    return {
        "query": q,
        "results": index.search(q, limit=limit),
        "note": "Results come from the rebuildable index; open a file to see the real content.",
    }


@router.get("/{project_id}/activity")
@guarded("read activity")
def activity(project_id: str, limit: int = Query(default=80, le=500)) -> dict[str, Any]:
    """The live activity feed: recent indexable events, newest first."""
    return {"events": index.history(limit=limit)}


@router.get("/{project_id}/paths")
@guarded("read project paths")
def project_paths(project_id: str) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    root = projects.project_path_of(record)
    return {
        "root": str(root),
        "memory": str(root / "memory"),
        "knowledge": str(root / "knowledge"),
        "story": str(root / "story"),
        "assets": str(root / "assets"),
        "reports": str(root / "reports"),
        "screenshots": str(root / "screenshots"),
        "godot_project": str(root / "godot_project"),
        "web_build": str(projects.web_build_path(record)),
        "exists": {name: (root / name).exists() for name in
                   ("memory", "knowledge", "story", "assets", "reports", "screenshots", "godot_project", "web_build")},
    }


@router.get("/{project_id}/godot-status")
@guarded("read godot status")
def godot_status(project_id: str) -> dict[str, Any]:
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    from ...core.config import load_config
    from ...mcp.godot_mcp_client import GodotMCPClient

    config = load_config()
    root = projects.project_path_of(record)
    project_dir = root / "godot_project"
    client = GodotMCPClient(executable=config.godot.executable, project_path=project_dir)
    try:
        scripts = [str(path.relative_to(project_dir)) for path in sorted(project_dir.rglob("*.gd"))] if project_dir.exists() else []
        validation = client.validate_scripts(scripts) if scripts else None
        return {
            "configured": bool(config.godot.executable),
            "version": config.godot.version,
            "transport": client.transport,
            "project_exists": (project_dir / "project.godot").exists(),
            "script_count": len(scripts),
            "validation": validation.as_dict() if validation else None,
            "web_build": {
                "path": str(projects.web_build_path(record)),
                "exists": (projects.web_build_path(record) / "index.html").exists(),
            },
        }
    finally:
        client.close()


@router.get("/{project_id}/open-folder")
@guarded("open project folder")
def open_folder(project_id: str, sub: str = Query(default="")) -> dict[str, Any]:
    """Return the absolute path the desktop shell should open in Explorer or the editor.

    The sidecar does not launch Explorer itself: the shell does that, because a browser cannot
    and because an API that opens windows on the machine is a liability.
    """
    record = projects.get_project(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_project", "message": "Not found."})
    root = projects.project_path_of(record)
    target = root / sub if sub else root
    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail={"error": "folder_missing", "message": f"{target} does not exist yet."},
        )
    return {"path": str(target), "sub": sub}
