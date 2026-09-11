"""Logs and run state: the Live Terminal feed and the studio's own diagnostics.

The feed is a push stream (WebSocket) with this router as the pull fallback, because a client
that reconnects after a laptop sleep needs the history it missed, and a plain GET is the
cheapest way to give it that.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from ...core.events import bus
from ...core.paths import studio_log_file
from ...orchestration import context_engine
from ...runtime import runtime
from ..deps import guarded, require_project

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def recent(
    limit: int = Query(default=200, le=1000),
    type: str = Query(default="", description="Filter by event type, e.g. log or task_paused."),
    agent: str = Query(default=""),
) -> dict[str, Any]:
    """Recent bus events, newest last so the Live Terminal can append directly."""
    types = [item.strip() for item in type.split(",") if item.strip()] or None
    events = bus.recent(limit=limit, types=types)
    if agent:
        events = [
            event
            for event in events
            if (event.get("payload") or {}).get("agent") == agent
            or (event.get("payload") or {}).get("agent_id") == agent
        ]
    return {"events": events, "count": len(events), "stats": bus.stats()}


@router.get("/stream-info")
def stream_info() -> dict[str, Any]:
    """What the client needs to open the WebSocket: path, replay size and current sequence."""
    return {
        "websocket_path": "/ws/events",
        "replay_available": bus.stats()["ring_size"],
        "subscribers": bus.stats()["subscribers"],
        "last_seq": bus.stats()["last_seq"],
        "types": [
            "log",
            "task_created",
            "task_updated",
            "verdict",
            "task_paused",
            "task_resumed",
            "screenshot_added",
            "notification",
            "provider_skipped",
            "provider_failed",
            "provider_tested",
            "model_rotated",
            "run_state",
            "asset_request",
            "milestone",
            "project_created",
            "active_project_changed",
        ],
        "note": "Every event carries a monotonically increasing seq; use it to resume after a reconnect.",
    }


@router.get("/run-state")
def run_state() -> dict[str, Any]:
    return {"run_state": runtime.as_dict(), "recovered": runtime._recovered}


@router.post("/run-state/start")
@guarded("start run")
def start() -> dict[str, Any]:
    return {"run_state": runtime.start()}


@router.post("/run-state/pause")
@guarded("pause run")
def pause() -> dict[str, Any]:
    return {"run_state": runtime.pause()}


@router.post("/run-state/stop")
@guarded("stop run")
def stop() -> dict[str, Any]:
    return {"run_state": runtime.stop(wait=True)}


@router.post("/tick")
@guarded("dispatch once")
def tick() -> dict[str, Any]:
    """One dispatch pass. Useful for a user who wants to step the studio manually."""
    outcome = runtime.tick_once()
    return outcome.as_dict()


@router.get("/context-stats")
@guarded("read context stats")
def context_stats() -> dict[str, Any]:
    """What the ADHD filter is saving, in tokens, per project."""
    context = require_project()
    return context_engine.stats(context.path, context.bus)


@router.get("/failures")
@guarded("read failure patterns")
def failures(limit: int = Query(default=10, le=50)) -> dict[str, Any]:
    context = require_project()
    return {"patterns": context_engine.failure_patterns(context.path, limit=limit)}


@router.get("/file")
def log_file(lines: int = Query(default=300, le=5000)) -> dict[str, Any]:
    """Tail of the on-disk studio log - what survives a crash, unlike the in-memory ring."""
    path: Path = studio_log_file()
    if not path.exists():
        return {"path": str(path), "lines": [], "note": "No log file yet."}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
    except OSError as exc:
        return {"path": str(path), "lines": [], "note": f"Could not read the log: {exc}"}
    return {"path": str(path), "lines": [line.rstrip("\n") for line in content[-lines:]]}
