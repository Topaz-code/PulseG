"""adhd_filter - the memory engine every agent call runs through.

Named for the problem it solves: a model that receives the whole project goes vague, and a
model that receives nothing invents. Each agent gets exactly three things (spec C.3):

1. ``progress.md`` - the last 20 lines, which is where the studio actually is right now.
2. ``gdd.md`` - the ``## SUMMARY`` section, capped at ~100 lines. Nothing sees the full GDD.
3. The agent's own pending tasks.

Everything else is opt-in and budgeted: recent verdicts for the agent being called, open asset
requests (so it does not regenerate art a human already supplied), a one-paragraph knowledge
digest, and the dependencies of the task in hand. If the budget runs out, the bundle records
what was dropped - a silently truncated context is how an agent ends up contradicting itself.

``review()`` is the Auditor's use of this skill: it asks whether a submission actually
answers the task that was given, which is the failure mode a compressor can cause.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core.atomic import read_text
from ..core.models import Task, Verdict
from .base import SkillResult, estimate_tokens

SKILL = "adhd_filter"

PROGRESS_TAIL_LINES = 20
GDD_SUMMARY_MAX_LINES = 100
MAX_VERDICTS = 3
MAX_ASSET_REQUESTS = 6
DEFAULT_BUDGET_TOKENS = 6000
KNOWLEDGE_DIGEST_CHARS = 900


def _read(path: Path, default: str = "") -> str:
    try:
        return read_text(path, default)
    except Exception:  # pragma: no cover - defensive
        return default


# --- the bundle ---------------------------------------------------------------------------


@dataclass
class ContextBundle:
    """Exactly what an agent is told, plus what it was not told and why."""

    agent_id: str
    progress_tail: str = ""
    gdd_summary: str = ""
    pending_tasks: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    recent_verdicts: list[dict[str, Any]] = field(default_factory=list)
    asset_requests: list[dict[str, Any]] = field(default_factory=list)
    knowledge_digest: str = ""
    omitted: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        blocks: list[str] = []
        if self.progress_tail:
            blocks.append("PROJECT PROGRESS (last lines of memory/progress.md)\n" + self.progress_tail.strip())
        if self.gdd_summary:
            blocks.append("DESIGN SUMMARY (## SUMMARY from memory/gdd.md)\n" + self.gdd_summary.strip())
        if self.pending_tasks:
            lines = [
                f"- {item.get('task_id')} [{item.get('status')}] {item.get('title')}"
                for item in self.pending_tasks
            ]
            blocks.append("YOUR PENDING TASKS\n" + "\n".join(lines))
        if self.dependencies:
            lines = [
                f"- {item.get('task_id')} ({item.get('status')}): {item.get('title')}"
                for item in self.dependencies
            ]
            blocks.append("WORK YOU DEPEND ON\n" + "\n".join(lines))
        if self.recent_verdicts:
            lines = []
            for item in self.recent_verdicts:
                lines.append(
                    f"- {item.get('task_id')}: {item.get('verdict')} (score {item.get('score')}) "
                    f"{item.get('fix_note', '')[:200]}"
                )
            blocks.append(
                "YOUR RECENT AUDITOR VERDICTS (fix these patterns before resubmitting)\n"
                + "\n".join(lines)
            )
        if self.asset_requests:
            lines = [
                f"- {item.get('name')} ({item.get('kind')}): {item.get('description', '')[:120]}"
                for item in self.asset_requests
            ]
            blocks.append("OPEN ASSET REQUESTS (do not duplicate these)\n" + "\n".join(lines))
        if self.knowledge_digest:
            blocks.append("KNOWLEDGE BASE DIGEST\n" + self.knowledge_digest.strip())
        if self.omitted:
            blocks.append(
                "NOT INCLUDED IN THIS CONTEXT (ask for it explicitly if you need it)\n"
                + "\n".join(f"- {item}" for item in self.omitted)
            )
        return "\n\n".join(blocks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "progress_tail": self.progress_tail,
            "gdd_summary": self.gdd_summary,
            "pending_tasks": self.pending_tasks,
            "dependencies": self.dependencies,
            "recent_verdicts": self.recent_verdicts,
            "asset_requests": self.asset_requests,
            "knowledge_digest": self.knowledge_digest,
            "omitted": self.omitted,
            "stats": self.stats,
        }


def read_progress_tail(project_path: Path, lines: int = PROGRESS_TAIL_LINES) -> str:
    text = _read(project_path / "memory" / "progress.md")
    if not text.strip():
        return ""
    collected: list[str] = []
    for raw in reversed(text.strip().splitlines()):
        if raw.strip():
            collected.append(raw.rstrip())
        if len(collected) >= lines:
            break
    return "\n".join(reversed(collected))


def read_gdd_summary(project_path: Path, max_lines: int = GDD_SUMMARY_MAX_LINES) -> str:
    """The ``## SUMMARY`` section only. Other agents never see the rest of the GDD."""
    text = _read(project_path / "memory" / "gdd.md")
    if not text.strip():
        return ""
    match = re.search(r"^##\s+SUMMARY\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    body = match.group(1).strip() if match else ""
    if not body:
        # No SUMMARY section yet (early planning): give the top of the file instead, labelled.
        head = "\n".join(text.strip().splitlines()[:max_lines])
        return head
    lines = body.splitlines()[:max_lines]
    return "\n".join(lines)


def pending_tasks_for(project_path: Path, agent_id: str, limit: int = 8) -> list[dict[str, Any]]:
    from ..orchestration import memory as memory_module

    out: list[dict[str, Any]] = []
    for task in memory_module.all_tasks(project_path):
        if task.assigned_to != agent_id:
            continue
        if task.status.value in ("APPROVED", "REJECTED"):
            continue
        out.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status.value,
                "phase": task.phase,
                "dependencies": task.dependencies,
            }
        )
        if len(out) >= limit:
            break
    return out


