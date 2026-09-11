"""The dispatch loop: Prompter decides, this executes, the Auditor gates, the human approves.

One tick does all of this, in order:

1. **Recover.** Anything sitting in ``NEEDS_INTERVENTION`` is retried if the reason it paused
   has gone away - a key was added, the chain was edited, or the human pressed retry.
2. **Plan.** The Prompter reads the state and proposes dispatches and new tasks. If the
   Prompter's model is unreachable, the deterministic plan dispatches every unblocked task in
   priority order, so a key outage slows the studio down rather than stopping it.
3. **Dispatch.** Unblocked PENDING tasks go to idle agents, in parallel, respecting three
   rules: one in-flight task per agent, no two in-flight tasks touching the same file, and the
   task's dependencies must be APPROVED.
4. **Work.** The agent runs, its file blocks land on disk, Godot validates them, and the task
   moves to SUBMITTED.
5. **Audit.** Every submission goes through the Auditor. A decline requeues the task with the
   fix note; a pass or a decline alike then moves to ``NEEDS_HUMAN_REVIEW``. The human gate is
   never skipped, and approval is the only thing that commits.
6. **Notify.** Review requests, stalls, exhausted chains and completed phases fire Telegram and
   Windows notifications.

The loop is re-entrant: it can be called per tick from the API's background task or once from a
test, and every state change goes through the task bus, so a crash mid-tick loses at most the
work in flight - which is recovered on the next start from the snapshot files.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..agents import loader, prompter as prompter_agent
from ..agents.base import AgentContext, AgentRunResult
from ..agents.registry import registry
from ..core.config import load_config
from ..core.models import Status, Task
from ..mcp.godot_mcp_client import GodotMCPClient
from ..providers.router import FallbackRouter
from . import auditor as auditor_module
from . import memory
from .task_bus import TaskBus, TransitionError

log = logging.getLogger(__name__)

MAX_PARALLEL_TASKS = 6
GODOT_VALIDATE_KINDS = {"implementation"}


@dataclass
class DispatchOutcome:
    """What one tick did. The API returns this to the UI's run-state indicator."""

    dispatched: list[str] = field(default_factory=list)
    submitted: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)
    paused: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reasoning: str = ""
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "dispatched": self.dispatched,
            "submitted": self.submitted,
            "needs_review": self.needs_review,
            "paused": self.paused,
            "resumed": self.resumed,
            "created": self.created,
            "skipped": self.skipped,
            "errors": self.errors,
            "reasoning": self.reasoning,
            "duration_ms": self.duration_ms,
        }


