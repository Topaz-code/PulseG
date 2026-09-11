"""Prompter agent: reads state and dispatches the next tasks (spec C.2).

Called on a dispatch tick. It receives the queue, the agent states and the GDD summary, and
returns either dispatches for existing tasks, brand new tasks, or a phase transition. The
deterministic fallback in :func:`fallback_plan` matters: if the model is unavailable the
pipeline still advances on the obvious next step rather than stalling on a missing key.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..core.models import Status, Task
from ..skills import adhd_filter
from .base import AgentContext, AgentRunResult, run_text_agent

log = logging.getLogger(__name__)

AGENT_ID = "prompter"
MAX_NEW_TASKS_PER_TICK = 4


def _state_brief(ctx: AgentContext, task_bus) -> dict[str, Any]:
    tasks = task_bus.all()
    open_tasks = [task for task in tasks if task.status is not Status.APPROVED]
    approved = [task for task in tasks if task.status is Status.APPROVED]
    return {
        "phase": task_bus.phase_progress(),
        "counts": task_bus.counts(),
        "unblocked": [
            {
                "task_id": task.task_id,
                "agent": task.assigned_to,
                "title": task.title,
                "kind": task.kind,
                "phase": task.phase,
            }
            for task in task_bus.unblocked_pending()
        ],
        "blocked": [
            {"task_id": task.task_id, "title": task.title, "waiting_on": task.dependencies}
            for task in task_bus.blocked_pending()[:10]
        ],
        "in_flight": [
            {"task_id": task.task_id, "agent": task.assigned_to, "files": task.file_claims}
            for task in tasks
            if task.status in (Status.IN_PROGRESS, Status.AUDITING, Status.SUBMITTED)
        ],
        "awaiting_human": [
            {"task_id": task.task_id, "title": task.title}
            for task in tasks
            if task.status is Status.NEEDS_HUMAN_REVIEW
        ],
        "paused": [
            {"task_id": task.task_id, "title": task.title, "attempts": task.retry_count}
            for task in tasks
            if task.status is Status.NEEDS_INTERVENTION
        ],
        "approved_recent": [
            {"task_id": task.task_id, "title": task.title, "phase": task.phase} for task in approved[-6:]
        ],
        "open_tasks": len(open_tasks),
        "phases": ctx.extra.get("phases", []),
        "agents": ctx.extra.get("agent_summary", {}),
    }


def plan(ctx: AgentContext, task_bus) -> AgentRunResult:
    """Ask the Prompter what to do next."""
    state = _state_brief(ctx, task_bus)
    ctx.extra["phases"] = ctx.extra.get("phases", [])
    instruction = (
        "Here is the current studio state. Decide the next actions.\n\n"
        "STUDIO_STATE\n" + json.dumps(state, indent=1)[:6000] + "\n"
    )
    run = run_text_agent(ctx, extra_instruction=instruction, require_json=True)
    if run.paused or not run.ok:
        # A paused Prompter must not stall the pipeline: fall back to the deterministic plan.
        plan_payload = fallback_plan(state)
        run.payload = plan_payload
        run.problems.append(
            "Prompter model unavailable; used the deterministic dispatch plan instead "
            "(unblocked tasks in priority order)."
        )
        run.ok = True
    if run.payload:
        run.payload.setdefault("dispatch", [])
        run.payload["create_tasks"] = (run.payload.get("create_tasks") or [])[:MAX_NEW_TASKS_PER_TICK]
        run.payload.setdefault("phase_complete", False)
    return run


def fallback_plan(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic dispatch: every unblocked task goes to its own agent queue.

    The dispatcher still applies concurrency and file-claim limits, so this is safe to run
    with nothing but the queue files on disk.
    """
    dispatch = [
        {
            "task_id": item["task_id"],
            "agent": item["agent"],
            "reason": "unblocked; dependencies approved",
        }
        for item in state.get("unblocked", [])
    ]
    return {
        "dispatch": dispatch,
        "create_tasks": [],
        "phase_complete": False,
        "phase_notes": "",
        "reasoning": "Deterministic plan: dispatch every unblocked task in priority order.",
    }


def seed_first_tasks(ctx: AgentContext, task_bus, *, genre: str = "") -> list[Task]:
    """After planning is signed off, create the Phase 1 tasks that get the game started.

    Deterministic on purpose: the very first dispatch of a new project should not depend on a
    model's mood. The Prompter takes over from tick two.
    """
    created: list[Task] = []
    created.append(
        task_bus.create(
            assigned_to="documenter",
            created_by="prompter",
            phase=1,
            kind="documentation",
            title="Freeze the design summary for the build team",
            instruction=(
                "Read memory/gdd.md and planning/chat.json. Rewrite `## SUMMARY` so it is "
                "under 400 words and contains: genre, core loop, art direction, perspective, "
                "current phase, and the three most important open questions. This is the only "
                "section other agents receive, so anything missing here is invisible to them."
            ),
            expected_outputs=["memory/gdd.md#SUMMARY"],
            file_claims=["memory/gdd.md"],
            priority=10,
        )
    )
    created.append(
        task_bus.create(
            assigned_to="programmer",
            created_by="prompter",
            phase=1,
            kind="implementation",
            title="Build the playable character",
            instruction=(
                "Create the player: a CharacterBody2D scene with a CollisionShape2D, a "
                "movement script using the input actions already declared in project.godot "
                "(move_left, move_right, move_up, move_down, jump), and a simple animated "
                "sprite placeholder. Include acceleration, friction, and a grounded check. "
                "The scene must open in Godot 4 without errors."
            ),
            dependencies=[created[0].task_id],
            expected_outputs=["godot_project/scenes/player.tscn", "godot_project/scripts/player.gd"],
            file_claims=["godot_project/scenes/player.tscn", "godot_project/scripts/player.gd"],
            priority=20,
        )
    )
    created.append(
        task_bus.create(
            assigned_to="programmer",
            created_by="prompter",
            phase=1,
            kind="implementation",
            title="Build the first test level",
            instruction=(
                "Create Level 1: a TileMapLayer or StaticBody2D floor with collision, a "
                "camera that follows the player, and clear boundaries so the player cannot "
                "fall out of the world. Wire scenes/main.tscn to instance the level and the "
                "player together."
            ),
            dependencies=[created[1].task_id],
            expected_outputs=["godot_project/scenes/level_1.tscn", "godot_project/scenes/main.tscn"],
            file_claims=["godot_project/scenes/level_1.tscn", "godot_project/scenes/main.tscn"],
            priority=30,
        )
    )
    created.append(
        task_bus.create(
            assigned_to="tester",
            created_by="prompter",
            phase=1,
            kind="test",
            title="Verify the playable core",
            instruction=(
                "Run the project, capture screenshots of the player moving and colliding with "
                "the floor, and report pass/fail against: player visible, physics sane, "
                "collisions firing, animation advancing, no error dialogs, framerate stable."
            ),
            dependencies=[created[2].task_id],
            expected_outputs=["reports/test_phase1.md"],
            file_claims=["reports/test_phase1.md"],
            priority=40,
        )
    )
    for task in created:
        log.info("seeded %s for %s", task.task_id, task.assigned_to)
    return created


def build_context_note(ctx: AgentContext) -> str:
    """The compressed-context note appended to the Prompter's own prompt."""
    bundle = adhd_filter.build_context(ctx.project_path, AGENT_ID, task=ctx.task)
    return bundle.render()


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID
