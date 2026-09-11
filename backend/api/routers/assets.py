"""Assets: the library, ingestion, asset requests and regeneration.

Agents check the library before generating anything, so this router has to be the honest record
of what exists. Two behaviours worth knowing about:

* **Ingest copies, never moves.** A user's file is left where they put it.
* **Regenerate creates a task.** It is not a "rerun the model" button: the new attempt goes
  through the same instruction, the same Auditor and the same human gate as the first one,
  because an asset that skipped the gate would be the one exception in the whole pipeline.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ...core.events import bus
from ...mcp.filesystem_mcp import write_project_file
from ...orchestration import memory, projects
from ...runtime import runtime
from ..deps import guarded, require_project
from ..schemas import AssetRequestCreate, FulfilAssetRequest, IngestAssetsRequest, RegenerateRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assets", tags=["assets"])

#: Suffixes a browser can preview directly.
AUDIO_SUFFIXES = {".ogg", ".wav", ".mp3", ".m4a", ".flac"}


@router.get("")
@guarded("list assets")
def list_assets(kind: str = Query(default=""), query: str = Query(default="")) -> dict[str, Any]:
    context = require_project()
    library = projects.asset_library(context.record)
    if kind:
        library = [entry for entry in library if entry.get("kind") == kind]
    if query:
        needle = query.lower()
        library = [entry for entry in library if needle in str(entry.get("path", "")).lower()]
    categories: dict[str, int] = {}
    for entry in library:
        categories[str(entry.get("kind", "other"))] = categories.get(str(entry.get("kind", "other")), 0) + 1
    return {
        "assets": [
            {**entry, "url": f"/media/{entry.get('path')}", "audio": Path(str(entry.get("path", ""))).suffix.lower() in AUDIO_SUFFIXES}
            for entry in library
        ],
        "categories": categories,
        "total_bytes": sum(int(entry.get("size_bytes", 0) or 0) for entry in library),
        "requests": projects.asset_requests(context.record),
    }


@router.post("/ingest")
@guarded("ingest assets")
def ingest(payload: IngestAssetsRequest) -> dict[str, Any]:
    context = require_project()
    result = projects.ingest_assets(context.record, payload.source_dir, kind=payload.kind)
    runtime.wake()
    return result


@router.get("/requests")
@guarded("list asset requests")
def requests() -> dict[str, Any]:
    context = require_project()
    rows = projects.asset_requests(context.record)
    return {"requests": rows, "open": sum(1 for row in rows if row.get("status") == "open")}


@router.post("/requests")
@guarded("create asset request")
def create_request(payload: AssetRequestCreate) -> dict[str, Any]:
    """Raise a visible, actionable asset request card from the UI (or the command bar)."""
    context = require_project()
    row = projects.create_asset_request(
        context.record,
        name=payload.name,
        kind=payload.kind,
        description=payload.description,
        requested_by="human",
        needed_by_task=payload.needed_by_task,
    )
    return {"request": row}


@router.post("/requests/{request_id}/fulfil")
@guarded("fulfil asset request")
def fulfil(request_id: str, payload: FulfilAssetRequest) -> dict[str, Any]:
    context = require_project()
    ok = memory.fulfil_asset_request(context.path, request_id, path=payload.path, by=payload.by)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_request", "message": f"No open asset request with id {request_id}."},
        )
    runtime.wake()
    return {"ok": True, "request_id": request_id, "path": payload.path}


@router.post("/regenerate")
@guarded("request regeneration")
def regenerate(payload: RegenerateRequest) -> dict[str, Any]:
    """Ask an agent for another attempt at an asset, without bypassing the gate."""
    context = require_project()
    target = context.path / payload.asset_path
    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error": "asset_missing",
                "message": f"{payload.asset_path} is not in this project, so there is nothing to regenerate.",
            },
        )
    agent = payload.agent
    instruction = (
        f"Regenerate the asset at `{payload.asset_path}`.\n\n"
        f"Reason from the human: {payload.reason or 'not given'}\n\n"
        "Keep the project's style lock. Write the replacement to the same path and state in "
        "your result what changed and why it is better. If the honest answer is that the asset "
        "is fine and the problem is elsewhere, say so instead of producing a variation."
    )
    task = context.bus.create(
        assigned_to=agent,
        created_by="human_direct",
        phase=int(context.bus.phase_progress().get("current_phase", 1) or 1),
        kind="art" if agent == "image_generator" else "audio",
        title=f"Regenerate {Path(payload.asset_path).name}",
        instruction=instruction,
        context_files=[payload.asset_path],
        expected_outputs=[payload.asset_path],
        file_claims=[payload.asset_path],
        priority=30,
    )
    memory.append_progress(
        context.path,
        f"{task.task_id} regeneration requested for {payload.asset_path}",
        agent="human",
        task_id=task.task_id,
        status="CREATED",
    )
    runtime.wake()
    return {
        "task": task.model_dump(mode="json"),
        "note": "The new asset goes through the Auditor and your review like everything else.",
    }


@router.post("/request-recuration")
@guarded("request recurations")
def recurate(payload: RegenerateRequest) -> dict[str, Any]:
    """Audio-specific: re-curate rather than regenerate (trim, loop, normalise again)."""
    context = require_project()
    task = context.bus.create(
        assigned_to="audio_curator",
        created_by="human_direct",
        phase=int(context.bus.phase_progress().get("current_phase", 1) or 1),
        kind="audio",
        title=f"Re-curate {Path(payload.asset_path).name}",
        instruction=(
            f"Re-curate `{payload.asset_path}`. Reason: {payload.reason or 'not given'}.\n\n"
            "Check loudness against -14 LUFS, loop point if it is background music, and the file "
            "format. Replace it in place and report exactly what you changed."
        ),
        context_files=[payload.asset_path],
        expected_outputs=[payload.asset_path],
        file_claims=[payload.asset_path],
        priority=30,
    )
    runtime.wake()
    return {"task": task.model_dump(mode="json")}


@router.get("/style-lock")
@guarded("read style lock")
def style_lock() -> dict[str, Any]:
    """The single style string every generated asset is prefixed with."""
    context = require_project()
    from ...agents.image_generator import style_lock as build_style_lock
    from ...agents.base import AgentContext
    from ...agents.registry import registry

    agent = registry.get("image_generator")
    if agent is None:
        return {"style": "", "source": "no image_generator agent configured"}
    ctx = AgentContext(agent=agent, project_path=context.path, project_id=context.project_id)
    style = build_style_lock(ctx)
    path = context.path / "assets" / "STYLE.md"
    return {
        "style": style,
        "source": "assets/STYLE.md" if path.exists() else "derived from the GDD summary",
        "note": "Edit assets/STYLE.md to change it. Every generation prompt is prefixed with it.",
    }


@router.put("/style-lock")
@guarded("update style lock")
def update_style_lock(payload: dict[str, str]) -> dict[str, Any]:
    context = require_project()
    style = str(payload.get("style", "")).strip()
    if not style:
        raise HTTPException(
            status_code=422,
            detail={"error": "empty_style", "message": "A style lock cannot be empty."},
        )
    write_project_file(context.path, "assets/STYLE.md", style + "\n")
    bus.publish("style_lock_updated", {"project_id": context.project_id, "style": style[:400]})
    return {"style": style}
