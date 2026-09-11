"""Notifications: Telegram, Windows toasts, and the in-app notification list.

The service is deliberately fail-soft - a notification problem must never stop a build - and
it is the only place that decides whether a given event is worth interrupting a person for.
"""
from __future__ import annotations

from . import service  # noqa: F401

__all__ = ["service"]
