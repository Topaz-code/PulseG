"""Notification service: Telegram and Windows toasts, on real triggers only (spec G).

Four things fire a notification, and nothing else does:

* a task reached **Needs Your Review** - someone has to decide,
* a task hit **Needs Intervention** - every fallback is exhausted and the pipeline is paused,
* the **pipeline stalled** - nothing is running and nothing is waiting on the human,
* a **phase completed** - a milestone worth knowing about.

Design rules that came from using this class of tool:

* A notification must never be able to block the pipeline. Delivery failures are recorded,
  published to the UI and returned, but they never raise into the caller.
* Every notification is written to a local log first, so a Telegram outage or a machine with
  toasts disabled still leaves a complete history in the app.
* Nothing sensitive goes out: task ids, titles and counts. No instructions, no code, no keys.
* Quiet hours are respected for delivery, but the notification is still recorded - the human
  sees it when they open the app.
"""
from __future__ import annotations

import logging
import platform
import re
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any

from ..core.atomic import read_json, write_text_if_changed
from ..core.config import load_config, update_config
from ..core.events import bus
from ..core.models import NotificationEvent
from ..core.paths import studio_home
from ..core.vault import Vault

log = logging.getLogger(__name__)

LOG_FILE = "notifications.json"
MAX_STORED = 300
TELEGRAM_PROVIDER = "telegram"

