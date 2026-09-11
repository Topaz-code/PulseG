"""The Auditor: every submission funnels through here before anything else can move.

Two layers, deliberately:

1. **Deterministic.** ``no_ai_slop``, ``humanizer``, ``deslop``, ``harvard_shape`` and a
   task-fit check run as code over the submission and the files it wrote. These cannot be
   talked out of a finding.
2. **Model judgement.** The Auditor model reads the same evidence - plus the screenshots, for
   visual tasks - and returns a structured verdict.

The model may lower a score and explain *why* in the fix note, but it can never overrule a
high-severity deterministic finding: those force a DECLINED verdict. The reverse also holds -
a model that says "looks great" does not stop a deterministic failure, and a model that
declines without a reason is rejected as malformed. That asymmetry is the point of the
pipeline: taste is subjective, evidence is not.

Nothing here approves work. The Auditor's output is a recommendation; the human gate decides.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

from ..core.models import Status, Task, Verdict
from ..mcp.filesystem_mcp import read_project_file
from ..providers.router import FallbackRouter
from ..skills import adhd_filter, deslop, grillme, harvard_shape, humanizer, no_ai_slop
from ..skills.base import SkillResult, severity_weight
from . import memory

log = logging.getLogger(__name__)

MAX_SUBMISSION_CHARS = 60_000
MAX_FILE_CHARS = 20_000

#: Narrative work is held to the writing skills; code and documentation are not - a scene file
#: is not expected to use contractions.
NARRATIVE_KINDS = {"narrative", "story", "dialogue", "planning", "documentation"}


def _submission_text(project_path: Path, task: Task) -> str:
    """The submitted prose plus the contents of the files it claims to have written."""
    parts: list[str] = []
    if task.output:
        parts.append(task.output[:MAX_SUBMISSION_CHARS])
    used = len(parts[0]) if parts else 0
    for relative in task.artifacts[:20]:
        if used >= MAX_SUBMISSION_CHARS:
            break
        if relative.startswith("memory/") and "#" in relative:
            relative = relative.split("#", 1)[0]
        if not relative or relative.startswith("screenshots/"):
            continue
        try:
            content = read_project_file(project_path, relative, limit=MAX_FILE_CHARS)
        except Exception:
            continue
        if not content:
            continue
        parts.append(f"--- FILE {relative} ---\n{content}")
        used += len(content)
    return "\n\n".join(parts)[:MAX_SUBMISSION_CHARS]


def _document_kind(task: Task) -> str:
    mapping = {
        "documentation": "documentation",
        "narrative": "narrative",
        "story": "narrative",
        "dialogue": "narrative",
        "implementation": "code",
        "test": "documentation",
        "research": "documentation",
        "planning": "documentation",
    }
    return mapping.get(task.kind, "documentation")


def run_deterministic_checks(project_path: Path, task: Task, submission: str) -> dict[str, SkillResult]:
    """Every check that does not need a model. The Auditor's evidence base."""
    kind = _document_kind(task)
    results: dict[str, SkillResult] = {}

    results["task_fit"] = adhd_filter.review(task=task, submission=submission, artifacts=task.artifacts)
    results["no_ai_slop"] = no_ai_slop.review(submission, context=f"{task.task_id}/{kind}")
    results["deslop"] = deslop.review(
        submission,
        agent_id=task.assigned_to,
        expected_outputs=task.expected_outputs,
        artifacts=task.artifacts,
        context=f"{task.task_id}/{kind}",
    )
    results["harvard_shape"] = harvard_shape.review(
        submission, context=f"{task.task_id}/{kind}", document_kind=kind
    )
    if kind in ("narrative", "documentation"):
        results["humanizer"] = humanizer.review(submission, context=f"{task.task_id}/{kind}", document_kind=kind)
    if task.kind == "planning":
        results["grillme"] = _planning_gate(project_path, submission)
    return results


def _planning_gate(project_path: Path, submission: str) -> SkillResult:
    """A planning task may not pass unless the ledger itself is ready."""
    from ..agents import planning_agent

    state = planning_agent.read_state(project_path)
    ledger = planning_agent.get_ledger(state)
    transcript = submission
    try:
        chat = memory.read_chat(project_path)
        transcript = "\n".join(message.content for message in chat[-20:])
    except Exception:  # pragma: no cover - chat history is optional evidence
        pass
    return grillme.review(ledger=ledger, concept=state.get("concept", ""), transcript=transcript)


