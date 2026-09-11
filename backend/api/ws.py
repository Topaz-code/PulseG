"""WebSocket event stream.

The dashboard never polls (PROCESS.md rule 4): it opens one socket, renders events as they
arrive, and re-fetches a screen's data only when an event tells it that screen changed.

Two details that make this usable in a real app rather than a demo:

* **Replay.** A client may ask for the last N events on connect, so a page refresh or a laptop
  waking from sleep renders the correct state instead of waiting for the next thing to happen.
* **Heartbeat.** A ping every 20 seconds keeps proxies from closing an idle socket and lets the
  client notice a dead connection quickly.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..core.events import bus
from ..runtime import runtime

log = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

HEARTBEAT_S = 20.0


@router.websocket("/ws/events")
async def events(websocket: WebSocket, replay: int = Query(default=40, ge=0, le=500)) -> None:
    await websocket.accept()
    client = f"{websocket.client.host if websocket.client else 'local'}"
    log.info("Event stream connected (%s), replaying %s event(s)", client, replay)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await websocket.send_json({"type": "ping", "at": _now(), "payload": bus.stats()})

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        await websocket.send_json(
            {
                "type": "hello",
                "payload": {
                    "app": "PulseG Studio",
                    "subscribers": bus.stats()["subscribers"],
                    "last_seq": bus.stats()["last_seq"],
                    "run_state": runtime.as_dict(),
                },
            }
        )
        async for envelope in bus.subscribe(replay=replay):
            await websocket.send_json(envelope)
    except WebSocketDisconnect:
        log.info("Event stream disconnected (%s)", client)
    except Exception as exc:  # pragma: no cover - a broken socket is not an app error
        log.info("Event stream closed (%s): %s", client, exc)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):  # pragma: no cover
            pass


@router.get("/ws/health")
def ws_health() -> dict[str, Any]:
    """For the top bar's connection indicator, without opening a socket."""
    stats = bus.stats()
    return {
        "subscribers": stats["subscribers"],
        "ring_size": stats["ring_size"],
        "last_seq": stats["last_seq"],
        # The UI uses this to decide when the socket counts as stale, so the number lives here
        # rather than being duplicated as a magic constant in the frontend.
        "heartbeat_s": HEARTBEAT_S,
        "ok": True,
    }


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
