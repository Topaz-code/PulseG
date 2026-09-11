"""Agent dispatch table.

Adding a new agent module is a two-line change here; adding a new *roster* agent that only
needs chat, vision, image generation or transcription needs no change at all - it falls back
to :func:`generic.run`, which is the whole point of the config-driven design.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .base import AgentContext, AgentRunResult, run_and_write, run_text_agent
from . import (
    audio_curator,
    documenter,
    image_generator,
    planning_agent,
    programmer,
    prompter,
    researcher,
    story_writer,
    task_processor,
    tester,
    transcriptor,
)

log = logging.getLogger(__name__)

def _auditor_runner(ctx: AgentContext, **kwargs: Any) -> AgentRunResult:
    """The Auditor has a real implementation in ``orchestration.auditor``.

    This indirection exists so the Auditor still appears in the roster, the UI and the
    fallback router with a plausible generic path if it is somehow invoked as a chat agent.
    """
    try:
        from ..orchestration import auditor as auditor_module
    except Exception as exc:  # pragma: no cover - import guard
        log.error("Auditor module unavailable: %s", exc)
        return run_text_agent(ctx, require_json=True)
    if ctx.task is None:
        return run_text_agent(ctx, require_json=True)
    verdict = auditor_module.audit_task(ctx.project_path, ctx.task, router=ctx.router_or_default())
    return AgentRunResult(
        ok=True,
        agent_id="auditor",
        output=(verdict.summary or "") + ("\n" + verdict.fix_note if verdict.fix_note else ""),
        payload=verdict.model_dump(mode="json"),
    )


#: agent id -> callable taking (ctx, **kwargs)
RUNNERS: dict[str, Callable[..., AgentRunResult]] = {
    "planning_agent": planning_agent.run,
    "prompter": prompter.plan,
    "researcher": researcher.run,
    "transcriptor": transcriptor.run,
    "documenter": documenter.run,
    "image_generator": image_generator.run,
    "audio_curator": audio_curator.run,
    "programmer": programmer.run,
    "tester": tester.run,
    "story_writer": story_writer.run,
    "auditor": _auditor_runner,
    "task_processor": task_processor.run,
}


def generic(ctx: AgentContext, **kwargs: Any) -> AgentRunResult:
    """Runner for roster agents that have no specialised module.

    This is what makes "add an agent with config only" true: a new agent gets its prompt, its
    chain, its context and its file-writing behaviour from the same machinery as the built-in
    ones, with no Python written for it.
    """
    require_json = bool(ctx.agent.skills) or ctx.task is not None and ctx.task.expected_outputs != []
    return run_and_write(ctx, require_json=require_json, **kwargs)


def runner_for(agent_id: str) -> Callable[..., AgentRunResult]:
    return RUNNERS.get(agent_id, generic)


def run_agent(ctx: AgentContext, **kwargs: Any) -> AgentRunResult:
    """Execute one agent, never raising: a crash becomes a failed result for the task record."""
    runner = runner_for(ctx.agent.id)
    try:
        return runner(ctx, **kwargs)
    except Exception as exc:  # pragma: no cover - defensive boundary
        log.exception("Agent %s raised", ctx.agent.id)
        return AgentRunResult(
            ok=False,
            agent_id=ctx.agent.id,
            error=f"{type(exc).__name__}: {exc}",
            problems=[f"Unhandled error in the {ctx.agent.id} runner; the task was not lost."],
        )


__all__ = [
    "RUNNERS",
    "generic",
    "run_agent",
    "runner_for",
    "audio_curator",
    "documenter",
    "image_generator",
    "planning_agent",
    "programmer",
    "prompter",
    "researcher",
    "story_writer",
    "task_processor",
    "tester",
    "transcriptor",
]