def deterministic_score(results: dict[str, SkillResult]) -> tuple[int, list[str]]:
    """Score 0-10 from deterministic evidence, with the reasons that moved it."""
    score = 10
    reasons: list[str] = []
    for name, result in results.items():
        if result.passed:
            continue
        penalty = max(1, round(result.severity_score / 12))
        score -= penalty
        if result.has_high_severity:
            score -= 1
        reasons.append(f"{name}: -{penalty} ({result.summary or 'findings present'})")
    return max(0, min(10, score)), reasons


def _hard_failures(results: dict[str, SkillResult]) -> list[str]:
    """Findings that force a decline regardless of what the model thinks."""
    failures: list[str] = []
    for name, result in results.items():
        for finding in result.findings:
            if finding.severity == "high":
                failures.append(f"{name}: {finding.message}")
    return failures[:10]


def _skill_flags(results: dict[str, SkillResult]) -> dict[str, list[str]]:
    flags: dict[str, list[str]] = {}
    for name, result in results.items():
        messages = result.flags()
        if messages:
            flags[name] = messages[:10]
    return flags


def _fix_note(results: dict[str, SkillResult], model_note: str = "") -> str:
    """Write the rejection as work to do, most severe first."""
    lines: list[str] = []
    if model_note.strip():
        lines.append(model_note.strip())
    ordered: list[tuple[int, str, str]] = []
    for name, result in results.items():
        for finding in result.findings:
            ordered.append(({"high": 0, "medium": 1, "low": 2, "info": 3}.get(finding.severity, 2), name, f"{finding.message}" + (f" Fix: {finding.recommendation}" if finding.recommendation else "")))
    ordered.sort(key=lambda item: item[0])
    for _, name, text in ordered[:8]:
        lines.append(f"- [{name}] {text}")
    return "\n".join(lines)


def _auditor_prompt(task: Task, results: dict[str, SkillResult], submission: str) -> str:
    evidence: list[str] = []
    for name, result in results.items():
        evidence.append(
            f"{name}: {'passed' if result.passed else 'failed'} "
            f"(severity score {result.severity_score})\n"
            + "\n".join(f"  - [{finding.severity}] {finding.message}" for finding in result.findings[:8])
        )
    return (
        f"You are auditing {task.task_id} ('{task.title}'), assigned to {task.assigned_to}.\n\n"
        f"TASK INSTRUCTION\n{task.instruction}\n\n"
        "EXPECTED OUTPUTS\n" + ("\n".join(f"- {item}" for item in task.expected_outputs) or "- none declared") + "\n\n"
        "DETERMINISTIC EVIDENCE (already run; you cannot overrule a high-severity item)\n"
        + "\n".join(evidence)
        + f"\n\nSUBMISSION\n{submission[:12000]}\n\n"
        "Decide whether this work is good enough to put in front of the human. You are looking "
        "for real defects: wrong engine version, unimplementable advice, a mechanic with no rule, "
        "code that would not parse, evidence that does not prove the claim. Do not decline for "
        "style alone if the deterministic checks passed. Do not approve anything you cannot see "
        "evidence for. Return the JSON object from your system prompt."
    )


def _model_verdict(
    task: Task,
    results: dict[str, SkillResult],
    submission: str,
    router: FallbackRouter,
    *,
    project_path: Path,
) -> tuple[dict[str, Any], str, str]:
    """Ask the Auditor model. Returns (payload, provider, model); never raises."""
    from ..agents.base import AgentContext
    from ..agents.registry import registry

    agent = registry.get("auditor")
    if agent is None:
        return {}, "", ""
    context = AgentContext(agent=agent, project_path=project_path, task=task, router=router)
    images = [str(project_path / shot) for shot in task.screenshots[:6]] if task.screenshots else None
    from ..agents.base import run_text_agent

    run = run_text_agent(
        context,
        extra_instruction=_auditor_prompt(task, results, submission),
        images=images,
        require_json=True,
    )
    if run.paused or not run.ok or not run.payload:
        return {}, run.provider_used, run.model_used
    return run.payload, run.provider_used, run.model_used


