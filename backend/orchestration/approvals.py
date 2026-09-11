"""The human gate: nothing is approved, committed or unblocked without a person.

Every task reaches ``NEEDS_HUMAN_REVIEW`` regardless of the Auditor's verdict. From there the
human has exactly three moves, and this module is the only thing allowed to perform them:

* **Approve** - records the decision, commits the work to git, unblocks dependents.
* **Reject** - records the note, merges it with the Auditor's findings, requeues the task.
* **Override** - approves despite a DECLINED verdict, with the override recorded so the audit
  trail shows the human made that call rather than the pipeline hiding it.

The commit happens here and nowhere else, which is what makes "commit only on human-approved
tasks" a property of the code rather than a promise in a document.

Notifications fire only on the four triggers the specification names. Approving and rejecting
are the human's own actions, so they are logged to ``progress.md`` and not pushed back at the
person who just did them - except for a completed phase, which is worth knowing about.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..core.models import Status
from . import memory
from .git_ops import CommitResult, GitRepo
from .task_bus import TaskBus, TransitionError

log = logging.getLogger(__name__)


class ApprovalError(RuntimeError):
    pass


def review_queue(bus: TaskBus) -> list[dict[str, Any]]:
    """Everything waiting on the human, newest first, with the verdict attached."""
    rows: list[dict[str, Any]] = []
    for task in bus.by_status(Status.NEEDS_HUMAN_REVIEW):
        verdict = task.auditor_verdict
        rows.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "agent": task.assigned_to,
                "phase": task.phase,
                "verdict": verdict.verdict if verdict else "",
                "score": verdict.score if verdict else None,
                "summary": verdict.summary if verdict else "",
                "fix_note": verdict.fix_note if verdict else "",
                "flags": verdict.skill_flags if verdict else {},
                "screenshots": task.screenshots,
                "artifacts": task.artifacts,
                "provider": task.provider_used,
                "model": task.model_used,
                "decline_count": task.decline_count,
                "human_rejections": task.human_rejections,
                "override_needed": bool(verdict and verdict.is_declined),
            }
        )
    return rows


def pending_count(bus: TaskBus) -> int:
    return len(bus.by_status(Status.NEEDS_HUMAN_REVIEW))


def approve(
    bus: TaskBus,
    task_id: str,
    *,
    note: str = "",
    override: bool = False,
    git: GitRepo | None = None,
) -> dict[str, Any]:
    """Approve a task: decision recorded, work committed, dependents unblocked."""
    task = bus.get(task_id)
    if task is None:
        raise ApprovalError(f"Unknown task {task_id}.")
    if task.status is not Status.NEEDS_HUMAN_REVIEW:
        raise ApprovalError(
            f"{task_id} is {task.status.value}, not at the review gate. Only tasks the Auditor "
            "has finished with can be approved."
        )
    verdict = task.auditor_verdict
    if override and (verdict is None or not verdict.is_declined):
        raise ApprovalError(
            "Override is only for approving work the Auditor declined. This task has no decline "
            "to override."
        )
    if not override and verdict is not None and verdict.is_declined:
        raise ApprovalError(
            "The Auditor declined this task. Approve it as an override (the override is recorded) "
            "or reject it with a note."
        )

    approved = bus.approve(task_id, note=note, override=override)

    repo = git or GitRepo(bus.path)
    phase_labels = _phase_labels()
    commit: CommitResult = repo.commit_task(
        approved, phase_label=phase_labels.get(approved.phase, "")
    )
    if commit.ok and commit.sha:
        try:
            approved = bus.transition(
                task_id,
                Status.APPROVED,
                reason="commit recorded",
                committed=commit.sha,
                commit_branch=commit.branch,
            )
        except TransitionError:
            # Recording the sha is bookkeeping; the approval itself already happened.
            log.info("Could not attach commit %s to %s", commit.sha, task_id)

    unblocked = [item.task_id for item in bus.unblocked_pending()]
    memory.append_progress(
        bus.path,
        f"{task_id} approved by the human"
        + (" (override of the Auditor's decline)" if override else "")
        + (f" - committed {commit.sha[:8]}" if commit.ok and commit.sha else " - not committed"),
        agent="human",
        task_id=task_id,
        status="APPROVED",
    )
    result: dict[str, Any] = {
        "task": approved.model_dump(mode="json"),
        "commit": {
            "ok": commit.ok,
            "sha": commit.sha,
            "branch": commit.branch,
            "message": commit.message,
            "detail": commit.detail,
            "files": commit.files,
        },
        "unblocked": unblocked,
        "override": override,
    }
    milestone = _milestone_if_complete(bus, approved.phase)
    if milestone:
        result["phase_complete"] = milestone
    return result


def reject(bus: TaskBus, task_id: str, *, note: str, auditor_note: str = "") -> dict[str, Any]:
    """Reject and requeue, merging the human's note with the Auditor's findings."""
    task = bus.get(task_id)
    if task is None:
        raise ApprovalError(f"Unknown task {task_id}.")
    if not note.strip():
        raise ApprovalError(
            "A rejection needs a note. The agent cannot fix what you did not describe, and the "
            "note is what it reads on the next attempt."
        )
    if task.status is not Status.NEEDS_HUMAN_REVIEW:
        raise ApprovalError(f"{task_id} is {task.status.value}, not waiting for review.")

    rejected = bus.reject(task_id, note=note, auditor_note=auditor_note)
    memory.append_progress(
        bus.path,
        f"{task_id} rejected by the human; requeued for {rejected.assigned_to}",
        agent="human",
        task_id=task_id,
        status="REJECTED",
    )
    return {"task": rejected.model_dump(mode="json")}


def override_and_approve(bus: TaskBus, task_id: str, *, note: str, git: GitRepo | None = None) -> dict[str, Any]:
    """Approve against the Auditor's decline, with the override on the record."""
    if not note.strip():
        raise ApprovalError(
            "An override needs a note saying why. The Auditor declined this work; the record "
            "must show the reasoning behind going ahead anyway."
        )
    result = approve(bus, task_id, note=f"OVERRIDE: {note}", override=True, git=git)
    memory.append_progress(
        bus.path,
        f"{task_id} approved over the Auditor's decline: {note[:200]}",
        agent="human",
        task_id=task_id,
        status="OVERRIDE",
    )
    return result