def active_dependencies(project_path: Path, task: Task | None) -> list[dict[str, Any]]:
    if task is None or not task.dependencies:
        return []
    from ..orchestration import memory as memory_module

    index = {item.task_id: item for item in memory_module.all_tasks(project_path)}
    rows: list[dict[str, Any]] = []
    for dependency in task.dependencies[:8]:
        found = index.get(dependency)
        if found is None:
            rows.append({"task_id": dependency, "status": "MISSING", "title": "dependency not found"})
            continue
        rows.append(
            {
                "task_id": found.task_id,
                "status": found.status.value,
                "title": found.title,
                "artifacts": found.artifacts[:4],
            }
        )
    return rows


def recent_verdicts(project_path: Path, agent_id: str = "", limit: int = MAX_VERDICTS) -> list[dict[str, Any]]:
    from ..orchestration import memory as memory_module

    rows: list[dict[str, Any]] = []
    for entry in reversed(memory_module.read_audit_history(project_path, limit=40)):
        if agent_id and entry.get("agent") and entry["agent"] != agent_id:
            continue
        rows.append(entry)
        if len(rows) >= limit:
            break
    return rows


def open_asset_requests(project_path: Path, limit: int = MAX_ASSET_REQUESTS) -> list[dict[str, Any]]:
    from ..orchestration import memory as memory_module

    rows: list[dict[str, Any]] = []
    for request in memory_module.read_asset_requests(project_path):
        if request.status != "open":
            continue
        rows.append(
            {
                "request_id": request.request_id,
                "name": request.name,
                "kind": request.kind,
                "description": request.description,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _knowledge_digest(project_path: Path, limit: int = KNOWLEDGE_DIGEST_CHARS) -> str:
    """One tight paragraph: titles, sources and trust, never the whole note."""
    from ..orchestration import memory as memory_module

    entries = memory_module.read_knowledge(project_path)
    if not entries:
        return ""
    lines: list[str] = []
    for entry in entries[:10]:
        source = f" ({entry.source_url})" if entry.source_url else ""
        lines.append(f"- [{entry.trust}] {entry.title}{source}: {entry.summary[:160]}")
    digest = "\n".join(lines)
    if len(digest) > limit:
        digest = digest[:limit].rsplit("\n", 1)[0] + "\n- (digest truncated; open the Knowledge view for the rest)"
    return digest


def build_context(
    project_path: Path,
    agent_id: str,
    *,
    task: Task | None = None,
    include_knowledge: bool = True,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
) -> ContextBundle:
    """Assemble the compressed context for one agent call."""
    bundle = ContextBundle(agent_id=agent_id)
    bundle.progress_tail = read_progress_tail(project_path)
    bundle.gdd_summary = read_gdd_summary(project_path)
    bundle.pending_tasks = pending_tasks_for(project_path, agent_id)
    bundle.dependencies = active_dependencies(project_path, task)
    bundle.recent_verdicts = recent_verdicts(project_path, agent_id)
    bundle.asset_requests = open_asset_requests(project_path)
    if include_knowledge:
        bundle.knowledge_digest = _knowledge_digest(project_path)

    if task is not None:
        bundle.stats["task_kind"] = task.kind
        bundle.stats["task_phase"] = task.phase
    _apply_budget(bundle, budget_tokens)
    return bundle


def _apply_budget(bundle: ContextBundle, budget_tokens: int) -> None:
    """Drop the least essential blocks first, recording each drop."""
    order = ["knowledge_digest", "asset_requests", "recent_verdicts", "dependencies", "progress_tail"]
    bundle.stats["rendered_tokens"] = estimate_tokens(bundle.render())
    for name in order:
        if bundle.stats["rendered_tokens"] <= budget_tokens:
            break
        value = getattr(bundle, name)
        if not value:
            continue
        setattr(bundle, name, [] if isinstance(value, list) else "")
        bundle.omitted.append(f"{name} (trimmed to stay inside the {budget_tokens}-token context budget)")
        bundle.stats["rendered_tokens"] = estimate_tokens(bundle.render())
    bundle.stats["budget_tokens"] = budget_tokens
    bundle.stats["omitted_count"] = len(bundle.omitted)


def compression_report(project_path: Path) -> dict[str, Any]:
    """What the filter is actually saving, in tokens, for the Logs view."""
    gdd_full = _read(project_path / "memory" / "gdd.md")
    progress_full = _read(project_path / "memory" / "progress.md")
    summary = read_gdd_summary(project_path)
    tail = read_progress_tail(project_path)
    return {
        "gdd_tokens_full": estimate_tokens(gdd_full),
        "gdd_tokens_sent": estimate_tokens(summary),
        "progress_tokens_full": estimate_tokens(progress_full),
        "progress_tokens_sent": estimate_tokens(tail),
        "saved_tokens": estimate_tokens(gdd_full) + estimate_tokens(progress_full) - estimate_tokens(summary) - estimate_tokens(tail),
        "note": "Every agent call sends only the summary section and the progress tail.",
    }


# --- the Auditor's use of this skill ---------------------------------------------------------


def review(
    *,
    task: Task,
    submission: str,
    artifacts: Sequence[str] = (),
    context: str = "submission",
) -> SkillResult:
    """Is the submission actually the thing that was asked for?

    The Auditor runs this first: a beautifully written document that answers a different
    question is a decline, not a pass.
    """
    result = SkillResult(skill=SKILL)
    body = submission or ""
    result.metrics["submission_tokens"] = estimate_tokens(body)
    result.metrics["expected_outputs"] = list(task.expected_outputs)
    result.metrics["artifacts"] = list(artifacts)

    if not body.strip() and not artifacts:
        result.add(
            "Empty submission: no text and no files were produced",
            severity="high",
            location=context,
            recommendation="The task must produce its declared outputs before it can be audited.",
        )
        result.summary = "Nothing was submitted."
        return result

    missing = [item for item in task.expected_outputs if not _output_present(item, artifacts, body)]
    for item in missing:
        result.add(
            f"Expected output not found: {item}",
            severity="high",
            location=context,
            recommendation="Produce the file (not a description of it) and resubmit.",
        )

    if len(body.strip()) < 40 and not artifacts:
        result.add(
            "Submission is a stub",
            severity="medium",
            location=context,
            recommendation="Complete the work; partial output is not an approved task.",
        )

    for keyword in _required_keywords(task.instruction):
        if keyword.lower() not in body.lower():
            result.add(
                f"The instruction names '{keyword}' and the submission never mentions it",
                severity="medium",
                location=context,
                recommendation="Address every named element of the instruction explicitly.",
            )

    if result.passed:
        result.summary = "Submission answers the task as written."
    else:
        result.summary = f"{len(result.findings)} gap(s) between the task and the submission."
    return result


_QUOTED = re.compile(r"`([^`]{3,60})`")


def _required_keywords(instruction: str) -> list[str]:
    """Backtick-quoted identifiers in the instruction are things the agent must address."""
    return [match for match in _QUOTED.findall(instruction or "") if not match.startswith("http")][:8]


def _output_present(expected: str, artifacts: Sequence[str], body: str) -> bool:
    target = expected.strip().strip("`")
    section = ""
    if "#" in target:
        target, section = target.split("#", 1)
    for artifact in artifacts:
        artifact_path, _, artifact_section = artifact.partition("#")
        if artifact_path == target or artifact_path.endswith(target.lstrip("/")):
            if not section:
                return True
            if section and (artifact_section.upper() == section.upper() or section.upper() in body.upper()):
                return True
    if section and section.upper() in (body or "").upper() and not artifacts:
        # A GDD section is legitimate output even when no file block was written, as long as
        # the section heading or its name appears unmistakably in the submission.
        return True
    return False


def ignored_instruction_excerpts(instruction: str, submission: str, window: int = 60) -> list[str]:
    """Bullet points of the instruction that never appear anywhere in the submission."""
    checks: list[str] = []
    for line in (instruction or "").splitlines():
        stripped = line.strip().lstrip("-*0123456789. ").strip()
        if len(stripped.split()) < 5:
            continue
        words = [word for word in re.findall(r"[a-zA-Z]{5,}", stripped.lower())][:4]
        if words and not any(word in (submission or "").lower() for word in words):
            checks.append(stripped[:window])
    return checks[:6]


def digest_json(payload: dict[str, Any], limit: int = 1500) -> str:
    """Compact JSON rendering used when a bundle block has to fit a small budget."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[: limit - 20] + '..."}'


def bundle_for_prompt(project_path: Path, agent_id: str, task: Task | None = None) -> str:
    """Convenience wrapper used by tests and by the API's context-preview endpoint."""
    return build_context(project_path, agent_id, task=task).render()


def verdict_pattern(result: SkillResult) -> str:
    """One-line pattern extraction for the Auditor: what class of failure was this?"""
    high = [finding.message for finding in result.findings if finding.severity == "high"]
    return high[0] if high else (result.summary or "no notable pattern")


def summarise_verdicts(verdicts: Iterable[Verdict]) -> dict[str, Any]:
    """Aggregate decline reasons for the agent detail view."""
    rows = list(verdicts)
    if not rows:
        return {"count": 0, "average_score": 0.0, "top_flags": []}
    flags: dict[str, int] = {}
    for verdict in rows:
        for flag in verdict.skill_flags:
            flags[flag] = flags.get(flag, 0) + 1
    return {
        "count": len(rows),
        "average_score": round(sum(verdict.score for verdict in rows) / len(rows), 2),
        "top_flags": sorted(flags.items(), key=lambda item: -item[1])[:8],
    }
