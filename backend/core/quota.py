"""Quota tracking and proactive warnings.

The spec asks for a quota meter per key with warnings *before* a hard failure. Free tiers
punish surprises (Firecrawl's 1,000 credits are a one-time grant, Deepgram's $200 likewise,
OpenRouter's daily cap is 50 unless credits were bought), so the tracker keeps rolling
counters per provider+model and compares them against the limits declared in
``providers/specs.py``.

Counters are advisory: they live in ``~/.pulsegstudio/quota.json`` and are reconstructed
from provider rate-limit headers whenever a provider exposes them. Nothing here is used for
billing - it exists to tell the user "your Groq daily budget is 80% spent, the pipeline will
fall back to Mistral soon".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import paths
from .atomic import atomic_write_json, file_lock, read_json
from .events import bus
from .models import ProviderSpec

log = logging.getLogger(__name__)

WARN_RATIO = 0.8  # warn at 80% of the known limit
COOLDOWN_DEFAULT_S = 60.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _minute_key() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M")


def _day_key() -> str:
    return _utcnow().date().isoformat()


@dataclass
class Counter:
    """Rolling counters for one provider (optionally per model)."""

    provider: str
    requests_minute: int = 0
    requests_today: int = 0
    tokens_today: int = 0
    minute_stamp: str = field(default_factory=_minute_key)
    day_stamp: str = field(default_factory=_day_key)
    last_status: str = "ok"
    last_error: str = ""
    cooldown_until: str = ""
    total_requests: int = 0
    total_tokens: int = 0
    updated_at: str = field(default_factory=lambda: _utcnow().isoformat(timespec="seconds"))

    def roll(self) -> None:
        minute, day = _minute_key(), _day_key()
        if self.minute_stamp != minute:
            self.minute_stamp = minute
            self.requests_minute = 0
        if self.day_stamp != day:
            self.day_stamp = day
            self.requests_today = 0
            self.tokens_today = 0

    @property
    def in_cooldown(self) -> bool:
        if not self.cooldown_until:
            return False
        try:
            return datetime.fromisoformat(self.cooldown_until) > _utcnow()
        except ValueError:
            return False

    @property
    def cooldown_remaining_s(self) -> int:
        if not self.cooldown_until:
            return 0
        try:
            delta = datetime.fromisoformat(self.cooldown_until) - _utcnow()
        except ValueError:
            return 0
        return max(0, int(delta.total_seconds()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "requests_minute": self.requests_minute,
            "requests_today": self.requests_today,
            "tokens_today": self.tokens_today,
            "total_requests": self.total_requests,
            "total_tokens": self.total_tokens,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "cooldown_remaining_s": self.cooldown_remaining_s,
            "updated_at": self.updated_at,
        }


class QuotaTracker:
    """Persisted counters plus limit comparison and warning events."""

    def __init__(self, path=None) -> None:
        self.path = path or (paths.studio_home() / "quota.json")
        self._counters: dict[str, Counter] = {}
        self._loaded = False

    # --- persistence ------------------------------------------------------------

    def _load(self) -> None:
        if self._loaded:
            return
        data = read_json(self.path, default={}) or {}
        for provider, payload in (data.get("counters") or {}).items():
            try:
                counter = Counter(**{k: v for k, v in payload.items() if k in Counter.__annotations__})
                counter.provider = provider
                counter.roll()
                self._counters[provider] = counter
            except Exception:  # pragma: no cover - tolerate schema drift
                continue
        self._loaded = True

    def _save(self) -> None:
        payload = {
            "updated_at": _utcnow().isoformat(timespec="seconds"),
            "counters": {pid: c.as_dict() | {"minute_stamp": c.minute_stamp, "day_stamp": c.day_stamp, "cooldown_until": c.cooldown_until} for pid, c in self._counters.items()},
        }
        try:
            with file_lock(self.path):
                atomic_write_json(self.path, payload)
        except Exception as exc:  # pragma: no cover
            log.debug("Could not persist quota counters: %s", exc)

    # --- recording --------------------------------------------------------------

    def counter(self, provider: str) -> Counter:
        self._load()
        counter = self._counters.get(provider)
        if counter is None:
            counter = Counter(provider=provider)
            self._counters[provider] = counter
        counter.roll()
        return counter

    def record_success(self, provider: str, *, tokens_in: int = 0, tokens_out: int = 0) -> None:
        counter = self.counter(provider)
        counter.requests_minute += 1
        counter.requests_today += 1
        counter.total_requests += 1
        counter.tokens_today += tokens_in + tokens_out
        counter.total_tokens += tokens_in + tokens_out
        counter.last_status = "ok"
        counter.cooldown_until = ""
        counter.updated_at = _utcnow().isoformat(timespec="seconds")
        self._save()

    def record_failure(self, provider: str, kind: str, message: str = "", retry_after: float | None = None) -> None:
        counter = self.counter(provider)
        counter.last_status = kind
        counter.last_error = message[:300]
        counter.updated_at = _utcnow().isoformat(timespec="seconds")
        if kind in ("rate_limited", "quota_exhausted"):
            seconds = retry_after or COOLDOWN_DEFAULT_S
            counter.cooldown_until = (_utcnow() + timedelta(seconds=seconds)).isoformat(timespec="seconds")
        self._save()

    def update_from_headers(self, provider: str, headers: dict[str, str]) -> None:
        """Trust the provider's own numbers when it exposes them (Groq, OpenRouter do)."""
        counter = self.counter(provider)
        lowered = {k.lower(): v for k, v in (headers or {}).items()}
        remaining_day = lowered.get("x-ratelimit-remaining-requests")
        remaining_min = lowered.get("x-ratelimit-remaining-requests-minute")
        if remaining_min and str(remaining_min).isdigit():
            counter.requests_minute = max(0, 30 - int(remaining_min))
        if remaining_day and str(remaining_day).isdigit():
            counter.requests_today = max(counter.requests_today, 0)
            counter.last_error = f"remaining today: {remaining_day} (reported by provider)"
        self._save()

    # --- reporting --------------------------------------------------------------

    def usage_for(self, spec: ProviderSpec) -> dict[str, Any]:
        counter = self.counter(spec.id)
        limits = spec.limits or {}
        rpd = limits.get("rpd") or limits.get("rpd_small") or limits.get("rpd_large")
        rpm = limits.get("rpm")
        day_ratio = (counter.requests_today / rpd) if rpd else None
        minute_ratio = (counter.requests_minute / rpm) if rpm else None
        ratio = max([r for r in (day_ratio, minute_ratio) if r is not None], default=None)
        return {
            "provider": spec.id,
            "requests_today": counter.requests_today,
            "requests_minute": counter.requests_minute,
            "tokens_today": counter.tokens_today,
            "limit_day": rpd,
            "limit_minute": rpm,
            "limit_kind": "one-time credit" if limits.get("credits_once") else ("credit" if limits.get("credit_usd") else "requests"),
            "ratio": round(ratio, 3) if ratio is not None else None,
            "percent": int(round((ratio or 0) * 100)),
            "free_forever": spec.id in _free_forever(),
            "in_cooldown": counter.in_cooldown,
            "cooldown_remaining_s": counter.cooldown_remaining_s,
            "last_status": counter.last_status,
            "last_error": counter.last_error,
            "verified": spec.verified,
        }

    def warn_if_needed(self, spec: ProviderSpec) -> None:
        """Emit a proactive warning event so the UI can nag before the wall is hit."""
        usage = self.usage_for(spec)
        ratio = usage.get("ratio")
        if ratio is None:
            return
        if ratio >= 1.0:
            bus.publish(
                "quota_exhausted",
                {
                    "provider": spec.id,
                    "name": spec.name,
                    "message": f"{spec.name} has used its free allowance for this window. "
                    "Tasks will fall back along their chain; nothing will be lost.",
                    "usage": usage,
                },
            )
        elif ratio >= WARN_RATIO:
            bus.publish(
                "quota_warning",
                {
                    "provider": spec.id,
                    "name": spec.name,
                    "message": f"{spec.name} is at {usage['percent']}% of its free limit "
                    f"({usage['requests_today']}/{usage['limit_day']} requests today). "
                    "Consider spreading work to a fallback provider.",
                    "usage": usage,
                },
            )

    def aggregate_percent(self, specs: list[ProviderSpec]) -> int:
        """Single number for the command bar's 'Quota' chip."""
        ratios = []
        for spec in specs:
            usage = self.usage_for(spec)
            if usage.get("ratio") is not None:
                ratios.append(min(1.0, float(usage["ratio"])))
        if not ratios:
            return 0
        used = sum(ratios) / len(ratios)
        return max(0, min(100, int(round((1 - used) * 100))))

    def snapshot(self, specs: list[ProviderSpec]) -> list[dict[str, Any]]:
        return [self.usage_for(spec) for spec in specs]

    def reset(self, provider: str | None = None) -> None:
        self._load()
        if provider:
            self._counters.pop(provider, None)
        else:
            self._counters.clear()
        self._save()


def _free_forever() -> set[str]:
    from ..providers.specs import FREE_FOREVER

    return FREE_FOREVER


#: Process-wide tracker.
quota = QuotaTracker()
