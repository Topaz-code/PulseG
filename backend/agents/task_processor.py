"""Task Processor: small, fast tasks on the cheapest model in the roster.

Its whole reason to exist is cost control. Deciding whether a task title is a duplicate,
normalising a small JSON blob, or turning a one-line human note into a well-formed task does
not need a frontier model. It runs ``llama-3.1-8b-instant`` on its own limiter so the big
agents never spend their rate limit on bookkeeping.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..core.models import Status, Task
from .base import AgentContext, AgentRunResult, note_agent_state, run_text_agent

log = logging.getLogger(__name__)

AGENT_ID = "task_processor"


def run(ctx: AgentContext) -> AgentRunResult:
    run_result = run_text_agent(ctx, require_json=True)
    if not run_result.paused:
        note_agent_state(ctx, run_result)
    return run_result


# --- the four bookkeeping jobs ---------------------------------------------------------


def normalise_human_note(note: str, *, task: Task | None = None) -> dict[str, Any]:
    """Turn a human comment into a structured change request.

    Falls back to a plain rewrite so a rejected task is never held up by a missing key.
    """
    from .registry import registry

    agent = registry.get(AGENT_ID)
    text = (note or "").strip()
    structured = {
        "summary": text[:200],
        "requirements": [],
        "file_hints": [],
        "severity": "change",
        "is_rejection": False,
    }
    if agent is None or not text:
        return structured

    context = AgentContext(agent=agent, project_path=(task.project_root if task else None) or _neutral_path())
    if task is not None:
        context.task = task
    run_result = run_text_agent(
        context,
        extra_instruction=(
            "Rewrite this human note into a machine-actionable change request for the agent "
            f"that owns task {task.task_id if task else 'n/a'}.\n\nHUMAN NOTE:\n{text}"
        ),
        require_json=True,
    )
    if run_result.ok and run_result.payload:
        payload = run_result.payload
        structured.update(
            {
                "summary": str(payload.get("summary") or text)[:300],
                "requirements": [str(item)[:200] for item in (payload.get("requirements") or [])][:8],
                "file_hints": [str(item)[:120] for item in (payload.get("file_hints") or [])][:6],
                "severity": str(payload.get("severity") or "change"),
                "is_rejection": bool(payload.get("is_rejection", True)),
            }
        )
    return structured


def _neutral_path():
    from ..core.paths import projects_root

    return projects_root()


def dedupe_against_open(titles: list[str]) -> dict[str, Any]:
    """Find tasks that mean the same thing as work already queued.

    Deterministic and local: no model call, so it can run on every Prompter tick for free.
    """
    canonical = [re.sub(r"[^a-z0-9 ]+", " ", title.lower()).split() for title in titles]
    groups: list[list[str]] = []
    used: set[int] = set()
    for index, words in enumerate(canonical):
        if index in used:
            continue
        group = [titles[index]]
        for other in range(index + 1, len(canonical)):
            if other in used:
                continue
            overlap = len(set(words) & set(canonical[other]))
            union = len(set(words) | set(canonical[other])) or 1
            if overlap / union >= 0.6:
                group.append(titles[other])
                used.add(other)
        groups.append(group)
    return {"groups": [group for group in groups if len(group) > 1], "checked": len(titles)}


def summarise_attempt_history(task: Task) -> str:
    """One line per attempt, for the Needs Intervention card and the Agent Detail view."""
    if not task.attempts:
        return "No attempts recorded yet."
    lines: list[str] = []
    for index, attempt in enumerate(task.attempts, start=1):
        status = "ok" if attempt.success else attempt.failure_kind.value
        lines.append(
            f"{index}. {attempt.provider}/{attempt.model} - {status} "
            f"({attempt.duration_ms} ms){': ' + attempt.error[:160] if attempt.error else ''}"
        )
    return "\n".join(lines)


def small_json_cleanup(raw: str) -> dict[str, Any] | None:
    """Best-effort parse of a small blob the big agents should not be spending tokens on."""
    if not raw:
        return None
    for candidate in (raw, raw.strip().strip("`")):
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID


def is_lightweight(status: Status) -> bool:
    """Bookkeeping work can be batched onto the cheap model at any time."""
    return status in (Status.PENDING, Status.NEEDS_HUMAN_REVIEW)