class Dispatcher:
    """Runs the studio for one project."""

    def __init__(
        self,
        project_path: Path,
        *,
        project_id: str = "",
        bus: TaskBus | None = None,
        router: FallbackRouter | None = None,
        max_parallel: int = MAX_PARALLEL_TASKS,
        godot_executable: str = "",
    ) -> None:
        self.path = Path(project_path)
        self.bus = bus or TaskBus(self.path, project_id)
        self.router = router or FallbackRouter()
        self.max_parallel = max(1, max_parallel)
        self.godot_executable = godot_executable or self._configured_godot()
        self._lock = threading.Lock()
        self.running = False
        self.last_outcome = DispatchOutcome()

    # --- helpers ------------------------------------------------------------------

    def _configured_godot(self) -> str:
        try:
            return load_config().godot_path or ""
        except Exception:  # pragma: no cover - config is optional for tests
            return ""

    def _agent_running(self, agent_id: str) -> bool:
        return any(
            task.assigned_to == agent_id and task.status is Status.IN_PROGRESS for task in self.bus.all()
        )

    def _context(self, task: Task) -> AgentContext:
        agent = registry.get(task.assigned_to)
        if agent is None:
            raise KeyError(f"Task {task.task_id} is assigned to unknown agent '{task.assigned_to}'.")
        return AgentContext(
            agent=agent,
            project_path=self.path,
            project_id=self.bus.project_id or str(task.project_id),
            task=task,
            router=self.router,
            godot_executable=self.godot_executable,
            extra={"task_bus": self.bus},
        )

    # --- the tick -----------------------------------------------------------------

    def tick(self, *, create_tasks: bool = True, use_model_prompter: bool = True) -> DispatchOutcome:
        if not self._lock.acquire(blocking=False):
            return DispatchOutcome(errors=["A dispatch tick is already running."])
        started = time.perf_counter()
        outcome = DispatchOutcome()
        try:
            outcome.resumed = self.recover_interventions()
            outcome.created, outcome.reasoning = self.plan(create_tasks=create_tasks, use_model=use_model_prompter)
            self.dispatch_ready(outcome)
        except Exception as exc:  # pragma: no cover - the loop must survive anything
            log.exception("Dispatch tick failed")
            outcome.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            self._lock.release()
        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        self.last_outcome = outcome
        return outcome

    # --- 1. recovery ---------------------------------------------------------------

    def recover_interventions(self) -> list[str]:
        """Retry paused tasks whose blocker has gone away.

        A paused task is never deleted. It resumes when a provider in its chain is now usable
        (a key was added), when the chain itself changed, or when the human presses retry -
        which the API does by calling :meth:`retry_task`.
        """
        resumed: list[str] = []
        # configured_providers() already counts key-free providers (demo, yt-dlp, Kenney) as
        # available, so this is exactly the set of chains that can run right now.
        available = registry.configured_providers()
        for task in self.bus.by_status(Status.NEEDS_INTERVENTION):
            agent = registry.get(task.assigned_to)
            if agent is None:
                continue
            usable = [ref.provider for _, ref in agent.chain() if ref.provider in available]
            if not usable:
                continue
            try:
                self.bus.resume(task.task_id, reason=f"provider became available: {usable[0]}")
                resumed.append(task.task_id)
                memory.append_progress(
                    self.path,
                    f"{task.task_id} resumed automatically: {usable[0]} is available again",
                    agent="dispatcher",
                    task_id=task.task_id,
                    status="RESUMED",
                )
            except TransitionError as exc:  # pragma: no cover - defensive
                log.info("Could not resume %s: %s", task.task_id, exc)
        return resumed

    def retry_task(self, task_id: str, *, reason: str = "human retry") -> Task:
        """Explicit human retry from the Needs Intervention card."""
        task = self.bus.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task {task_id}.")
        if task.status is Status.NEEDS_INTERVENTION:
            return self.bus.resume(task_id, reason=reason)
        if task.status is Status.REJECTED:
            return self.bus.transition(task_id, Status.PENDING, reason=reason)
        return task

    # --- 2. planning ----------------------------------------------------------------

    def plan(self, *, create_tasks: bool = True, use_model: bool = True) -> tuple[list[str], str]:
        agent = registry.get("prompter")
        if agent is None:
            return [], "No Prompter agent configured."
        context = AgentContext(
            agent=agent,
            project_path=self.path,
            project_id=self.bus.project_id,
            router=self.router,
            extra={"phases": memory.project_stats(self.path).get("phases", [])},
        )
        if use_model:
            run = prompter_agent.plan(context, self.bus)
            payload = run.payload or {}
        else:
            payload = prompter_agent.fallback_plan(self._state_brief())
            run = AgentRunResult(ok=True, agent_id="prompter", payload=payload)

        created: list[str] = []
        if create_tasks:
            for spec in payload.get("create_tasks") or []:
                if not isinstance(spec, dict) or not spec.get("instruction"):
                    continue
                assigned = str(spec.get("assigned_to") or registry.agent_for_task_kind(str(spec.get("kind", ""))))
                if registry.get(assigned) is None:
                    continue
                task = self.bus.create(
                    assigned_to=assigned,
                    instruction=str(spec["instruction"])[:4000],
                    title=str(spec.get("title") or "")[:120],
                    created_by="prompter",
                    phase=int(spec.get("phase") or self.bus.phase_progress().get("current_phase", 1) or 1),
                    kind=str(spec.get("kind") or "implementation"),
                    dependencies=[str(item) for item in (spec.get("dependencies") or [])],
                    context_files=[str(item) for item in (spec.get("context_files") or [])][:12],
                    expected_outputs=[str(item) for item in (spec.get("expected_outputs") or [])][:12],
                    file_claims=[str(item) for item in (spec.get("file_claims") or [])][:12],
                    priority=int(spec.get("priority") or 100),
                )
                created.append(task.task_id)
        reasoning = str(payload.get("reasoning") or "")[:600]
        if run.problems:
            reasoning = (reasoning + " " + run.problems[0][:200]).strip()
        return created, reasoning

    def _state_brief(self) -> dict[str, Any]:
        return {
            "unblocked": [
                {"task_id": task.task_id, "agent": task.assigned_to, "title": task.title}
                for task in self.bus.unblocked_pending()
            ],
            "counts": self.bus.counts(),
            "phase": self.bus.phase_progress(),
        }

    # --- 3/4/5. dispatch and run ------------------------------------------------------

    def dispatch_ready(self, outcome: DispatchOutcome | None = None) -> DispatchOutcome:
        outcome = outcome or DispatchOutcome()
        candidates = self._runnable()
        for task, reason in candidates:
            outcome.skipped.append({"task_id": task.task_id, "reason": reason})
        ready = [task for task, reason in candidates if reason == ""]
        slots = max(0, self.max_parallel - len(self.bus.by_status(Status.IN_PROGRESS)))
        selected = ready[:slots]
        for task in selected:
            outcome.dispatched.append(task.task_id)

        if not selected:
            return outcome

        with ThreadPoolExecutor(max_workers=len(selected), thread_name_prefix="pulseg-agent") as pool:
            futures = {pool.submit(self.run_task, task.task_id): task.task_id for task in selected}
            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    outcome.errors.append(f"{task_id}: {type(exc).__name__}: {exc}")
                    continue
                if result.get("submitted"):
                    outcome.submitted.append(task_id)
                if result.get("needs_review"):
                    outcome.needs_review.append(task_id)
                if result.get("paused"):
                    outcome.paused.append(task_id)
                if result.get("error"):
                    outcome.errors.append(f"{task_id}: {result['error']}")
        self._check_stall(outcome)
        return outcome

    def _runnable(self) -> list[tuple[Task, str]]:
        """Unblocked tasks paired with the reason they cannot run (empty string means go).

        The reserved-agents set matters more than it looks: at the start of a pass no task is
        IN_PROGRESS yet, so a naive check would hand the same agent two tasks in the same
        pass. A human's task and a Prompter's task for the same agent then raced, and one of
        the two lost its state update. One agent, one task, always.
        """
        rows: list[tuple[Task, str]] = []
        reserved: set[str] = set()
        for task in sorted(self.bus.unblocked_pending(), key=lambda item: (item.phase, item.priority, item.task_id)):
            if self._agent_running(task.assigned_to):
                rows.append((task, f"{task.assigned_to} already has a task in flight"))
                continue
            if task.assigned_to in reserved:
                rows.append((task, f"{task.assigned_to} already has a task in flight"))
                continue
            conflicts = self.bus.file_conflicts(task)
            if conflicts:
                other, path = conflicts[0]
                rows.append((task, f"file lock: {path} is being written by {other.task_id}"))
                continue
            reserved.add(task.assigned_to)
            rows.append((task, ""))
        return rows

    def run_task(self, task_id: str, *, max_retries: int = 2) -> dict[str, Any]:
        """Claim, run, submit, audit and gate one task. Returns a summary dict."""
        task = self.bus.get(task_id)
        if task is None:
            return {"error": f"unknown task {task_id}"}
        if self.bus.file_conflicts(task):
            other, path = self.bus.file_conflicts(task)[0]
            return {"error": f"file lock: {path} held by {other.task_id}"}

        try:
            task = self.bus.claim(task_id, task.assigned_to)
        except TransitionError as exc:
            return {"error": str(exc)}

        run: AgentRunResult | None = None
        for attempt in range(1, max_retries + 2):
            context = self._context(task)
            context.extra["attempt"] = attempt
            run = loader.run_agent(context)
            if run.ok:
                break
            detail = run.error or "; ".join(run.problems) or "the agent produced nothing usable"
            # fail_attempt records the history without moving the task, so the retry stays on
            # the same task and the error trail is complete when the human looks at it.
            self.bus.fail_attempt(task_id, detail)
            if run.paused:
                paused = self._pause_task(task_id, run.pause_reason or detail)
                return {
                    "paused": True,
                    "needs_intervention": paused.status is Status.NEEDS_INTERVENTION,
                    "error": run.pause_reason or detail,
                }
            if attempt <= max_retries:
                log.info("Retrying %s (attempt %s): %s", task_id, attempt + 1, detail[:160])
                continue
            paused = self._pause_task(task_id, f"{attempt} attempt(s) failed; last error: {detail}")
            return {
                "paused": True,
                "needs_intervention": True,
                "error": detail,
            }

        assert run is not None
        self._submit(task_id, run)
        # SUBMITTED -> AUDITING -> NEEDS_HUMAN_REVIEW is the documented flow, so the audit
        # window is visible in the Kanban rather than the task appearing to jump the gate.
        auditing = self.bus.start_audit(task_id)
        verdict = auditor_module.audit_task(self.path, auditing, router=self.router)
        reviewed = self.bus.record_verdict(task_id, verdict)
        # Trigger one of the four notifications: a person has to decide something.
        self._notify_review(reviewed)
        return {
            "submitted": True,
            "needs_review": True,
            "verdict": verdict.verdict,
            "score": verdict.score,
            "screenshots": len(reviewed.screenshots),
        }

    def _submit(self, task_id: str, run: AgentRunResult) -> Task:
        """Move the task to SUBMITTED, validating Godot files first when the task wrote any."""
        task = self.bus.get(task_id)
        assert task is not None
        godot_files = [item for item in run.artifacts if item.endswith((".gd", ".tscn"))]
        if godot_files and task.kind in GODOT_VALIDATE_KINDS:
            validation = self.validate_godot(godot_files)
            if not validation.get("ok"):
                # The submission still happens: the Auditor and the human must see the failure
                # rather than the dispatcher hiding it. The message rides along with the task.
                run.problems.append(f"Godot validation failed: {validation.get('message', '')[:300]}")
            run.notes.append(f"Godot validation: {validation.get('message', '')[:200]}")
        updated = self.bus.submit(
            task_id,
            output=run.output,
            artifacts=run.artifacts,
            provider_used=run.provider_used,
            model_used=run.model_used,
            agent_notes=(run.problems[:10] + run.notes[:10]),
        )
        for screenshot in run.screenshots:
            try:
                updated = self.bus.add_screenshot(task_id, screenshot)
            except TransitionError:  # pragma: no cover - screenshots are additive
                pass
        return updated

    def validate_godot(self, files: Sequence[str]) -> dict[str, Any]:
        client = GodotMCPClient(
            executable=self.godot_executable, project_path=self.path / "godot_project"
        )
        try:
            result = client.validate_scripts(list(files))
            return result.as_dict()
        finally:
            client.close()

    def _pause_task(self, task_id: str, reason: str) -> Task:
        reason = reason or "provider chain exhausted"
        paused = self.bus.pause(task_id, reason)
        memory.append_progress(
            self.path,
            f"{task_id} paused - needs intervention: {reason[:160]}",
            agent="dispatcher",
            task_id=task_id,
            status="PAUSED",
        )
        self._notify_intervention(paused, reason)
        return paused

    def _check_stall(self, outcome: DispatchOutcome) -> None:
        """A pipeline with nothing runnable and nothing in review is stalled, and says so."""
        counts = self.bus.counts()
        active = counts.get("IN_PROGRESS", 0) + counts.get("AUDITING", 0) + counts.get("SUBMITTED", 0)
        waiting = counts.get("NEEDS_HUMAN_REVIEW", 0)
        pending = counts.get("PENDING", 0)
        if pending and not active and not waiting:
            blocked = self.bus.blocked_pending()
            if blocked:
                detail = ", ".join(f"{task.task_id} waiting on {task.dependencies}" for task in blocked[:4])
                outcome.errors.append(f"Pipeline stalled: {detail}")
                self._notify_stalled(
                    f"{len(blocked)} task(s) are blocked with nothing in flight: {detail}"
                )

    # --- notifications (the four real triggers only) ----------------------------------

    def _notify_review(self, task: Task) -> None:
        try:
            from ..notifications import service

            service.notify_needs_review(task, project_path=self.path)
        except Exception as exc:  # pragma: no cover - best effort
            log.info("Review notification skipped: %s", exc)

    def _notify_intervention(self, task: Task, reason: str) -> None:
        try:
            from ..notifications import service

            service.notify_needs_intervention(task, reason, project_path=self.path)
        except Exception as exc:  # pragma: no cover - best effort
            log.info("Intervention notification skipped: %s", exc)

    def _notify_stalled(self, detail: str) -> None:
        try:
            from ..notifications import service

            service.notify_pipeline_stalled(detail, project_path=self.path)
        except Exception as exc:  # pragma: no cover - best effort
            log.info("Stall notification skipped: %s", exc)

    # --- planning handoff ------------------------------------------------------------------

    def seed_first_tasks(self, *, genre: str = "") -> list[str]:
        """Create the Phase 1 task set after the human confirms the design."""
        agent = registry.get("prompter")
        if agent is None:
            return []
        context = AgentContext(
            agent=agent, project_path=self.path, project_id=self.bus.project_id, router=self.router
        )
        created = prompter_agent.seed_first_tasks(context, self.bus, genre=genre)
        return [task.task_id for task in created]

    def wait_for_review_clear(self, *, timeout_s: float = 600.0, poll_s: float = 2.0) -> bool:
        """Run ticks until nothing is waiting on the human, or the timeout expires.

        Used by headless runs and tests. Interactive use never needs this: the dashboard shows
        the review queue and the human decides when to act.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if not self.bus.by_status(Status.NEEDS_HUMAN_REVIEW):
                return True
            self.tick()
            time.sleep(poll_s)
        return False
