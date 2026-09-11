"""Tasks: the board, the review gate, and the task drawer.

This is the router the whole product is judged on, because it is where the human does the only
thing the studio cannot do itself: decide. Every endpoint here is a thin wrapper over
``orchestration.approvals`` so the API cannot approve something the gate would refuse.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from ...core.events import bus
from ...core.models import KANBAN_LANES, HumanDecision, Status
from ...orchestration import approvals, memory
from ...orchestration.auditor import audit_report_markdown, audit_summary_for_human
from ...runtime import runtime
from ..deps import board_payload, guarded, require_project, require_task
from ..schemas import (
    ApproveRequest,
    BulkApproveRequest,
    CommentRequest,
    CreateTaskRequest,
    OverrideRequest,
    RejectRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
@guarded("list tasks")
def list_tasks(
    status: str = Query(default="", description="Comma-separated status filter."),
    agent: str = Query(default=""),
    phase: int = Query(default=-1),
    limit: int = Query(default=500, le=2000),
) -> dict[str, Any]:
    context = require_project()
    tasks = context.bus.all()
    if status:
        wanted = {item.strip().upper() for item in status.split(",") if item.strip()}
        tasks = [task for task in tasks if task.status.value in wanted]
    if agent:
        tasks = [task for task in tasks if task.assigned_to == agent]
    if phase >= 0:
        tasks = [task for task in tasks if task.phase == phase]
    return {"tasks": [task.model_dump(mode="json") for task in tasks[:limit]], "counts": context.bus.counts()}


@router.get("/board")
@guarded("read board")
def board() -> dict[str, Any]:
    return board_payload(require_project())


@router.get("/review")
@guarded("read review queue")
def review_queue() -> dict[str, Any]:
    context = require_project()
    rows = approvals.review_queue(context.bus)
    return {
        "items": rows,
        "count": len(rows),
        "unreviewed_artifacts": approvals.unreviewed_artifacts(context.bus),
    }


@router.post("")
@guarded("create task")
def create_task(payload: CreateTaskRequest) -> dict[str, Any]:
    context = require_project()
    from ...agents.registry import registry

    if registry.get(payload.assigned_to) is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail={
                "error": "unknown_agent",
                "message": (
                    f"There is no agent called '{payload.assigned_to}'. Known agents: "
                    + ", ".join(sorted(registry.ids()))
                ),
            },
        )
    task = context.bus.create(
        assigned_to=payload.assigned_to,
        instruction=payload.instruction,
        title=payload.title,
        created_by=payload.created_by,
        phase=payload.phase,
        kind=payload.kind,
        dependencies=payload.dependencies,
        context_files=payload.context_files,
        expected_outputs=payload.expected_outputs,
        file_claims=payload.file_claims,
        priority=payload.priority,
    )
    memory.append_progress(
        context.path,
        f"{task.task_id} added by hand for {task.assigned_to}",
        agent=payload.created_by,
        task_id=task.task_id,
        status="CREATED",
    )
    if payload.created_by == "human_direct":
        bus.publish("human_direct_task", {"task_id": task.task_id, "instruction": payload.instruction[:400]})
    runtime.wake()
    return {"task": task.model_dump(mode="json")}


@router.get("/statuses")
def statuses() -> dict[str, Any]:
    """The status vocabulary, so the UI never hardcodes a label."""
    return {
        "statuses": [
            {"value": status.value, "label": status.value.replace("_", " ").title()} for status in Status
        ],
        "human_decisions": [item.value for item in HumanDecision],
        "lanes": [
            {"id": lane["id"], "title": lane["title"], "statuses": [status.value for status in lane["statuses"]]}
            for lane in KANBAN_LANES
        ],
    }


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.get("/{task_id}")
@guarded("read task")
def get_task(task_id: str) -> dict[str, Any]:
    task = require_task(task_id)
    context = require_project()
    payload = task.model_dump(mode="json")
    payload["audit_summary"] = audit_summary_for_human(task)
    payload["agent_state"] = (
        memory.read_agent_states(context.path).get(task.assigned_to).model_dump(mode="json")
        if memory.read_agent_states(context.path).get(task.assigned_to)
        else {}
    )
    payload["dependents"] = [
        {"task_id": item.task_id, "title": item.title, "status": item.status.value}
        for item in context.bus.dependents_of(task.task_id)
    ]
    payload["blocked_by"] = _blocked_by(context, task)
    return payload


@router.get("/{task_id}/audit-report")
@guarded("read audit report")
def audit_report(task_id: str) -> dict[str, Any]:
    task = require_task(task_id)
    if task.auditor_verdict is None:
        return {"report": "", "verdict": None}
    return {
        "report": audit_report_markdown(task.auditor_verdict),
        "verdict": task.auditor_verdict.model_dump(mode="json"),
    }


@router.post("/{task_id}/approve")
@guarded("approve task")
def approve(task_id: str, payload: ApproveRequest) -> dict[str, Any]:
    context = require_project()
    result = approvals.approve(context.bus, task_id, note=payload.note, override=payload.override)
    runtime.wake()
    return result


@router.post("/{task_id}/reject")
@guarded("reject task")
def reject(task_id: str, payload: RejectRequest) -> dict[str, Any]:
    context = require_project()
    task = context.bus.get(task_id)
    auditor_note = ""
    if task is not None and task.auditor_verdict is not None and task.auditor_verdict.is_declined:
        auditor_note = task.auditor_verdict.fix_note
    result = approvals.reject(context.bus, task_id, note=payload.note, auditor_note=auditor_note)
    runtime.wake()
    return result


@router.post("/{task_id}/override")
@guarded("override auditor")
def override(task_id: str, payload: OverrideRequest) -> dict[str, Any]:
    context = require_project()
    result = approvals.override_and_approve(context.bus, task_id, note=payload.note)
    runtime.wake()
    return result


@router.post("/bulk-approve")
@guarded("approve several tasks")
def bulk_approve(payload: BulkApproveRequest) -> dict[str, Any]:
    context = require_project()
    results = approvals.bulk_approve(context.bus, payload.task_ids, note=payload.note)
    runtime.wake()
    approved = [key for key, value in results.items() if isinstance(value, dict) and "task" in value]
    return {"results": results, "approved": approved, "failed": len(results) - len(approved)}


@router.post("/{task_id}/retry")
@guarded("retry task")
def retry(task_id: str) -> dict[str, Any]:
    """Press Retry on a Needs Intervention card, or restart a task that failed."""
    task = require_task(task_id)
    dispatcher = runtime.dispatcher()
    if dispatcher is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail={"error": "no_active_project", "message": "No project open."})
    updated = dispatcher.retry_task(task_id, reason="human pressed retry")
    memory.append_progress(
        require_project().path,
        f"{task_id} retried by the human",
        agent="human",
        task_id=task_id,
        status="RETRY",
    )
    runtime.wake()
    return {"task": updated.model_dump(mode="json"), "was": task.status.value}


@router.post("/{task_id}/comment")
@guarded("comment on task")
def comment(task_id: str, payload: CommentRequest) -> dict[str, Any]:
    """Add a note to a task without deciding on it. It becomes context for the next attempt."""
    context = require_project()
    task = require_task(task_id)
    notes = list(getattr(task, "comments", []) or [])
    notes.append({"by": "human", "note": payload.note})
    updated = context.bus.transition(
        task_id,
        task.status,
        reason="comment added",
        comments=notes,
        human_note=(task.human_note + "\n" + payload.note).strip() if task.human_note else payload.note,
    )
    memory.append_chat(
        context.path,
        __import__("backend.core.models", fromlist=["ChatMessage"]).ChatMessage(
            message_id=f"msg_{task_id}_{len(notes)}",
            role="human",
            content=f"[{task_id}] {payload.note}",
            meta={"task_id": task_id, "kind": "task_comment"},
        ),
    )
    return {"task": updated.model_dump(mode="json")}


@router.get("/{task_id}/screenshots")
@guarded("list screenshots")
def screenshots(task_id: str) -> dict[str, Any]:
    task = require_task(task_id)
    context = require_project()
    rows = []
    for relative in task.screenshots:
        path = context.path / relative
        rows.append(
            {
                "path": relative,
                "exists": path.exists(),
                "bytes": path.stat().st_size if path.exists() else 0,
                "url": f"/media/{relative}",
            }
        )
    return {"screenshots": rows, "notes": _screenshot_notes(context, task)}


@router.get("/{task_id}/output")
@guarded("read task output")
def output(task_id: str, limit: int = Query(default=200_000, le=500_000)) -> dict[str, Any]:
    task = require_task(task_id)
    return {
        "task_id": task.task_id,
        "output": (task.output or "")[:limit],
        "artifacts": task.artifacts,
        "provider": task.provider_used,
        "model": task.model_used,
    }


@router.get("/{task_id}/attempts")
@guarded("read attempts")
def attempts(task_id: str) -> dict[str, Any]:
    task = require_task(task_id)
    from ...agents.task_processor import summarise_attempt_history

    return {
        "attempts": [item.model_dump(mode="json") for item in task.attempts],
        "errors": task.errors,
        "summary": summarise_attempt_history(task),
        "retry_count": task.retry_count,
    }


@router.get("/{task_id}/dependencies")
@guarded("read dependencies")
def dependencies(task_id: str) -> dict[str, Any]:
    task = require_task(task_id)
    context = require_project()
    return {
        "dependencies": _blocked_by(context, task),
        "dependents": [
            {"task_id": item.task_id, "title": item.title, "status": item.status.value}
            for item in context.bus.dependents_of(task.task_id)
        ],
        "unblocked": context.bus.is_unblocked(task),
    }


@router.get("/{task_id}/context")
@guarded("read context")
def context_preview(task_id: str) -> dict[str, Any]:
    """Exactly what this task's agent will be told, before it runs. No hidden context."""
    task = require_task(task_id)
    context = require_project()
    from ...orchestration import context_engine

    return context_engine.preview(context.path, task.assigned_to, task_id)


def _blocked_by(context, task) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dependency in task.dependencies:
        found = context.bus.get(dependency)
        if found is None:
            rows.append({"task_id": dependency, "status": "MISSING", "title": "dependency not found"})
            continue
        rows.append({"task_id": found.task_id, "status": found.status.value, "title": found.title})
    return rows


def _screenshot_notes(context, task) -> str:
    """Explain how the screenshots were taken, so a blurry one is not a mystery."""
    if not task.screenshots:
        return (
            "No screenshots for this task. The Tester attaches them, so a task that never "
            "reached the Tester has none."
        )
    from ...mcp import screenshot_mcp

    return (
        f"{len(task.screenshots)} screenshot(s). {screenshot_mcp.describe_modes()['auto']}"
    )
