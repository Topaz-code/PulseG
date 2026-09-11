"""Programmer agent: writes Godot 4 scenes and GDScript, then validates before submitting.

Extra step compared with the generic runner: after the files are written, the Godot MCP
server (or the Godot CLI) is asked to check the scripts. A scene that does not parse is
caught here rather than two tasks later in the Tester, which is where it would otherwise
surface as a confusing "the game will not start".
"""
from __future__ import annotations

import logging

from ..core.models import Task
from ..mcp.godot_mcp_client import GodotMCPClient
from .base import AgentContext, AgentRunResult, apply_skill_checks, note_agent_state, run_and_write, snapshot_context

log = logging.getLogger(__name__)

AGENT_ID = "programmer"


def run(ctx: AgentContext, *, validate: bool = True) -> AgentRunResult:
    snapshot_context(ctx, "programmer.start")
    run_result = run_and_write(ctx, document_kind="code", require_json=False)
    if run_result.paused:
        note_agent_state(ctx, run_result)
        return run_result

    # Validate whatever Godot files were produced.
    godot_files = [path for path in run_result.artifacts if path.endswith((".gd", ".tscn"))]
    if validate and godot_files:
        client = GodotMCPClient(
            executable=ctx.godot_executable,
            project_path=ctx.project_path / "godot_project",
        )
        try:
            validation = client.validate_scripts(godot_files)
            run_result.problems.append(
                f"Godot validation ({validation.transport}): {validation.message}"
            )
            if not validation.ok:
                run_result.ok = False
                run_result.error = validation.message
                log.warning("Godot validation failed for %s: %s", ctx.task_id, validation.message)
            ctx.extra["godot_validation"] = validation.as_dict()
        finally:
            client.close()
    elif validate:
        run_result.problems.append(
            "No Godot files were produced by a task that looked like code work. Check the "
            "instruction and the expected outputs."
        )

    apply_skill_checks(ctx, run_result)
    _scene_structure_checks(ctx, run_result)
    note_agent_state(ctx, run_result)
    snapshot_context(ctx, "programmer.done")
    return run_result


def _scene_structure_checks(ctx: AgentContext, run_result: AgentRunResult) -> None:
    """Deterministic structural checks on the emitted scenes.

    These mirror the Auditor's ``deslop`` findings but run at author time, so the agent's
    own retry (if the task is rejected) starts from a known list of defects.
    """
    problems: list[str] = []
    for path in run_result.artifacts:
        if not path.endswith(".tscn"):
            continue
        full = ctx.project_path / path
        if not full.exists():
            continue
        text = full.read_text(encoding="utf-8", errors="replace")
        if ("CharacterBody2D" in text or "Area2D" in text or "RigidBody2D" in text) and "CollisionShape2D" not in text:
            problems.append(f"{path}: physics body without a CollisionShape2D child")
        if "script = ExtResource" not in text and "script" not in text:
            problems.append(f"{path}: scene has no script attached")
    if problems:
        run_result.problems.extend(problems)
        run_result.skill_results["programmer.structure"] = {
            "problems": problems,
            "note": "Deterministic scene checks run before the Auditor sees the task.",
        }


def validate_only(ctx: AgentContext, paths: list[str]) -> dict:
    client = GodotMCPClient(executable=ctx.godot_executable, project_path=ctx.project_path / "godot_project")
    try:
        return client.validate_scripts(paths).as_dict()
    finally:
        client.close()


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID
