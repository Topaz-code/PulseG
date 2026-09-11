"""Context engine: the API-facing side of the ADHD filter.

The skills module decides *what* an agent sees; this module exposes those decisions so the UI
can show them. Two reasons that matters:

* **Trust.** The Agent Detail view can show exactly the text that will be sent before a task
  runs. A memory system nobody can inspect is a memory system nobody can debug.
* **Budget.** The Overview view shows what the filter is saving. Agents that receive the whole
  project behave worse *and* cost more, and the numbers make that visible.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.models import Task
from ..skills import adhd_filter
from . import memory
from .task_bus import TaskBus


def build_for_task(project_path: Path, agent_id: str, task: Task) -> adhd_filter.ContextBundle:
    return adhd_filter.build_context(project_path, agent_id, task=task)


def preview(project_path: Path, agent_id: str, task_id: str = "") -> dict[str, Any]:
    """What would be sent to this agent, right now."""
    task = memory_task(project_path, task_id) if task_id else None
    bundle = adhd_filter.build_context(project_path, agent_id, task=task)
    return {
        "agent_id": agent_id,
        "task_id": task_id,
        "rendered": bundle.render(),
        "stats": bundle.stats,
        "omitted": bundle.omitted,
        "sections": {
            "progress_tail": bundle.progress_tail,
            "gdd_summary": bundle.gdd_summary,
            "pending_tasks": bundle.pending_tasks,
            "dependencies": bundle.dependencies,
            "recent_verdicts": bundle.recent_verdicts,
            "asset_requests": bundle.asset_requests,
            "knowledge_digest": bundle.knowledge_digest,
        },
    }


def memory_task(project_path: Path, task_id: str) -> Task | None:
    for task in memory.all_tasks(project_path):
        if task.task_id == task_id:
            return task
    return None


def messages_for_task(project_path: Path, agent_id: str, task_id: str) -> list[dict[str, str]]:
    """The exact system and user messages, for the "inspect prompt" panel."""
    from ..agents.base import AgentContext, build_agent_messages
    from ..agents.registry import registry

    agent = registry.get(agent_id)
    if agent is None:
        raise KeyError(f"Unknown agent '{agent_id}'")
    task = memory_task(project_path, task_id)
    if task is None:
        raise KeyError(f"Unknown task '{task_id}'")
    context = AgentContext(agent=agent, project_path=project_path, task=task)
    return build_agent_messages(context)


def compression(project_path: Path) -> dict[str, Any]:
    return adhd_filter.compression_report(project_path)


def stats(project_path: Path, bus: TaskBus | None = None) -> dict[str, Any]:
    """The numbers behind the Overview view's context panel."""
    report = compression(project_path)
    tasks = (bus.all() if bus else memory.all_tasks(project_path))
    verdicts = [task.auditor_verdict for task in tasks if task.auditor_verdict]
    return {
        **report,
        "tasks_with_verdicts": len(verdicts),
        "average_score": round(sum(item.score for item in verdicts) / len(verdicts), 2) if verdicts else 0.0,
        "declines": sum(1 for item in verdicts if item.is_declined),
        "history_lines": memory.progress_line_count(project_path),
        "gdd_sections": memory.gdd_sections(project_path),
    }


def decisions(project_path: Path, limit: int = 40) -> list[str]:
    """The DECISIONS section, newest last, for the GDD viewer."""
    section = ""
    for name in memory.gdd_sections(project_path):
        if name.upper() == "DECISIONS":
            section = name
            break
    text = memory.read_gdd(project_path)
    if not section:
        return []
    import re

    match = re.search(rf"^##\s+{re.escape(section)}\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    lines = [line.strip() for line in match.group(1).strip().splitlines() if line.strip()]
    return lines[-limit:]


def agent_history(project_path: Path, agent_id: str, limit: int = 25) -> list[dict[str, Any]]:
    """Everything this agent has done, newest first - the Agent Detail view's history tab."""
    rows: list[dict[str, Any]] = []
    for task in memory.all_tasks(project_path):
        if task.assigned_to != agent_id:
            continue
        rows.append(
            {
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status.value,
                "phase": task.phase,
                "provider": task.provider_used,
                "model": task.model_used,
                "attempts": len(task.attempts),
                "retry_count": task.retry_count,
                "verdict": task.auditor_verdict.verdict if task.auditor_verdict else "",
                "score": task.auditor_verdict.score if task.auditor_verdict else None,
                "updated_at": task.updated_at,
            }
        )
    rows.sort(key=lambda row: row["updated_at"] or "", reverse=True)
    return rows[:limit]


def failure_patterns(project_path: Path, limit: int = 10) -> list[dict[str, Any]]:
    """Which failures keep happening, grouped. Drives the "fix the chain" suggestion."""
    grouped: dict[str, dict[str, Any]] = {}
    for task in memory.all_tasks(project_path):
        for attempt in task.attempts:
            if attempt.success:
                continue
            key = attempt.failure_kind.value if attempt.failure_kind else "unknown"
            entry = grouped.setdefault(key, {"kind": key, "count": 0, "providers": set(), "example": ""})
            entry["count"] += 1
            entry["providers"].add(attempt.provider)
            entry["example"] = entry["example"] or (attempt.error or "")[:200]
    rows = sorted(grouped.values(), key=lambda item: -item["count"])[:limit]
    for row in rows:
        row["providers"] = sorted(row["providers"])
    return rows
