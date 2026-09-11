"""The file-based task bus (spec C.1, C.2, C.3).

Every state change goes through :meth:`TaskBus.transition`, which validates the move against
``ALLOWED_TRANSITIONS`` and writes the file before returning. That single choke point is what
makes the guarantees in the specification enforceable rather than aspirational:

* a task cannot be committed without a human decision (``approve`` is the only path to
  ``APPROVED``, and ``approve`` requires a ``HumanDecision``);
* a task that fails every provider is *paused* (``NEEDS_INTERVENTION``) with its full
  attempt history, never deleted;
* two tasks cannot write the same file at once - ``claim`` refuses a task whose file claims
  overlap an in-flight task.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..core.atomic import atomic_write_json, reentrant_lock
from ..core.events import bus
from ..core.index import index
from ..core.models import (
    ALLOWED_TRANSITIONS,
    KANBAN_LANES,
    AgentState,
    HumanDecision,
    Status,
    Task,
    Verdict,
)
from . import memory

log = logging.getLogger(__name__)


class TransitionError(ValueError):
    """Raised when a state change is not allowed. Never silently ignored."""


class TaskBus:
    """Task operations for one project."""

    def __init__(self, project_path: Path, project_id: str = "") -> None:
        self.path = project_path
        self.project_id = project_id or project_path.name

    # --- reads ------------------------------------------------------------------

    def all(self) -> list[Task]:
        return memory.all_tasks(self.path)

    def open_tasks(self) -> list[Task]:
        return memory.read_queue(self.path)

    def completed(self) -> list[Task]:
        return memory.read_completed(self.path)

    def get(self, task_id: str) -> Task | None:
        for task in self.all():
            if task.task_id == task_id:
                return task
        return None

    def by_status(self, *statuses: Status) -> list[Task]:
        wanted = set(statuses)
        return [task for task in self.all() if task.status in wanted]

    def board(self) -> list[dict[str, Any]]:
        """Kanban columns, in display order, with their tasks."""
        tasks = self.all()
        lanes: list[dict[str, Any]] = []
        for lane in KANBAN_LANES:
            statuses = set(lane["statuses"])
            items = [task for task in tasks if task.status in statuses]
            items.sort(key=lambda task: (task.priority, task.created_at))
            lanes.append({**lane, "count": len(items), "tasks": [t.model_dump(mode="json") for t in items]})
        return lanes

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.all():
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        return counts

    # --- writes -----------------------------------------------------------------

    def next_id(self) -> str:
        return f"TASK_{memory.next_task_number(self.path):03d}"

    def create(
        self,
        *,
        assigned_to: str,
        instruction: str,
        title: str = "",
        created_by: str = "prompter",
        phase: int = 1,
        kind: str = "implementation",
        dependencies: Sequence[str] = (),
        context_files: Sequence[str] = (),
        expected_outputs: Sequence[str] = (),
        file_claims: Sequence[str] = (),
        priority: int = 100,
    ) -> Task:
        with self._lock():
            return self._create_locked(
                assigned_to=assigned_to,
                instruction=instruction,
                title=title,
                created_by=created_by,
                phase=phase,
                kind=kind,
                dependencies=dependencies,
                context_files=context_files,
                expected_outputs=expected_outputs,
                file_claims=file_claims,
                priority=priority,
            )

    def _create_locked(
        self,
        *,
        assigned_to: str,
        instruction: str,
        title: str = "",
        created_by: str = "prompter",
        phase: int = 1,
        kind: str = "implementation",
        dependencies: Sequence[str] = (),
        context_files: Sequence[str] = (),
        expected_outputs: Sequence[str] = (),
        file_claims: Sequence[str] = (),
        priority: int = 100,
    ) -> Task:
        task = Task(
            task_id=self.next_id(),
            project_id=self.project_id,
            phase=phase,
            created_by=created_by,
            assigned_to=assigned_to,
            instruction=instruction,
            title=title or instruction[:80],
            kind=kind,
            dependencies=list(dependencies),
            context_files=list(context_files),
            expected_outputs=list(expected_outputs),
            file_claims=list(file_claims),
            priority=priority,
        )
        tasks = self.open_tasks()
        tasks.append(task)
        memory.write_queue(self.path, tasks)
        self._index(task)
        self._emit("task_created", task, {"title": task.title})
        return task

    def transition(self, task_id: str, new_status: Status, *, reason: str = "", **fields: Any) -> Task:
        """Validate and apply a state change, writing the file before returning.

        The whole read-validate-write cycle runs under the project's queue lock. Without it
        two agents finishing at the same moment each read the queue, each replace their own
        entry, and one of the two writes is lost - which shows up much later as "Cannot move
        TASK_00x from PENDING to SUBMITTED", because the lost write put the task back to
        PENDING on disk while the other thread thought it was IN_PROGRESS.
        """
        with self._lock():
            return self._transition_locked(task_id, new_status, reason=reason, **fields)

    def _transition_locked(
        self, task_id: str, new_status: Status, *, reason: str = "", **fields: Any
    ) -> Task:
        task = self.get(task_id)
        if task is None:
            raise TransitionError(f"Unknown task {task_id}")
        if new_status not in ALLOWED_TRANSITIONS[task.status] and new_status != task.status:
            raise TransitionError(
                f"Cannot move {task_id} from {task.status.value} to {new_status.value}. "
                f"Allowed: {', '.join(sorted(status.value for status in ALLOWED_TRANSITIONS[task.status]))}"
            )
        previous = task.status
        task.status = new_status
        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)
        if reason:
            task.errors.append(f"[{_utcnow()}] {reason}")
            task.errors = task.errors[-50:]
        task.touch()
        self._persist(task)
        self._emit(
            "task_status",
            task,
            {"from": previous.value, "to": new_status.value, "reason": reason},
        )
        return task

    def claim(self, task_id: str, agent_id: str, *, force: bool = False) -> Task:
        """Move a task to IN_PROGRESS, enforcing the file-conflict rule (spec C.2)."""
        with self._lock():
            return self._claim_locked(task_id, agent_id, force=force)

    def _claim_locked(self, task_id: str, agent_id: str, *, force: bool = False) -> Task:
        task = self.get(task_id)
        if task is None:
            raise TransitionError(f"Unknown task {task_id}")
        if task.assigned_to != agent_id and not force:
            raise TransitionError(
                f"{task_id} is assigned to {task.assigned_to}, not {agent_id}."
            )
        if task.status is Status.PENDING:
            busy = [
                other.task_id
                for other in self.by_status(Status.IN_PROGRESS)
                if other.assigned_to == agent_id and other.task_id != task_id
            ]
            if busy:
                raise TransitionError(
                    f"{task_id} cannot start while {agent_id} is still working on "
                    f"{busy[0]}. One task per agent at a time."
                )
        conflicts = self.file_conflicts(task, ignore=[task_id])
        if conflicts:
            detail = "; ".join(f"{other.task_id} holds {path}" for other, path in conflicts[:3])
            raise TransitionError(
                f"Cannot start {task_id}: its files are already being written by another "
                f"task ({detail}). It will be dispatched once they finish."
            )
        return self.transition(task_id, Status.IN_PROGRESS, started_at=_utcnow())

    def submit(
        self,
        task_id: str,
        *,
        output: str,
        artifacts: Sequence[str] = (),
        **fields: Any,
    ) -> Task:
        """Move a task to SUBMITTED with its output and the fields the caller wants recorded.

        Extra fields (provider used, model used, agent notes) go through the same transition,
        because ``transition()`` persists the whole task it re-reads - anything set on a local
        copy beforehand would be lost.
        """
        task = self.transition(
            task_id,
            Status.SUBMITTED,
            output=output,
            artifacts=list(artifacts),
            submitted_at=_utcnow(),
            **fields,
        )
        return task

    def start_audit(self, task_id: str) -> Task:
        return self.transition(task_id, Status.AUDITING, audited_at=_utcnow())

    def record_verdict(self, task_id: str, verdict: Verdict) -> Task:
        """Attach the Auditor's verdict and move the task to the human's queue.

        Note what this does *not* do: it never approves. Every task goes to
        ``NEEDS_HUMAN_REVIEW`` whatever the Auditor said (spec C.3).
        """
        task = self.get(task_id)
        if task is None:
            raise TransitionError(f"Unknown task {task_id}")
        declined = verdict.is_declined
        memory.append_audit(self.path, verdict, agent=task.assigned_to, task_id=task_id)
        # transition() re-reads the task from disk, so every mutation must be passed through
        # as a field rather than set on the local object.
        task = self.transition(
            task_id,
            Status.NEEDS_HUMAN_REVIEW,
            audited_at=_utcnow(),
            auditor_verdict=verdict,
            audit_rounds=max(task.audit_rounds, verdict.round),
            decline_count=task.decline_count + (1 if declined else 0),
        )
        top_flag = next((flag for flags in verdict.skill_flags.values() for flag in flags), "")
        self._emit(
            "verdict",
            task,
            {
                "verdict": verdict.verdict,
                "score": verdict.score,
                "flag": top_flag,
                "fix_note": verdict.fix_note,
            },
        )
        return task

    def approve(self, task_id: str, *, note: str = "", override: bool = False) -> Task:
        """The only path to APPROVED. Requires a human decision, by construction."""
        task = self.get(task_id)
        if task is None:
            raise TransitionError(f"Unknown task {task_id}")
        decision = HumanDecision.OVERRIDDEN if override else HumanDecision.APPROVED
        decided_at = _utcnow()
        memory.append_progress(
            self.path,
            f"approve | {task.title[:80]} | auditor "
            f"{task.auditor_verdict.verdict if task.auditor_verdict else 'n/a'}"
            + (" (human override)" if override else ""),
            agent=task.assigned_to,
            task_id=task.task_id,
            status="APPROVED",
        )
        task = self.transition(
            task_id,
            Status.APPROVED,
            decided_at=decided_at,
            human_decision=decision,
            human_note=note,
        )
        self._index(task)
        # Move from the open queue to history so the open queue stays small and readable.
        queue = [item for item in self.open_tasks() if item.task_id != task_id]
        memory.write_queue(self.path, queue)
        memory.append_completed(self.path, task)
        self._emit("task_approved", task, {"override": override, "note": note})
        return task

    def reject(self, task_id: str, *, note: str, auditor_note: str = "") -> Task:
        """Reject and requeue with the human's note *combined* with the Auditor's (spec C.3)."""
        task = self.get(task_id)
        if task is None:
            raise TransitionError(f"Unknown task {task_id}")
        combined = []
        if task.auditor_verdict and task.auditor_verdict.fix_note and auditor_note:
            combined.append(f"Auditor: {auditor_note}")
        elif task.auditor_verdict and task.auditor_verdict.fix_note:
            combined.append(f"Auditor: {task.auditor_verdict.fix_note}")
        if note:
            combined.append(f"Human: {note}")
        decided_at = _utcnow()
        revised_instruction = (
            task.instruction + "\n\nREVISION NOTES\n" + "\n".join(f"- {item}" for item in combined)
        ).strip()
        memory.append_progress(
            self.path,
            f"reject | {task.title[:80]} | {note[:120] or 'no note'}",
            agent=task.assigned_to,
            task_id=task.task_id,
            status="REJECTED",
        )
        task = self.transition(
            task_id,
            Status.REJECTED,
            decided_at=decided_at,
            human_decision=HumanDecision.REJECTED,
            human_note=note,
            instruction=revised_instruction,
            # Kept separate from decline_count: an Auditor decline rotates the model, a human
            # rejection is feedback. Losing that distinction would make rule B.2.5 misfire.
            human_rejections=task.human_rejections + 1,
        )
        self._emit("task_rejected", task, {"note": note, "requeued": True})
        # Requeue immediately: a rejection is feedback, not an ending.
        task = self.transition(task_id, Status.PENDING, reason="requeued after human rejection")
        return task

    def pause(self, task_id: str, reason: str) -> Task:
        """All fallbacks exhausted: pause with history intact. Never a failure state."""
        task = self.get(task_id)
        if task is None:
            raise TransitionError(f"Unknown task {task_id}")
        needs_key = "key" in reason.lower() or "not configured" in reason.lower()
        task = self.transition(task_id, Status.NEEDS_INTERVENTION, reason=reason)
        memory.append_progress(
            self.path,
            f"paused | {reason[:160]}",
            agent=task.assigned_to,
            task_id=task.task_id,
            status="NEEDS_INTERVENTION",
        )
        self._emit(
            "task_paused",
            task,
            {"reason": reason, "needs_key": needs_key, "retry_count": task.retry_count},
        )
        return task

    def resume(self, task_id: str, *, reason: str = "human retry") -> Task:
        task = self.transition(task_id, Status.PENDING, reason=reason)
        self._emit("task_resumed", task, {"reason": reason})
        return task

    def resume_all_paused(self, *, reason: str) -> list[str]:
        """Called when the human adds a key or edits a fallback chain (spec B.2.4)."""
        resumed: list[str] = []
        for task in self.by_status(Status.NEEDS_INTERVENTION):
            self.transition(task.task_id, Status.PENDING, reason=reason)
            self._emit("task_resumed", task, {"reason": reason})
            resumed.append(task.task_id)
        return resumed

    def fail_attempt(self, task_id: str, reason: str) -> Task:
        """Record that an attempt failed without pausing the task yet."""
        task = self.get(task_id)
        if task is None:
            raise TransitionError(f"Unknown task {task_id}")
        task.retry_count += 1
        task.errors.append(f"[{_utcnow()}] {reason}")
        task.errors = task.errors[-50:]
        task.touch()
        self._persist(task)
        self._emit("task_attempt_failed", task, {"reason": reason, "retry_count": task.retry_count})
        return task

    def add_screenshot(self, task_id: str, relative_path: str) -> Task:
        task = self.get(task_id)
        if task is None:
            raise TransitionError(f"Unknown task {task_id}")
        if relative_path not in task.screenshots:
            task.screenshots.append(relative_path)
        task.touch()
        self._persist(task)
        self._emit("screenshot_added", task, {"path": relative_path})
        return task

    # --- dependency logic (spec C.2) ---------------------------------------------

    def approved_ids(self) -> set[str]:
        return {task.task_id for task in self.completed() if task.status is Status.APPROVED}

    def is_unblocked(self, task: Task, approved: set[str] | None = None) -> bool:
        approved = approved if approved is not None else self.approved_ids()
        return all(dependency in approved for dependency in task.dependencies)

    def unblocked_pending(self) -> list[Task]:
        """PENDING tasks whose dependencies are all APPROVED, in dispatch order."""
        approved = self.approved_ids()
        candidates = [
            task
            for task in self.by_status(Status.PENDING)
            if self.is_unblocked(task, approved)
        ]
        candidates.sort(key=lambda task: (task.priority, task.phase, task.created_at))
        return candidates

    def blocked_pending(self) -> list[Task]:
        approved = self.approved_ids()
        return [task for task in self.by_status(Status.PENDING) if not self.is_unblocked(task, approved)]

    def dependents_of(self, task_id: str) -> list[Task]:
        return [
            task
            for task in self.open_tasks()
            if task_id in task.dependencies and task.status is Status.PENDING
        ]

    def file_conflicts(self, task: Task, *, ignore: Sequence[str] = ()) -> list[tuple[Task, str]]:
        """In-flight tasks that claim the same files. Prevents conflicting writes."""
        ignore_set = set(ignore)
        claimed = set(task.file_claims)
        if not claimed:
            return []
        conflicts: list[tuple[Task, str]] = []
        for other in self.by_status(Status.IN_PROGRESS):
            if other.task_id in ignore_set or other.task_id == task.task_id:
                continue
            for path in set(other.file_claims) & claimed:
                conflicts.append((other, path))
        return conflicts

    def phase_progress(self) -> dict[str, Any]:
        tasks = self.all()
        if not tasks:
            return {"phase": 0, "phases_total": 6, "approved": 0, "total": 0, "percent": 0}
        current = max((task.phase for task in tasks if task.status is not Status.APPROVED), default=max(t.phase for t in tasks))
        in_phase = [task for task in tasks if task.phase == current]
        approved = len([task for task in in_phase if task.status is Status.APPROVED])
        return {
            "phase": current,
            "phases_total": max(6, max(task.phase for task in tasks)),
            "approved": approved,
            "total": len(in_phase),
            "percent": int(round(approved / len(in_phase) * 100)) if in_phase else 0,
        }

    # --- internals ---------------------------------------------------------------

    def _persist(self, task: Task) -> None:
        """Write the task back to the file that owns it.

        Callers normally hold the queue lock already (``reentrant_lock`` makes the nested
        acquire free), but taking it here too means any future caller that writes a task
        directly still cannot lose a concurrent update.
        """
        with self._lock():
            self._persist_locked(task)

    def _lock(self) -> Any:
        return reentrant_lock(memory.queue_path(self.path))

    def _persist_locked(self, task: Task) -> None:
        queue = self.open_tasks()
        found = False
        for position, existing in enumerate(queue):
            if existing.task_id == task.task_id:
                queue[position] = task
                found = True
                break
        if found:
            memory.write_queue(self.path, queue)
        else:
            completed = self.completed()
            for position, existing in enumerate(completed):
                if existing.task_id == task.task_id:
                    completed[position] = task
                    atomic_write_json(
                        memory.completed_path(self.path),
                        [item.model_dump(mode="json") for item in completed],
                    )
                    break
        self._index(task)

    def _index(self, task: Task) -> None:
        try:
            index.upsert_task(task)
        except Exception as exc:  # pragma: no cover - the cache must never break the pipeline
            log.debug("Index update failed for %s: %s", task.task_id, exc)

    def _emit(self, event_type: str, task: Task, extra: dict[str, Any] | None = None) -> None:
        bus.publish(
            event_type,
            {
                "task_id": task.task_id,
                "project_id": self.project_id,
                "status": task.status.value,
                "agent": task.assigned_to,
                "title": task.title,
                **(extra or {}),
            },
        )

    def set_agent_state(self, agent_id: str, **changes: Any) -> AgentState:
        state = memory.update_agent_state(self.path, agent_id, **changes)
        try:
            index.upsert_agent_state(self.project_id, agent_id, state.model_dump())
        except Exception:  # pragma: no cover
            pass
        bus.publish("agent_state", {"agent": agent_id, "project_id": self.project_id, **state.model_dump(mode="json")})
        return state

    def snapshot(self) -> dict[str, Any]:
        """Everything the board view needs in one payload."""
        return {
            "project_id": self.project_id,
            "counts": self.counts(),
            "phase": self.phase_progress(),
            "lanes": self.board(),
            "agents": {k: v.model_dump(mode="json") for k, v in memory.read_agent_states(self.path).items()},
        }


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
