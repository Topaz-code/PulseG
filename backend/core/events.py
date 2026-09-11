"""In-process event bus + WebSocket fan-out.

Everything the dashboard shows live flows through here: agent status changes, task
transitions, streamed log lines, quota warnings, notifications. The frontend never polls
(PROCESS.md rule 4) - it subscribes to ``/ws`` and re-fetches only when told to.

Design notes
------------
* One asyncio queue per subscriber, with a bounded size and a drop-oldest policy so a slow
  browser tab can never block the pipeline.
* Events are also appended to a ring buffer, so a client that connects late (or reconnects
  after sleep) can request a replay of the last N events and still render the full state.
* Log records are additionally streamed from the Python logging system through
  :func:`install_log_stream`, which is what powers the Logs / Live Terminal view.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable

MAX_QUEUE = 512
RING_SIZE = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EventBus:
    """Async pub/sub with replay. Single instance per process (see :data:`bus`)."""

    def __init__(self, ring_size: int = RING_SIZE) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._seq = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    # --- publish ---------------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the app loop so sync code (agents, git hooks) can publish safely."""
        self._loop = loop

    def publish(self, event_type: str, payload: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        """Publish from anywhere. Safe to call from a worker thread.

        Returns the envelope so callers can log/inspect what they emitted.
        """
        envelope = {
            "seq": next(self._seq),
            "type": event_type,
            "at": _now(),
            "payload": payload or {},
            **({"meta": extra} if extra else {}),
        }
        self._ring.append(envelope)
        loop = self._loop
        if loop is None or loop.is_closed():
            # No running app (tests, CLI use): ring buffer only.
            return envelope
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fanout(envelope)
        else:
            loop.call_soon_threadsafe(self._fanout, envelope)
        return envelope

    def _fanout(self, envelope: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()  # drop oldest
                except asyncio.QueueEmpty:  # pragma: no cover
                    pass
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:  # pragma: no cover
                continue

    # --- subscribe -------------------------------------------------------------

    async def subscribe(self, replay: int = 0) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._subscribers.add(queue)
        try:
            if replay > 0:
                for envelope in list(self._ring)[-replay:]:
                    queue.put_nowait(envelope)
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def recent(self, limit: int = 100, types: Iterable[str] | None = None) -> list[dict[str, Any]]:
        wanted = set(types) if types else None
        events = [e for e in self._ring if wanted is None or e["type"] in wanted]
        return events[-limit:]

    def stats(self) -> dict[str, Any]:
        return {
            "subscribers": self.subscriber_count,
            "ring_size": len(self._ring),
            "last_seq": next(self._seq) - 1,
        }


#: Process-wide bus.
bus = EventBus()


class PublishOnPublish:
    """Mixin used by orchestration services to emit lifecycle events consistently."""

    def emit(self, event_type: str, payload: dict[str, Any] | None = None, **extra: Any) -> None:
        bus.publish(event_type, payload, **extra)


# --- Log streaming --------------------------------------------------------------------


class EventStreamHandler(logging.Handler):
    """Forwards log records to the bus so the Logs view can tail them live."""

    #: chatty libraries whose noise is not useful in the Live Terminal
    QUIET = ("httpx", "httpcore", "urllib3", "watchfiles", "uvicorn.access", "asyncio")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            name = record.name
            if any(name.startswith(prefix) for prefix in self.QUIET) and record.levelno < logging.WARNING:
                return
            message = record.getMessage()
            if record.exc_info:
                message += " | " + logging.Formatter().formatException(record.exc_info)
            bus.publish(
                "log",
                {
                    "level": record.levelname,
                    "logger": name,
                    "message": message[:4000],
                    "agent_id": getattr(record, "agent_id", None),
                    "task_id": getattr(record, "task_id", None),
                    "phase": getattr(record, "phase", None),
                    "at": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                        timespec="milliseconds"
                    ),
                    "ts": record.created,
                },
            )
        except Exception:  # pragma: no cover - a logging handler must never raise
            pass


def install_log_stream(level: int = logging.INFO) -> EventStreamHandler:
    """Attach the bus handler to the root logger exactly once."""
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, EventStreamHandler):
            return handler
    handler = EventStreamHandler()
    handler.setLevel(level)
    root.addHandler(handler)
    return handler


class AgentLogAdapter(logging.LoggerAdapter):
    """Logger that stamps agent_id / task_id onto every record for UI colour-coding."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        extra.update(self.extra)
        return msg, kwargs


def agent_logger(name: str, agent_id: str, **fields: Any) -> AgentLogAdapter:
    return AgentLogAdapter(logging.getLogger(name), {"agent_id": agent_id, **fields})


def timed() -> float:
    return time.perf_counter()
