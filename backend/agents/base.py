"""Shared agent runner.

Every agent module is thin because the interesting parts are shared: build the compressed
context, call the model through the fallback router, parse the JSON payload, write the files
the agent produced, run the deterministic skills over the result, and update the task.

What each agent module customises:

* ``AGENT_ID`` and the post-processing function (e.g. the Programmer validates scenes with
  Godot, the Image Generator saves images, the Tester attaches screenshots).
* Nothing else. Prompt text lives in ``roster.py`` so it can be reviewed and edited by the
  user from Settings without touching code.

Failure handling follows Pillar B.2 exactly: a :class:`ChainExhausted` result is returned as
``paused=True`` so the caller can move the task to ``NEEDS_INTERVENTION`` with the attempt
history intact rather than treating it as a dead end.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..core.models import AgentDefinition, Task
from ..mcp.filesystem_mcp import write_agent_files
from ..providers.router import ChainExhausted, FallbackRouter, RouterResult
from ..skills import adhd_filter, deslop, no_ai_slop

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AgentContext:
    """Everything an agent needs to do its job, passed explicitly (no globals)."""

    agent: AgentDefinition
    project_path: Path
    project_id: str = ""
    task: Task | None = None
    router: FallbackRouter | None = None
    godot_executable: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def router_or_default(self) -> FallbackRouter:
        if self.router is None:
            self.router = FallbackRouter()
        return self.router

    @property
    def task_id(self) -> str:
        return self.task.task_id if self.task else ""


@dataclass
class AgentRunResult:
    ok: bool
    agent_id: str
    output: str = ""
    payload: dict[str, Any] | None = None
    artifacts: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    provider_used: str = ""
    model_used: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    skill_results: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    paused: bool = False
    pause_reason: str = ""
    error: str = ""

    @property
    def summary(self) -> str:
        if self.paused:
            return f"paused: {self.pause_reason[:200]}"
        if not self.ok:
            return f"failed: {self.error[:200]}"
        return f"{self.provider_used}/{self.model_used} in {self.latency_ms} ms"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "agent_id": self.agent_id,
            "provider_used": self.provider_used,
            "model_used": self.model_used,
            "latency_ms": self.latency_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "artifacts": self.artifacts,
            "screenshots": self.screenshots,
            "problems": self.problems,
            "notes": self.notes,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "skill_results": {
                name: (result.as_dict() if hasattr(result, "as_dict") else result)
                for name, result in self.skill_results.items()
            },
        }


# --- prompt construction --------------------------------------------------------------


def build_agent_messages(ctx: AgentContext, *, extra_instruction: str = "", document_kind: str = "prose") -> list[dict[str, str]]:
    """Compose the system + user messages for one call.

    The system prompt is the agent's role prompt plus the compressed CONTEXT block; the user
    message is the task instruction (including any revision notes from a rejection).
    """
    bundle = adhd_filter.build_context(ctx.project_path, ctx.agent.id, task=ctx.task)
    system_parts = [ctx.agent.system_prompt.strip()]
    rendered = bundle.render()
    if rendered:
        system_parts.append(rendered)
    system = "\n\n".join(part for part in system_parts if part)

    user_parts: list[str] = []
    if ctx.task:
        user_parts.append(f"TASK {ctx.task.task_id} (phase {ctx.task.phase}, kind {ctx.task.kind})")
        user_parts.append(ctx.task.instruction.strip())
        if ctx.task.expected_outputs:
            user_parts.append(
                "EXPECTED OUTPUTS (the Auditor checks these one by one):\n"
                + "\n".join(f"- {item}" for item in ctx.task.expected_outputs)
            )
        if ctx.task.file_claims:
            user_parts.append("FILES YOU MAY WRITE:\n" + "\n".join(f"- {item}" for item in ctx.task.file_claims))
        if ctx.task.human_note:
            user_parts.append(f"HUMAN NOTE FROM THE LAST REVIEW:\n{ctx.task.human_note}")
        if ctx.task.auditor_verdict and ctx.task.auditor_verdict.is_declined:
            verdict = ctx.task.auditor_verdict
            user_parts.append(
                "THE AUDITOR DECLINED THE PREVIOUS ATTEMPT:\n"
                f"score {verdict.score}/10\n{verdict.fix_note or verdict.summary}"
            )
    elif extra_instruction:
        user_parts.append(extra_instruction)
    else:
        user_parts.append("No task was provided. Explain what you would do next and why.")

    if extra_instruction and ctx.task:
        user_parts.append(extra_instruction)

    # A machine-readable identity line. Providers ignore it; demo mode and the diagnostic log
    # use it to route deterministically, which is what makes PULSEG_DEMO=1 trustworthy.
    marker = f"PULSEG_AGENT: {ctx.agent.id}"
    if ctx.task:
        marker += f" | TASK {ctx.task.task_id} | kind {ctx.task.kind}"
    user_parts.append(marker)

    ctx.extra["context_stats"] = bundle.stats
    ctx.extra["context_omitted"] = bundle.omitted
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


# --- payload handling -----------------------------------------------------------------


JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_payload(response_text: str, parsed: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    """Get the structured payload out of a response, or explain why it could not be read."""
    if parsed:
        return parsed, ""
    if not response_text.strip():
        return None, "The model returned an empty response."
    match = JSON_FENCE.search(response_text)
    if match:
        import json

        try:
            return json.loads(match.group(1)), ""
        except json.JSONDecodeError as exc:
            return None, f"Fenced JSON did not parse: {exc}"
    start = response_text.find("{")
    end = response_text.rfind("}")
    if start != -1 and end > start:
        import json

        try:
            return json.loads(response_text[start : end + 1]), ""
        except json.JSONDecodeError:
            pass
    if response_text.strip().startswith("```file:") or "```file:" in response_text:
        # A pure code submission with no JSON report: valid for the Programmer.
        return {"files": [], "note": "response contained file blocks but no JSON report"}, ""
    return None, (
        "The response contained no JSON payload. The agent must return the JSON object its "
        "system prompt specifies."
    )


def run_text_agent(
    ctx: AgentContext,
    *,
    extra_instruction: str = "",
    images: Sequence[str] | None = None,
    document_kind: str = "prose",
    max_tokens: int | None = None,
    require_json: bool = True,
) -> AgentRunResult:
    """Standard agent execution: context, call, parse, skills."""
    agent = ctx.agent
    router = ctx.router_or_default()
    messages = build_agent_messages(ctx, extra_instruction=extra_instruction)
    started = time.perf_counter()
    result: RouterResult = router.chat(
        agent,
        messages,
        task=ctx.task,
        images=images,
        max_tokens=max_tokens,
    )

    if not result.ok:
        exhausted: ChainExhausted | None = result.exhausted
        return AgentRunResult(
            ok=False,
            agent_id=agent.id,
            paused=True,
            pause_reason=str(exhausted) if exhausted else "provider chain exhausted",
            problems=[str(exhausted)] if exhausted else [],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    response = result.response
    text = getattr(response, "text", "") or ""
    payload, problem = parse_payload(text, getattr(response, "parsed", None))
    run = AgentRunResult(
        ok=payload is not None or not require_json,
        agent_id=agent.id,
        output=text,
        payload=payload,
        provider_used=result.provider_used,
        model_used=result.model_used,
        latency_ms=getattr(response, "latency_ms", 0) or int((time.perf_counter() - started) * 1000),
        tokens_in=getattr(response, "tokens_in", 0),
        tokens_out=getattr(response, "tokens_out", 0),
    )
    if problem:
        run.problems.append(problem)
    if payload is None and require_json:
        run.error = problem
        run.ok = False
    return run


def expected_outputs_present(ctx: AgentContext, run: AgentRunResult) -> list[str]:
    """Which declared outputs are missing from the artifacts and from disk.

    Some agents legitimately write through a purpose-built path rather than file blocks - the
    Documenter edits ``memory/gdd.md`` section by section - so this checks the filesystem too
    rather than trusting the artifact list alone.
    """
    if ctx.task is None:
        return []
    missing: list[str] = []
    for expected in ctx.task.expected_outputs:
        target = expected.strip().strip("`")
        if "#" in target:
            # A section reference: the file must exist and the carrier agent reports the
            # section as an artifact (``memory/gdd.md#SUMMARY``).
            path_part, section = target.split("#", 1)
            recorded = any(item == expected or item.startswith(f"{path_part}#") for item in run.artifacts)
            if recorded or (ctx.project_path / path_part).exists():
                continue
            missing.append(expected)
            continue
        if any(item == target or item.endswith(target.lstrip("/")) for item in run.artifacts):
            continue
        if (ctx.project_path / target).exists():
            continue
        missing.append(expected)
    return missing


def run_and_write(
    ctx: AgentContext,
    *,
    extra_instruction: str = "",
    images: Sequence[str] | None = None,
    document_kind: str = "prose",
    require_json: bool = True,
    write_files: bool = True,
    expect_files: bool = True,
) -> AgentRunResult:
    """Run the agent, write any file blocks it produced, and record the artifacts.

    This is what the Programmer, Documenter, Story Writer and Tester use, since all four
    produce files that must land on disk before the Auditor sees them. ``expect_files=False``
    is for agents that write through their own path instead of file blocks.
    """
    run = run_text_agent(
        ctx,
        extra_instruction=extra_instruction,
        images=images,
        document_kind=document_kind,
        require_json=require_json,
    )
    if write_files and run.output:
        written, problems = write_agent_files(
            ctx.project_path, run.output, allowed=(ctx.task.file_claims if ctx.task else None)
        )
        run.artifacts.extend(written)
        run.problems.extend(problems)
        if not written and expect_files and ctx.task and ctx.task.expected_outputs:
            run.problems.append(
                "The task expected files but none were written. The response had no usable "
                "file blocks."
            )
            run.ok = False
    if expect_files and ctx.task and ctx.task.expected_outputs:
        missing = expected_outputs_present(ctx, run)
        if missing:
            run.problems.append(
                "The submission did not produce: " + ", ".join(missing)
            )
            run.ok = False
    return run


def apply_skill_checks(
    ctx: AgentContext,
    run: AgentRunResult,
    *,
    extra_texts: Sequence[tuple[str, str]] = (),
) -> AgentRunResult:
    """Run the deterministic skills over the submission and attach the results.

    The Auditor re-runs these as its own evidence; running them here as well means the task
    record already carries them when the human opens the drawer.
    """
    texts: list[tuple[str, str]] = [(ctx.agent.id, run.output or "")]
    texts.extend(extra_texts)
    for label, text in texts:
        if not text.strip():
            continue
        run.skill_results[f"no_ai_slop:{label}"] = no_ai_slop.review(text, context=label)
        run.skill_results[f"deslop:{label}"] = deslop.review(
            text,
            agent_id=ctx.agent.id,
            payload=run.payload if label == ctx.agent.id else None,
            expected_outputs=(ctx.task.expected_outputs if ctx.task and label == ctx.agent.id else ()),
            context=label,
        )
    return run


def persist_memory_updates(ctx: AgentContext, run: AgentRunResult) -> None:
    """Apply the file-shaped side effects a payload asks for (progress, GDD sections)."""
    from ..orchestration import memory

    payload = run.payload or {}
    if ctx.task is None:
        return
    progress_lines = payload.get("progress_lines")
    if isinstance(progress_lines, list):
        for line in progress_lines[:20]:
            if isinstance(line, str) and line.strip():
                memory.append_progress(
                    ctx.project_path,
                    line.strip().lstrip("-").strip(),
                    agent=ctx.agent.id,
                    task_id=ctx.task.task_id,
                    status="NOTE",
                )
    sections = payload.get("gdd_sections")
    if isinstance(sections, dict):
        for name, body in sections.items():
            if isinstance(body, str) and body.strip():
                memory.write_gdd_section(ctx.project_path, str(name), body)
                run.artifacts.append(f"memory/gdd.md#{str(name).upper()}")
    decisions = payload.get("decisions")
    if isinstance(decisions, list) and decisions:
        lines = [
            f"- {_utcnow()[:10]} | {item.get('decision', '')} "
            f"| replaced: {item.get('replaced', 'n/a')} | why: {item.get('why', '')}"
            for item in decisions[:20]
            if isinstance(item, dict) and item.get("decision")
        ]
        if lines:
            memory.write_gdd_section(
                ctx.project_path,
                "DECISIONS",
                _append_to_section(ctx.project_path, "DECISIONS", "\n".join(lines)),
            )


def _append_to_section(project_path: Path, section: str, additional: str) -> str:
    """Read a GDD section and append lines, keeping existing content."""
    import re as _re

    from ..core.atomic import read_text

    text = read_text(project_path / "memory" / "gdd.md")
    match = _re.search(
        rf"^##\s+{_re.escape(section)}\s*$(.*?)(?=^##\s|\Z)", text, _re.MULTILINE | _re.DOTALL
    )
    body = match.group(1).strip() if match else ""
    return (body + "\n" + additional).strip()


def snapshot_context(ctx: AgentContext, stage: str) -> None:
    """Write a crash-recovery snapshot so an interrupted task can resume with its context."""
    from ..orchestration import memory

    memory.write_snapshot(
        ctx.project_path,
        ctx.agent.id,
        {
            "stage": stage,
            "task_id": ctx.task_id,
            "context": {key: len(str(value)) for key, value in ctx.extra.items()},
            "at": _utcnow(),
        },
    )


def note_agent_state(ctx: AgentContext, run: AgentRunResult, *, status: str = "") -> None:
    """Update agent_states.json - what the Dashboard's agent grid renders."""
    from ..orchestration import memory

    state = memory.update_agent_state(
        ctx.project_path,
        ctx.agent.id,
        status=status or ("idle" if run.ok else ("blocked" if run.paused else "error")),
        current_task=None,
        last_task=ctx.task_id or None,
        active_provider=run.provider_used or None,
        active_model=run.model_used or None,
        note=run.problems[0][:300] if run.problems else "",
    )
    state.tasks_completed += 1 if run.ok else 0
    state.tasks_failed += 0 if run.ok else 1
    state.total_latency_ms += run.latency_ms
    state.tokens_in += run.tokens_in
    state.tokens_out += run.tokens_out
    memory.write_agent_states(ctx.project_path, {**_states(ctx), ctx.agent.id: state})


def _states(ctx: AgentContext):
    from ..orchestration import memory

    return memory.read_agent_states(ctx.project_path)