def audit_task(
    project_path: Path,
    task: Task,
    *,
    router: FallbackRouter | None = None,
    use_model: bool = True,
) -> Verdict:
    """Audit one submission and return the verdict that will be stored on the task."""
    submission = _submission_text(project_path, task)
    results = run_deterministic_checks(project_path, task, submission)
    score, reasons = deterministic_score(results)
    failures = _hard_failures(results)

    payload: dict[str, Any] = {}
    provider_used = ""
    model_used = ""
    if use_model:
        payload, provider_used, model_used = _model_verdict(
            task, results, submission, router or FallbackRouter(), project_path=project_path
        )

    model_verdict = str(payload.get("verdict", "")).upper()
    model_score = payload.get("score")
    model_summary = str(payload.get("summary", "")).strip()
    model_note = str(payload.get("fix_note", "")).strip()

    # The model can lower the score, never raise it above the deterministic ceiling.
    if isinstance(model_score, (int, float)):
        score = min(score, max(0, int(model_score)))
    if not payload:
        reasons.append(
            "The Auditor model was unavailable, so this verdict rests on the deterministic "
            "checks alone. The task still goes to you for review."
        )

    verdict_value = "DECLINED" if failures else ("APPROVED" if score >= 6 else "DECLINED")
    if model_verdict == "DECLINED" and not failures:
        verdict_value = "DECLINED"
    if verdict_value == "APPROVED" and model_verdict == "APPROVED":
        verdict_value = "APPROVED"
    if not failures and model_verdict == "APPROVED" and score >= 6:
        verdict_value = "APPROVED"

    flags = _skill_flags(results)
    if payload.get("skill_flags"):
        for name, values in dict(payload["skill_flags"]).items():
            if values:
                flags.setdefault(name, []).extend(str(item) for item in values if item)

    screenshots_reviewed = [str(item) for item in (payload.get("screenshots_reviewed") or [])]
    if not screenshots_reviewed:
        screenshots_reviewed = list(task.screenshots)

    summary = model_summary or (
        "Deterministic checks passed." if not failures else f"{len(failures)} blocking finding(s)."
    )
    if failures:
        summary = f"Blocked by {len(failures)} high-severity finding(s). " + summary

    verdict = Verdict(
        task_id=task.task_id,
        verdict=verdict_value,
        score=int(score),
        skill_flags=flags,
        rubric={
            "deterministic_score": float(score),
            "blocking_findings": float(len(failures)),
            "skills_run": float(len(results)),
        },
        screenshots_reviewed=screenshots_reviewed,
        fix_note=_fix_note(results, model_note),
        summary=summary,
        provider_used=provider_used or ("deterministic" if not use_model else provider_used),
        model_used=model_used or ("deterministic" if not use_model else model_used),
        round=task.decline_count + 1,
    )
    verdict.audit_reasons = reasons  # type: ignore[attr-defined]
    memory.append_audit(project_path, verdict, agent=task.assigned_to, task_id=task.task_id)
    return verdict


def audit_report_markdown(verdict: Verdict, results: dict[str, SkillResult] | None = None) -> str:
    """Human-readable audit report, written into reports/ for the project record."""
    lines = [
        f"# Audit report {verdict.task_id}",
        "",
        f"- Verdict: {verdict.verdict}",
        f"- Score: {verdict.score}/10",
        f"- Auditor: {verdict.provider_used}/{verdict.model_used}",
        f"- Round: {verdict.round}",
        "",
        verdict.summary,
        "",
    ]
    if verdict.skill_flags:
        lines += ["## Flags", ""]
        for skill, flags in verdict.skill_flags.items():
            lines.append(f"### {skill}")
            lines.extend(f"- {flag}" for flag in flags)
            lines.append("")
    if verdict.fix_note:
        lines += ["## Fix note", "", verdict.fix_note, ""]
    if verdict.screenshots_reviewed:
        lines += ["## Screenshots reviewed", ""]
        lines.extend(f"- {shot}" for shot in verdict.screenshots_reviewed)
    if results:
        lines += ["", "## Skill metrics", ""]
        for name, result in results.items():
            lines.append(f"- {name}: {'pass' if result.passed else 'fail'} (severity {result.severity_score})")
    return "\n".join(lines)


def should_decline(task: Task) -> bool:
    return bool(task.auditor_verdict and task.auditor_verdict.is_declined)


def failing_skills(verdict: Verdict) -> Sequence[str]:
    return [name for name, flags in verdict.skill_flags.items() if flags]


def severity_total(verdict: Verdict) -> int:
    return sum(severity_weight("medium") for _ in failing_skills(verdict))


def audit_summary_for_human(task: Task) -> dict[str, Any]:
    """What the review drawer shows above the screenshots."""
    verdict = task.auditor_verdict
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "verdict": verdict.verdict if verdict else "",
        "score": verdict.score if verdict else None,
        "summary": verdict.summary if verdict else "",
        "fix_note": verdict.fix_note if verdict else "",
        "flags": verdict.skill_flags if verdict else {},
        "screenshots": task.screenshots,
        "decline_count": task.decline_count,
        "provider": task.provider_used,
        "model": task.model_used,
        "next_step": (
            "Waiting for your decision. Nothing is committed until you approve."
            if task.status is Status.NEEDS_HUMAN_REVIEW
            else "This task is not at the review gate."
        ),
    }