def bulk_approve(bus: TaskBus, task_ids: list[str], *, note: str = "", git: GitRepo | None = None) -> dict[str, Any]:
    """Approve several tasks, reporting per-task failures instead of stopping."""
    results: dict[str, Any] = {}
    for task_id in task_ids:
        try:
            results[task_id] = approve(bus, task_id, note=note, git=git)
        except ApprovalError as exc:
            results[task_id] = {"error": str(exc)}
    return results


def phase_gate(bus: TaskBus, phase: int) -> dict[str, Any]:
    """Exit criteria for a phase: every task approved, nothing in flight, nothing paused."""
    tasks = [task for task in bus.all() if task.phase == phase]
    open_tasks = [task for task in tasks if task.status is not Status.APPROVED]
    blocked = [task for task in open_tasks if task.status is Status.NEEDS_HUMAN_REVIEW]
    paused = [task for task in open_tasks if task.status is Status.NEEDS_INTERVENTION]
    complete = not open_tasks and bool(tasks)
    return {
        "phase": phase,
        "label": _phase_labels().get(phase, ""),
        "complete": complete,
        "total": len(tasks),
        "approved": len(tasks) - len(open_tasks),
        "awaiting_review": [task.task_id for task in blocked],
        "needs_intervention": [task.task_id for task in paused],
        "remaining": [
            {"task_id": task.task_id, "status": task.status.value, "title": task.title}
            for task in open_tasks
        ][:20],
        "next_step": (
            "Phase complete. Approve the milestone to regenerate the web export."
            if complete
            else "Keep approving tasks until nothing is open in this phase."
        ),
    }


def _milestone_if_complete(bus: TaskBus, phase: int) -> dict[str, Any] | None:
    gate = phase_gate(bus, phase)
    if not gate["complete"] or gate["total"] == 0:
        return None
    try:
        from ..notifications import service as notification_service

        notification_service.notify_phase_complete(phase, gate["label"], project_path=bus.path)
    except Exception as exc:  # pragma: no cover - notifications are best effort
        log.info("Phase-complete notification skipped: %s", exc)
    try:
        from . import projects

        record = _record_for(bus.path)
        if record is not None and record.get("mode") == "fresh":
            mark = projects.mark_milestone(record, phase)
            gate["milestone"] = mark
    except Exception as exc:  # pragma: no cover - milestone marking is additive
        log.info("Milestone marking skipped: %s", exc)
    return gate


def _record_for(project_path: Path) -> dict[str, Any] | None:
    from . import projects

    for record in projects.list_projects():
        if Path(record.get("path", "")).resolve() == Path(project_path).resolve():
            return record
    return None


def commit_history(bus: TaskBus, limit: int = 50) -> list[dict[str, str]]:
    repo = GitRepo(bus.path)
    if not repo.initialised:
        return []
    return repo.log(limit=limit)


def unreviewed_artifacts(bus: TaskBus) -> int:
    """Files touched by attempted work that no human has approved - the risk surface."""
    count = 0
    for task in bus.all():
        if task.status is Status.APPROVED:
            continue
        artifacts = [item for item in task.artifacts if not item.startswith("screenshots/")]
        count += len(artifacts)
    return count


def _phase_labels() -> dict[int, str]:
    from .projects import PHASES

    return {int(item["id"]): item["name"] for item in PHASES}
