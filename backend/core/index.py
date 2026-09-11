"""SQLite index: a fast local cache for dashboard queries.

**Not the source of truth.** Project files are (PROCESS.md rule 1). The index exists so the
task board, search and history views do not have to parse every JSON file on every request,
and it can be deleted at any time: ``rebuild()`` reconstructs it from the files. If the two
ever disagree, the files win and the index is refreshed - there is deliberately no code path
that writes project state from the index.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from . import paths
from .models import KnowledgeEntry, Task

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT,
    path TEXT,
    status TEXT,
    phase INTEGER,
    mode TEXT,
    last_opened TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    project_id TEXT,
    phase INTEGER,
    assigned_to TEXT,
    created_by TEXT,
    status TEXT,
    kind TEXT,
    title TEXT,
    instruction TEXT,
    retry_count INTEGER,
    decline_count INTEGER,
    provider_used TEXT,
    model_used TEXT,
    auditor_verdict TEXT,
    auditor_score INTEGER,
    human_decision TEXT,
    created_at TEXT,
    updated_at TEXT,
    screenshots INTEGER,
    artifacts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT,
    project_id TEXT,
    status TEXT,
    current_task TEXT,
    tasks_completed INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    updated_at TEXT,
    PRIMARY KEY (agent_id, project_id)
);

CREATE TABLE IF NOT EXISTS knowledge (
    entry_id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT,
    source_url TEXT,
    source_type TEXT,
    trust TEXT,
    summary TEXT,
    tags TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT,
    type TEXT,
    project_id TEXT,
    task_id TEXT,
    agent TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Index:
    """Connection-per-call SQLite wrapper. Cheap, and avoids cross-thread handle issues."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.sqlite_file()
        self._ready = False

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            if not self._ready:
                connection.executescript(SCHEMA)
                connection.commit()
                self._ready = True
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        finally:
            connection.close()

    # --- writes -----------------------------------------------------------------

    def upsert_project(self, record: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects (project_id, name, path, status, phase, mode, last_opened)
                VALUES (:project_id, :name, :path, :status, :phase, :mode, :last_opened)
                ON CONFLICT(project_id) DO UPDATE SET
                    name=excluded.name, path=excluded.path, status=excluded.status,
                    phase=excluded.phase, mode=excluded.mode, last_opened=excluded.last_opened
                """,
                {
                    "project_id": record.get("project_id", ""),
                    "name": record.get("name", ""),
                    "path": record.get("path", ""),
                    "status": record.get("status", ""),
                    "phase": int(record.get("phase") or 0),
                    "mode": record.get("mode", "fresh"),
                    "last_opened": record.get("last_opened", _utcnow()),
                },
            )

    def delete_project(self, project_id: str) -> None:
        with self.connect() as connection:
            for table in ("tasks", "agents", "knowledge", "projects"):
                connection.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))

    def upsert_task(self, task: Task) -> None:
        verdict = task.auditor_verdict
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, project_id, phase, assigned_to, created_by, status, kind, title,
                    instruction, retry_count, decline_count, provider_used, model_used,
                    auditor_verdict, auditor_score, human_decision, created_at, updated_at,
                    screenshots, artifacts
                ) VALUES (
                    :task_id, :project_id, :phase, :assigned_to, :created_by, :status, :kind,
                    :title, :instruction, :retry_count, :decline_count, :provider_used,
                    :model_used, :auditor_verdict, :auditor_score, :human_decision,
                    :created_at, :updated_at, :screenshots, :artifacts
                )
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status, phase=excluded.phase, assigned_to=excluded.assigned_to,
                    retry_count=excluded.retry_count, decline_count=excluded.decline_count,
                    provider_used=excluded.provider_used, model_used=excluded.model_used,
                    auditor_verdict=excluded.auditor_verdict, auditor_score=excluded.auditor_score,
                    human_decision=excluded.human_decision, updated_at=excluded.updated_at,
                    title=excluded.title, instruction=excluded.instruction,
                    screenshots=excluded.screenshots, artifacts=excluded.artifacts
                """,
                {
                    "task_id": task.task_id,
                    "project_id": task.project_id,
                    "phase": task.phase,
                    "assigned_to": task.assigned_to,
                    "created_by": task.created_by,
                    "status": task.status.value,
                    "kind": task.kind,
                    "title": task.title,
                    "instruction": task.instruction[:2000],
                    "retry_count": task.retry_count,
                    "decline_count": task.decline_count,
                    "provider_used": task.provider_used,
                    "model_used": task.model_used,
                    "auditor_verdict": verdict.verdict if verdict else None,
                    "auditor_score": verdict.score if verdict else None,
                    "human_decision": task.human_decision.value if task.human_decision else None,
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                    "screenshots": len(task.screenshots),
                    "artifacts": len(task.artifacts),
                },
            )

    def upsert_tasks(self, tasks: Iterable[Task]) -> None:
        for task in tasks:
            self.upsert_task(task)

    def upsert_agent_state(self, project_id: str, agent_id: str, state: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agents (agent_id, project_id, status, current_task, tasks_completed,
                                    tokens_in, tokens_out, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, project_id) DO UPDATE SET
                    status=excluded.status, current_task=excluded.current_task,
                    tasks_completed=excluded.tasks_completed, tokens_in=excluded.tokens_in,
                    tokens_out=excluded.tokens_out, updated_at=excluded.updated_at
                """,
                (
                    agent_id,
                    project_id,
                    state.get("status", "idle"),
                    state.get("current_task"),
                    int(state.get("tasks_completed") or 0),
                    int(state.get("tokens_in") or 0),
                    int(state.get("tokens_out") or 0),
                    state.get("last_active", _utcnow()),
                ),
            )

    def upsert_knowledge(self, project_id: str, entries: Sequence[KnowledgeEntry]) -> None:
        with self.connect() as connection:
            for entry in entries:
                connection.execute(
                    """
                    INSERT INTO knowledge (entry_id, project_id, title, source_url, source_type,
                                           trust, summary, tags, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entry_id) DO UPDATE SET
                        title=excluded.title, summary=excluded.summary, tags=excluded.tags
                    """,
                    (
                        entry.entry_id,
                        project_id,
                        entry.title,
                        entry.source_url,
                        entry.source_type,
                        entry.trust,
                        entry.summary,
                        ",".join(entry.tags),
                        entry.created_at,
                    ),
                )

    def record_event(self, event: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO events (at, type, project_id, task_id, agent, message) VALUES (?,?,?,?,?,?)",
                (
                    event.get("at", _utcnow()),
                    event.get("type", ""),
                    event.get("project_id", ""),
                    event.get("task_id", ""),
                    event.get("agent", ""),
                    str(event.get("message", ""))[:500],
                ),
            )

    # --- reads ------------------------------------------------------------------

    def task_counts(self, project_id: str = "") -> dict[str, int]:
        query = "SELECT status, COUNT(*) AS n FROM tasks"
        params: tuple[Any, ...] = ()
        if project_id:
            query += " WHERE project_id = ?"
            params = (project_id,)
        query += " GROUP BY status"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def search(self, text: str, project_id: str = "", limit: int = 40) -> list[dict[str, Any]]:
        """Substring search over tasks and knowledge. FTS is a nice-to-have, not a need."""
        like = f"%{text}%"
        results: list[dict[str, Any]] = []
        with self.connect() as connection:
            task_rows = connection.execute(
                """
                SELECT 'task' AS kind, task_id AS id, title, instruction AS body, status
                FROM tasks
                WHERE (title LIKE ? OR instruction LIKE ?)
                  AND (? = '' OR project_id = ?)
                LIMIT ?
                """,
                (like, like, project_id, project_id, limit),
            ).fetchall()
            results.extend(dict(row) for row in task_rows)
            knowledge_rows = connection.execute(
                """
                SELECT 'knowledge' AS kind, entry_id AS id, title, summary AS body, trust AS status
                FROM knowledge
                WHERE (title LIKE ? OR summary LIKE ?)
                  AND (? = '' OR project_id = ?)
                LIMIT ?
                """,
                (like, like, project_id, project_id, limit),
            ).fetchall()
            results.extend(dict(row) for row in knowledge_rows)
        return results[:limit]

    def history(self, project_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE (? = '' OR project_id = ?) ORDER BY id DESC LIMIT ?",
                (project_id, project_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"exists": False, "path": str(self.path)}
        with self.connect() as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                for table in ("projects", "tasks", "agents", "knowledge", "events")
            }
        return {
            "exists": True,
            "path": str(self.path),
            "size_bytes": self.path.stat().st_size,
            "counts": counts,
            "note": "Cache only. Project files are the source of truth; rebuild any time.",
        }

    # --- maintenance ------------------------------------------------------------

    def rebuild(self, projects: Sequence[dict[str, Any]]) -> dict[str, int]:
        """Recreate the whole index from the project files on disk."""
        from ..orchestration import memory

        self.reset()
        counters = {"projects": 0, "tasks": 0, "knowledge": 0}
        for record in projects:
            path = Path(record.get("path", ""))
            if not path.exists():
                continue
            self.upsert_project(record)
            counters["projects"] += 1
            tasks = memory.all_tasks(path)
            self.upsert_tasks(tasks)
            counters["tasks"] += len(tasks)
            entries = memory.read_knowledge(path)
            if entries:
                self.upsert_knowledge(record.get("project_id", ""), entries)
                counters["knowledge"] += len(entries)
            for agent_id, state in memory.read_agent_states(path).items():
                self.upsert_agent_state(record.get("project_id", ""), agent_id, state.model_dump())
        return counters

    def reset(self) -> None:
        if self.path.exists():
            with self.connect() as connection:
                for table in ("projects", "tasks", "agents", "knowledge", "events"):
                    connection.execute(f"DELETE FROM {table}")


#: Process-wide index.
index = Index()
