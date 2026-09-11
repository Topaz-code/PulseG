"""Documenter agent: owns memory/gdd.md and memory/progress.md."""
from __future__ import annotations

import logging

from ..core.models import Task
from ..skills import harvard_shape
from .base import (
    AgentContext,
    AgentRunResult,
    apply_skill_checks,
    expected_outputs_present,
    note_agent_state,
    persist_memory_updates,
    run_and_write,
)

log = logging.getLogger(__name__)

AGENT_ID = "documenter"


def run(ctx: AgentContext) -> AgentRunResult:
    # expect_files=False: this agent writes through persist_memory_updates() rather than file
    # blocks, so the generic "did it write files" check would reject correct work. The
    # expected-output check below still runs, against the filesystem.
    run_result = run_and_write(
        ctx, document_kind="documentation", require_json=True, expect_files=False
    )
    if run_result.paused:
        note_agent_state(ctx, run_result)
        return run_result

    # Apply the file-shaped side effects: progress lines, GDD sections, decision log.
    persist_memory_updates(ctx, run_result)
    missing = expected_outputs_present(ctx, run_result)
    if missing:
        run_result.problems.append("The submission did not produce: " + ", ".join(missing))
        run_result.ok = False

    # A GDD is expected to be uniform prose; use the documentation thresholds so the
    # stylometry check does not flag a reference document for being a reference document.
    gdd_text = ""
    gdd_path = ctx.project_path / "memory" / "gdd.md"
    if gdd_path.exists():
        gdd_text = gdd_path.read_text(encoding="utf-8", errors="replace")
    if gdd_text:
        run_result.skill_results["harvard_shape:gdd"] = harvard_shape.review(
            gdd_text, context="memory/gdd.md", document_kind="documentation"
        )

    apply_skill_checks(ctx, run_result, extra_texts=[("memory/gdd.md", gdd_text)] if gdd_text else [])
    note_agent_state(ctx, run_result)
    return run_result


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID
