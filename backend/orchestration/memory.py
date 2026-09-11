"""Project memory files: progress.md, gdd.md, task queue, agent states, audit history.

Files are the source of truth (PROCESS.md rule 1). Every writer here goes through
``atomic.py``, and every reader tolerates a missing or mid-write file, because the app must
survive a hard power-off mid-task without losing the queue.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..core.atomic import append_text, atomic_write_json, atomic_write_text, read_json, read_text
from ..core.models import AgentState, AssetRequest, ChatMessage, KnowledgeEntry, Task, Verdict

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- progress.md ----------------------------------------------------------------------


def append_progress(project_path: Path, line: str, *, agent: str = "", task_id: str = "", status: str = "") -> None:
    """Append one timestamped line. Append-only by design: never rewritten in place."""
    stamp = _utcnow()
    prefix = f"- {stamp}"
    if task_id:
        prefix += f" | {task_id}"
    if agent:
        prefix += f" | {agent}"
    if status:
        prefix += f" | {status}"
    body = line.strip().lstrip("-").strip()
    append_text(project_path / "memory" / "progress.md", f"{prefix} | {body}\n")


def progress_tail(project_path: Path, lines: int = 20) -> str:
    text = read_text(project_path / "memory" / "progress.md")
    return "\n".join(text.rstrip().splitlines()[-lines:])


def progress_line_count(project_path: Path) -> int:
    return len(read_text(project_path / "memory" / "progress.md").splitlines())


# --- gdd.md ---------------------------------------------------------------------------


GDD_SECTION_RE = re.compile(r"^(##\s+)([A-Z][A-Z0-9 _/-]*)\s*$", re.MULTILINE)


def read_gdd(project_path: Path) -> str:
    return read_text(project_path / "memory" / "gdd.md")


def gdd_sections(project_path: Path) -> list[str]:
    return [match.group(2).strip() for match in GDD_SECTION_RE.finditer(read_gdd(project_path))]


def write_gdd_section(project_path: Path, section: str, body: str) -> None:
    """Replace exactly one section and leave every other byte untouched.

    Section-scoped writes are what make parallel agents safe on a shared document; a
    whole-file rewrite from two agents would silently drop one of the changes.
    """
    text = read_gdd(project_path)
    header = section.strip().upper()
    pattern = re.compile(
        rf"^(##\s+{re.escape(header)}\s*)$(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if pattern.search(text):
        new_text = pattern.sub(lambda match: f"{match.group(1)}\n{body.strip()}\n\n", text, count=1)
    else:
        new_text = text.rstrip() + f"\n\n## {header}\n{body.strip()}\n"
    atomic_write_text(project_path / "memory" / "gdd.md", new_text)


def gdd_summary(project_path: Path, max_lines: int = 100) -> str:
    text = read_gdd(project_path)
    match = re.search(r"^##\s+SUMMARY\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if not match:
        return "\n".join(text.splitlines()[:max_lines])
    return "\n".join(match.group(1).strip().splitlines()[:max_lines])


def write_gdd(project_path: Path, text: str) -> None:
    atomic_write_text(project_path / "memory" / "gdd.md", text)


# --- task queue -----------------------------------------------------------------------


def queue_path(project_path: Path) -> Path:
    return project_path / "memory" / "task_queue.json"


def completed_path(project_path: Path) -> Path:
    return project_path / "memory" / "completed_tasks.json"


def read_queue(project_path: Path) -> list[Task]:
    raw = read_json(queue_path(project_path), default=[]) or []
    tasks: list[Task] = []
    if isinstance(raw, dict):
        raw = raw.get("tasks", [])
    for entry in raw:
        try:
            tasks.append(Task.model_validate(entry))
        except Exception as exc:  # pragma: no cover - corrupted entry
            log.error("Skipping unreadable task entry in %s: %s", queue_path(project_path), exc)
    return tasks


def write_queue(project_path: Path, tasks: Sequence[Task]) -> None:
    payload = [task.model_dump(mode="json") for task in tasks]
    atomic_write_json(queue_path(project_path), payload)


def read_completed(project_path: Path) -> list[Task]:
    raw = read_json(completed_path(project_path), default=[]) or []
    tasks: list[Task] = []
    if isinstance(raw, dict):
        raw = raw.get("tasks", [])
    for entry in raw:
        try:
            tasks.append(Task.model_validate(entry))
        except Exception:
            continue
    return tasks


def append_completed(project_path: Path, task: Task) -> None:
    tasks = read_completed(project_path)
    tasks.append(task)
    atomic_write_json(completed_path(project_path), [t.model_dump(mode="json") for t in tasks])


def next_task_number(project_path: Path) -> int:
    """Highest existing task number + 1, across both open and completed queues."""
    highest = 0
    for task in list(read_queue(project_path)) + list(read_completed(project_path)):
        match = re.match(r"TASK_(\d+)$", task.task_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def all_tasks(project_path: Path) -> list[Task]:
    return list(read_queue(project_path)) + list(read_completed(project_path))


# --- agent states ---------------------------------------------------------------------


def read_agent_states(project_path: Path) -> dict[str, AgentState]:
    raw = read_json(project_path / "memory" / "agent_states.json", default={}) or {}
    states: dict[str, AgentState] = {}
    for agent_id, payload in raw.items():
        try:
            states[agent_id] = AgentState.model_validate({**payload, "agent_id": agent_id})
        except Exception:
            states[agent_id] = AgentState(agent_id=agent_id)
    return states


def write_agent_states(project_path: Path, states: dict[str, AgentState]) -> None:
    payload = {agent_id: state.model_dump(mode="json") for agent_id, state in states.items()}
    atomic_write_json(project_path / "memory" / "agent_states.json", payload)


def update_agent_state(project_path: Path, agent_id: str, **changes: Any) -> AgentState:
    states = read_agent_states(project_path)
    state = states.get(agent_id) or AgentState(agent_id=agent_id)
    for key, value in changes.items():
        if hasattr(state, key):
            setattr(state, key, value)
    state.last_active = _utcnow()
    states[agent_id] = state
    write_agent_states(project_path, states)
    return state


# --- audit history --------------------------------------------------------------------


def append_audit(project_path: Path, verdict: Verdict, *, agent: str = "", task_id: str = "") -> None:
    history = read_json(project_path / "memory" / "audit_history.json", default=[]) or []
    if not isinstance(history, list):
        history = []
    history.append({**verdict.model_dump(mode="json"), "agent": agent, "task_id": task_id or verdict.task_id})
    # Keep the tail bounded; the full verdict stays on the task itself.
    atomic_write_json(project_path / "memory" / "audit_history.json", history[-500:])


def read_audit_history(project_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    history = read_json(project_path / "memory" / "audit_history.json", default=[]) or []
    return history[-limit:] if isinstance(history, list) else []


# --- crash recovery -------------------------------------------------------------------


def snapshot_path(project_path: Path, agent_id: str) -> Path:
    return project_path / "memory" / "context_snapshots" / f"{agent_id}.json"


def write_snapshot(project_path: Path, agent_id: str, payload: dict[str, Any]) -> None:
    atomic_write_json(
        snapshot_path(project_path, agent_id),
        {**payload, "agent_id": agent_id, "saved_at": _utcnow()},
    )


def read_snapshot(project_path: Path, agent_id: str) -> dict[str, Any]:
    return read_json(snapshot_path(project_path, agent_id), default={}) or {}


def recover_in_flight(project_path: Path) -> list[str]:
    """Find tasks that were IN_PROGRESS when the app died and return them to PENDING.

    Called on backend start. This is the crash-recovery half of zero-abandonment: a task
    interrupted by a power cut is not treated as a failure, it simply becomes queued again
    with a note in its history.
    """
    tasks = read_queue(project_path)
    recovered: list[str] = []
    changed = False
    for task in tasks:
        if task.status.value == "IN_PROGRESS":
            task.status = task.status.PENDING
            task.crash_recovered = True
            task.retry_count = task.retry_count  # unchanged: an interruption is not a retry
            task.errors.append(f"[{_utcnow()}] Interrupted by an app restart; requeued.")
            task.touch()
            recovered.append(task.task_id)
            changed = True
    if changed:
        write_queue(project_path, tasks)
    return recovered


# --- knowledge ------------------------------------------------------------------------


def read_knowledge(project_path: Path) -> list[KnowledgeEntry]:
    raw = read_json(project_path / "knowledge" / "index.json", default=[]) or []
    if isinstance(raw, dict):
        raw = raw.get("entries", [])
    entries: list[KnowledgeEntry] = []
    for item in raw:
        try:
            entries.append(KnowledgeEntry.model_validate(item))
        except Exception:
            continue
    return entries


def add_knowledge(project_path: Path, entry: KnowledgeEntry) -> None:
    entries = read_knowledge(project_path)
    entries = [existing for existing in entries if existing.entry_id != entry.entry_id]
    entries.append(entry)
    atomic_write_json(
        project_path / "knowledge" / "index.json",
        [item.model_dump(mode="json") for item in entries],
    )
    # Raw scraped material lives next to the index and is pruned separately; the digest in
    # the index is what agents see (see skills/adhd_filter.py).
    if entry.markdown_path:
        target = project_path / entry.markdown_path
        target.parent.mkdir(parents=True, exist_ok=True)


def write_knowledge_markdown(project_path: Path, relative_path: str, markdown: str) -> Path:
    target = project_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, markdown)
    return target


# --- asset requests -------------------------------------------------------------------


def read_asset_requests(project_path: Path) -> list[AssetRequest]:
    raw = read_json(project_path / "assets" / "asset_requests.json", default=[]) or []
    if isinstance(raw, dict):
        raw = raw.get("requests", [])
    requests: list[AssetRequest] = []
    for item in raw:
        try:
            requests.append(AssetRequest.model_validate(item))
        except Exception:
            continue
    return requests


def write_asset_requests(project_path: Path, requests: Sequence[AssetRequest]) -> None:
    atomic_write_json(
        project_path / "assets" / "asset_requests.json",
        [item.model_dump(mode="json") for item in requests],
    )


def add_asset_request(project_path: Path, request: AssetRequest) -> AssetRequest:
    requests = read_asset_requests(project_path)
    existing = next(
        (
            item
            for item in requests
            if item.status == "open" and item.name.lower() == request.name.lower()
        ),
        None,
    )
    if existing:
        return existing
    requests.append(request)
    write_asset_requests(project_path, requests)
    return request


def fulfil_asset_request(project_path: Path, request_id: str, *, path: str, by: str = "human") -> bool:
    requests = read_asset_requests(project_path)
    found = False
    for request in requests:
        if request.request_id == request_id:
            request.status = "fulfilled"
            request.fulfilled_path = path
            request.fulfilled_by = by
            found = True
    if found:
        write_asset_requests(project_path, requests)
    return found


# --- planning chat --------------------------------------------------------------------


def read_chat(project_path: Path) -> list[ChatMessage]:
    raw = read_json(project_path / "planning" / "chat.json", default=[]) or []
    if isinstance(raw, dict):
        raw = raw.get("messages", [])
    messages: list[ChatMessage] = []
    for item in raw:
        try:
            messages.append(ChatMessage.model_validate(item))
        except Exception:
            continue
    return messages


def append_chat(project_path: Path, message: ChatMessage) -> None:
    messages = read_chat(project_path)
    messages.append(message)
    atomic_write_json(project_path / "planning" / "chat.json", [m.model_dump(mode="json") for m in messages])


def human_text_corpus(project_path: Path, limit: int = 40) -> list[str]:
    """Genuinely human-authored text, used as the stylometry baseline.

    The source is the planning conversation (the human's own messages) plus any prose the
    human wrote under ``assets/references``. This matters: it lets ``harvard_shape`` compare
    agent output against *this user's* writing rather than a fabricated corpus.
    """
    texts = [
        message.content
        for message in read_chat(project_path)
        if message.role == "human" and len(message.content.split()) > 40
    ]
    references = project_path / "assets" / "references"
    if references.exists():
        for path in sorted(references.glob("*.md"))[:8]:
            body = read_text(path)
            if len(body.split()) > 40:
                texts.append(body)
    return texts[-limit:]


def project_stats(project_path: Path) -> dict[str, Any]:
    tasks = all_tasks(project_path)
    approved = [t for t in tasks if t.status.value == "APPROVED"]
    return {
        "tasks_total": len(tasks),
        "tasks_approved": len(approved),
        "tasks_open": len(tasks) - len(approved),
        "tasks_needing_review": len([t for t in tasks if t.status.value == "NEEDS_HUMAN_REVIEW"]),
        "tasks_paused": len([t for t in tasks if t.status.value == "NEEDS_INTERVENTION"]),
        "progress_lines": progress_line_count(project_path),
        "gdd_sections": gdd_sections(project_path),
        "asset_requests_open": len([r for r in read_asset_requests(project_path) if r.status == "open"]),
        "knowledge_entries": len(read_knowledge(project_path)),
    }