#: Never let a title or body carry anything that could leak a key or a code block.
_REDACTIONS = (
    (re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"), "[redacted-key]"),
    (re.compile(r"(gsk_[A-Za-z0-9_\-]{8,})"), "[redacted-key]"),
    (re.compile(r"(nvapi-[A-Za-z0-9_\-]{8,})"), "[redacted-key]"),
    (re.compile(r"(hf_[A-Za-z0-9_\-]{8,})"), "[redacted-key]"),
    (re.compile(r"(\d{6,10}:[A-Za-z0-9_\-]{20,})"), "[redacted-token]"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_path() -> Path:
    return studio_home() / LOG_FILE


def _scrub(text: str) -> str:
    cleaned = text or ""
    for pattern, replacement in _REDACTIONS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned[:1500]


# --- the log ----------------------------------------------------------------------------


def read_log(limit: int = 100, *, unread_only: bool = False) -> list[dict[str, Any]]:
    payload = read_json(_log_path(), default=[])
    events = payload if isinstance(payload, list) else []
    if unread_only:
        events = [event for event in events if not event.get("read")]
    return events[-limit:][::-1]


def _append_log(event: NotificationEvent) -> None:
    payload = read_json(_log_path(), default=[])
    events = payload if isinstance(payload, list) else []
    events.append(event.model_dump(mode="json"))
    write_text_if_changed(_log_path(), _dump(events[-MAX_STORED:]))


def _dump(events: list[dict[str, Any]]) -> str:
    import json

    return json.dumps(events, indent=1, ensure_ascii=False) + "\n"


def mark_read(event_id: str = "") -> int:
    """Mark one notification (or all of them) as read."""
    payload = read_json(_log_path(), default=[])
    events = payload if isinstance(payload, list) else []
    changed = 0
    for event in events:
        if event.get("read"):
            continue
        if not event_id or event.get("event_id") == event_id:
            event["read"] = True
            changed += 1
    if changed:
        write_text_if_changed(_log_path(), _dump(events))
        bus.publish("notifications_read", {"event_id": event_id, "count": changed})
    return changed


def unread_count() -> int:
    return sum(1 for event in read_log(limit=MAX_STORED) if not event.get("read"))


def clear_log() -> int:
    payload = read_json(_log_path(), default=[])
    count = len(payload) if isinstance(payload, list) else 0
    write_text_if_changed(_log_path(), _dump([]))
    return count


# --- quiet hours --------------------------------------------------------------------------


def in_quiet_hours(window: str, *, now: datetime | None = None) -> bool:
    """``"22:00-07:00"`` (or empty) -> is delivery suppressed right now?"""
    if not window or "-" not in window:
        return False
    try:
        start_text, end_text = window.split("-", 1)
        start = time.fromisoformat(start_text.strip())
        end = time.fromisoformat(end_text.strip())
    except ValueError:
        return False
    current = (now or datetime.now()).time()
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end  # window crosses midnight


# --- delivery channels ----------------------------------------------------------------------


def _telegram_ready() -> tuple[bool, str]:
    config = load_config()
    settings = config.notifications
    if not settings.telegram_enabled:
        return False, "Telegram notifications are turned off in Settings."
    if not Vault().has(TELEGRAM_PROVIDER):
        return False, "No Telegram bot token saved."
    if not settings.telegram_chat_id:
        return False, (
            "No chat id saved. Send any message to your bot, then press 'Detect chat id' in "
            "Settings."
        )
    return True, ""


def send_telegram(message_title: str, message_body: str) -> tuple[bool, str]:
    """Send one message. Returns ``(delivered, detail)``; never raises."""
    ready, reason = _telegram_ready()
    if not ready:
        return False, reason
    try:
        from ..providers.adapters import adapter_class_for
        from ..providers.specs import PROVIDERS

        spec = PROVIDERS[TELEGRAM_PROVIDER]
        adapter_cls = adapter_class_for(TELEGRAM_PROVIDER)
        adapter = adapter_cls(
            spec=spec,
            api_key=Vault().get(TELEGRAM_PROVIDER) or "",
            base_url=spec.base_url,
        )
        chat_id = load_config().notifications.telegram_chat_id
        ok = adapter.notify(message_title, message_body, chat_id=chat_id)
        return bool(ok), "sent" if ok else "Telegram rejected the message."
    except Exception as exc:
        log.info("Telegram delivery failed: %s", exc)
        return False, str(exc)[:300]


def detect_telegram_chat_id() -> dict[str, Any]:
    """Read the chat id from the bot's recent updates, so the user never hunts for it."""
    if not Vault().has(TELEGRAM_PROVIDER):
        return {"ok": False, "detail": "Save a bot token first."}
    try:
        from ..providers.adapters import adapter_class_for
        from ..providers.specs import PROVIDERS

        spec = PROVIDERS[TELEGRAM_PROVIDER]
        adapter = adapter_class_for(TELEGRAM_PROVIDER)(
            spec=spec, api_key=Vault().get(TELEGRAM_PROVIDER) or "", base_url=spec.base_url
        )
        chat_id = adapter.detect_chat_id()
        if not chat_id:
            return {
                "ok": False,
                "detail": (
                    "No messages found. Open Telegram, send your bot any message, then try again."
                ),
            }
        update_config(notifications={"telegram_chat_id": chat_id, "telegram_enabled": True})
        return {"ok": True, "chat_id": chat_id, "detail": "Chat id saved. Send a test message."}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:300]}


def windows_toast(title: str, body: str) -> tuple[bool, str]:
    """Native Windows toast. Also used by the Tauri shell, which handles it natively.

    On non-Windows platforms this is a no-op that says so, rather than an error: the log entry
    is still written and the UI still shows the notification.
    """
    if platform.system() != "Windows":
        return False, "Native toasts are Windows-only; the in-app list still has this."
    try:
        from winotify import Notification, audio  # type: ignore[import-not-found]

        toast = Notification(app_id="PulseG Studio", title=title[:120], msg=body[:400])
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return True, "shown"
    except ImportError:
        return False, "winotify is not installed in this build."
    except Exception as exc:
        log.info("Windows toast failed: %s", exc)
        return False, str(exc)[:300]


def tauri_notify(title: str, body: str) -> bool:
    """Ask the Tauri shell to show a native notification, when we are running inside it.

    The sidecar cannot call the shell directly, so it publishes on the bus and the shell's
    event bridge picks it up. In a browser or a test this is simply information.
    """
    bus.publish("native_notification", {"title": title[:120], "body": body[:400]})
    return True


# --- the public entry point ------------------------------------------------------------------


def notify(
    kind: str,
    title: str,
    body: str = "",
    *,
    project_path: Path | None = None,
    project_id: str = "",
    task_id: str = "",
    force: bool = False,
) -> NotificationEvent:
    """Record and deliver a notification. Never raises, never blocks the pipeline."""
    from ..core.config import new_id

    clean_title = _scrub(title)
    clean_body = _scrub(body)
    event = NotificationEvent(
        event_id=new_id("ntf"),
        kind=kind if kind in {
            "needs_review", "needs_intervention", "pipeline_stalled", "phase_complete",
            "test_connection", "system",
        } else "system",
        title=clean_title,
        body=clean_body,
        project_id=project_id or _project_id_for(project_path),
        task_id=task_id,
    )

    config = load_config()
    settings = config.notifications
    wanted = force or kind in (settings.notify_on or [])
    quiet = in_quiet_hours(settings.quiet_hours)

    delivered: dict[str, bool] = {"log": True}
    details: list[str] = []

    if not wanted:
        details.append(f"Not delivered: '{kind}' is turned off in Settings > Notifications.")
    elif quiet:
        details.append(
            f"Not delivered now: quiet hours ({settings.quiet_hours}) are active. It is waiting "
            "for you in the app."
        )
    else:
        if settings.telegram_enabled:
            ok, detail = send_telegram(clean_title, clean_body)
            delivered["telegram"] = ok
            details.append(f"Telegram: {detail}")
        if settings.windows_toasts:
            ok, detail = windows_toast(clean_title, clean_body)
            delivered["windows"] = ok
            if ok:
                details.append("Windows toast: shown.")
            elif platform.system() == "Windows":
                details.append(f"Windows toast: {detail}")
            # On other platforms the in-app list is the delivery channel; not worth a line.

    event.delivered = delivered
    event.delivery_detail = details
    _append_log(event)
    bus.publish(
        "notification",
        {
            **event.model_dump(mode="json"),
            "delivery_detail": details,
        },
    )
    log.info("Notification [%s] %s - %s", kind, clean_title, "; ".join(details) or "in-app only")
    return event


def _project_id_for(project_path: Path | None) -> str:
    if project_path is None:
        return ""
    try:
        from ..orchestration import projects

        for record in projects.list_projects():
            if Path(record.get("path", "")).resolve() == Path(project_path).resolve():
                return str(record.get("project_id", ""))
    except Exception:  # pragma: no cover - notification must not depend on the project layer
        return ""
    return ""


# --- the four real triggers --------------------------------------------------------------------


def notify_needs_review(task: Any, *, project_path: Path | None = None) -> NotificationEvent:
    verdict = getattr(task, "auditor_verdict", None)
    detail = ""
    if verdict is not None:
        detail = f"Auditor: {verdict.verdict} ({verdict.score}/10). "
    detail += "Approve, reject with a note, or override the Auditor."
    return notify(
        "needs_review",
        f"{task.task_id} is waiting for your review",
        f"{getattr(task, 'title', '')}\n\n{detail}",
        project_path=project_path,
        task_id=getattr(task, "task_id", ""),
    )


def notify_needs_intervention(task: Any, reason: str, *, project_path: Path | None = None) -> NotificationEvent:
    return notify(
        "needs_intervention",
        f"{task.task_id} needs your intervention",
        "Every provider in this agent's chain failed, so the task is paused - not lost.\n\n"
        f"{reason[:600]}\n\nAdd a key, edit the fallback chain, or press Retry.",
        project_path=project_path,
        task_id=getattr(task, "task_id", ""),
    )


def notify_pipeline_stalled(detail: str, *, project_path: Path | None = None) -> NotificationEvent:
    return notify(
        "pipeline_stalled",
        "The build pipeline has stalled",
        f"{detail}\n\nNothing is running and nothing is waiting on you. Open the Task Board to "
        "see what is blocking the queue.",
        project_path=project_path,
    )


def notify_phase_complete(phase: int, label: str, *, project_path: Path | None = None) -> NotificationEvent:
    return notify(
        "phase_complete",
        f"Phase {phase} complete: {label}",
        "Every task in this phase is approved and committed. Approve the milestone to regenerate "
        "the web export and move on.",
        project_path=project_path,
    )


def notify_system(title: str, body: str = "") -> NotificationEvent:
    return notify("system", title, body, force=True)


def test_all() -> dict[str, Any]:
    """Settings > Notifications 'Send test' button: one message per enabled channel."""
    results: dict[str, Any] = {}
    ok, detail = send_telegram("PulseG Studio test", "If you can read this, Telegram delivery works.")
    results["telegram"] = {"ok": ok, "detail": detail}
    results["telegram_ready"] = _telegram_ready()
    ok, detail = windows_toast("PulseG Studio test", "If you can see this, toasts work.")
    results["windows"] = {"ok": ok, "detail": detail}
    notify("test_connection", "Test notification", "Sent from Settings.", force=True)
    results["logged"] = True
    return results


def status() -> dict[str, Any]:
    """What Settings renders: channel readiness, counts, quiet hours."""
    config = load_config()
    ready, reason = _telegram_ready()
    return {
        "telegram": {
            "enabled": config.notifications.telegram_enabled,
            "has_token": Vault().has(TELEGRAM_PROVIDER),
            "chat_id": config.notifications.telegram_chat_id,
            "ready": ready,
            "detail": reason,
            "bot_username": _bot_username(),
        },
        "windows_toasts": {
            "enabled": config.notifications.windows_toasts,
            "platform_supported": platform.system() == "Windows",
        },
        "notify_on": config.notifications.notify_on,
        "quiet_hours": config.notifications.quiet_hours,
        "quiet_now": in_quiet_hours(config.notifications.quiet_hours),
        "unread": unread_count(),
        "recent": read_log(limit=20),
    }


def _bot_username() -> str:
    try:
        from ..providers.adapters import adapter_class_for
        from ..providers.specs import PROVIDERS

        if not Vault().has(TELEGRAM_PROVIDER):
            return ""
        spec = PROVIDERS[TELEGRAM_PROVIDER]
        adapter = adapter_class_for(TELEGRAM_PROVIDER)(
            spec=spec, api_key=Vault().get(TELEGRAM_PROVIDER) or "", base_url=spec.base_url
        )
        test = adapter.test_connection()
        if test.status == "valid" and "@" in (test.detail or ""):
            match = re.search(r"@([A-Za-z0-9_]+)", test.detail)
            return match.group(1) if match else ""
    except Exception:  # pragma: no cover - display only
        return ""
    return ""


__all__ = [
    "clear_log",
    "detect_telegram_chat_id",
    "in_quiet_hours",
    "mark_read",
    "notify",
    "notify_needs_intervention",
    "notify_needs_review",
    "notify_phase_complete",
    "notify_pipeline_stalled",
    "notify_system",
    "read_log",
    "send_telegram",
    "status",
    "test_all",
    "unread_count",
    "windows_toast",
]
