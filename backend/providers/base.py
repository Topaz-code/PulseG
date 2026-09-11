"""Provider adapter contract, result types and error classification.

The router (``router.py``) is deliberately provider-agnostic: it only knows this interface.
That is what makes "add a provider without a code change" true in practice - a new
OpenAI-compatible provider is a config entry, and a genuinely different protocol needs one
adapter class.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.models import FailureKind


class ProviderError(Exception):
    """Normalised provider failure. Carries enough detail for the router to decide.

    ``kind`` drives the decision: retry the same model, fall back, or pause and ask the
    human. See :class:`backend.core.models.FailureKind`.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: FailureKind = FailureKind.UNKNOWN,
        provider: str = "",
        model: str = "",
        status_code: int | None = None,
        retry_after: float | None = None,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.retry_after = retry_after
        self.body = body[:2000]

    @property
    def human_actionable(self) -> bool:
        return self.kind.human_actionable

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "kind": self.kind.value,
            "provider": self.provider,
            "model": self.model,
            "status_code": self.status_code,
            "retry_after": self.retry_after,
        }


def classify_http_error(status: int, body: str = "") -> FailureKind:
    """Map an HTTP status (plus body hints) onto a :class:`FailureKind`.

    Free tiers fail in specific ways, and treating a 429 the same as a 401 wastes the
    user's day: a 429 means "wait", a 401/403 means "your key is wrong, tell the human".
    """
    lowered = (body or "").lower()
    if status == 401:
        return FailureKind.INVALID_KEY
    if status == 402:
        return FailureKind.QUOTA_EXHAUSTED
    if status == 403:
        if any(token in lowered for token in ("quota", "exceeded", "credit", "billing", "insufficient")):
            return FailureKind.QUOTA_EXHAUSTED
        return FailureKind.INVALID_KEY
    if status == 404:
        return FailureKind.BAD_REQUEST
    if status == 408:
        return FailureKind.TIMEOUT
    if status == 413:
        return FailureKind.BAD_REQUEST
    if status == 422:
        return FailureKind.BAD_REQUEST
    if status == 429:
        if any(token in lowered for token in ("quota", "daily limit", "insufficient")):
            return FailureKind.QUOTA_EXHAUSTED
        return FailureKind.RATE_LIMITED
    if 500 <= status < 600:
        return FailureKind.UNAVAILABLE
    if status == 400 and any(token in lowered for token in ("content", "safety", "blocked", "filter")):
        return FailureKind.CONTENT_FILTERED
    if 400 <= status < 500:
        return FailureKind.BAD_REQUEST
    return FailureKind.UNKNOWN


def retry_after_from(headers: dict[str, str] | Any) -> float | None:
    """Parse ``Retry-After`` (seconds) or ``x-ratelimit-reset-*`` hints when present."""
    get = headers.get if hasattr(headers, "get") else (lambda *_a, **_k: None)
    for header in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        raw = get(header)
        if not raw:
            continue
        text = str(raw).strip()
        try:
            if text.endswith("ms"):
                return float(text[:-2]) / 1000.0
            if text.endswith("s"):
                return float(text[:-1])
            if text.endswith("m"):
                return float(text[:-1]) * 60
            return float(text)
        except ValueError:
            # e.g. "1m30s" or an HTTP date; fall back to a conservative default
            import re

            total = 0.0
            for value, unit in re.findall(r"(\d+(?:\.\d+)?)([hms])", text):
                total += float(value) * {"h": 3600, "m": 60, "s": 1}[unit]
            return total or None
    return None


# --- result types ---------------------------------------------------------------------


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    parsed: dict[str, Any] | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.text or "").strip()


@dataclass
class ImageResult:
    path: str
    provider: str
    prompt: str
    width: int = 0
    height: int = 0
    seed: int | None = None
    watermarked: bool = False
    job_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptResult:
    text: str
    provider: str
    language: str = ""
    duration_s: float = 0.0
    segments: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class WebDocument:
    url: str
    markdown: str
    title: str = ""
    provider: str = "firecrawl"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AudioHit:
    title: str
    url: str
    preview_url: str
    licence: str
    author: str = ""
    duration_s: float = 0.0
    source: str = "freesound"
    tags: list[str] = field(default_factory=list)


@dataclass
class VideoHit:
    url: str
    title: str
    channel: str = ""
    duration_s: float = 0.0
    has_subtitles: bool = False
    source: str = "youtube"


@dataclass
class ConnectionTest:
    status: str  # valid | invalid | rate_limited | unreachable
    detail: str = ""
    latency_ms: int = 0
    quota: dict[str, Any] = field(default_factory=dict)


class ProviderAdapter(ABC):
    """Base class. Subclasses override only the capabilities they support.

    Unsupported calls raise :class:`ProviderError` with ``BAD_REQUEST``, which the router
    treats as "this provider cannot do this task" and moves to the next in the chain.
    """

    provider_id: str = "abstract"
    supports_chat: bool = False
    supports_vision: bool = False
    supports_image: bool = False
    supports_stt: bool = False
    supports_scrape: bool = False
    supports_audio_search: bool = False
    supports_video_search: bool = False
    supports_notify: bool = False

    def __init__(self, api_key: str | None = None, base_url: str = "", **options: Any) -> None:
        self.api_key = api_key or ""
        self.base_url = base_url or ""
        self.options = options

    # --- capabilities -----------------------------------------------------------

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        timeout: float = 180.0,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        raise self._unsupported("chat")

    def vision(
        self,
        prompt: str,
        images: Sequence[str],
        model: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        timeout: float = 180.0,
    ) -> LLMResponse:
        raise self._unsupported("vision")

    def image(
        self,
        prompt: str,
        out_path: str,
        *,
        model: str = "",
        width: int = 512,
        height: int = 512,
        negative_prompt: str = "",
        reference_image: str = "",
        denoise: float = 0.55,
        timeout: float = 600.0,
    ) -> ImageResult:
        raise self._unsupported("image")

    def transcribe(
        self,
        audio_path: str,
        model: str = "",
        *,
        language: str = "",
        timeout: float = 600.0,
    ) -> TranscriptResult:
        raise self._unsupported("transcribe")

    def scrape(self, url: str, *, timeout: float = 90.0) -> WebDocument:
        raise self._unsupported("scrape")

    def search_audio(self, query: str, *, limit: int = 10, licence: str = "cc0") -> list[AudioHit]:
        raise self._unsupported("search_audio")

    def search_video(self, query: str, *, limit: int = 8) -> list[VideoHit]:
        raise self._unsupported("search_video")

    def subtitles(self, url: str, *, language: str = "en") -> TranscriptResult:
        raise self._unsupported("subtitles")

    def notify(self, title: str, body: str, chat_id: str = "") -> bool:
        raise self._unsupported("notify")

    # --- health -----------------------------------------------------------------

    @abstractmethod
    def test_connection(self, model: str = "") -> ConnectionTest:
        """Cheap real call used by the Settings 'Test Connection' button."""

    @property
    def requires_key(self) -> bool:
        return True

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) or not self.requires_key

    def _unsupported(self, capability: str) -> ProviderError:
        return ProviderError(
            f"{self.provider_id} does not support {capability}. The router will move to "
            "the next provider in the chain.",
            kind=FailureKind.BAD_REQUEST,
            provider=self.provider_id,
        )

    # --- helpers ----------------------------------------------------------------

    @staticmethod
    def _timed() -> float:
        return time.perf_counter()

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
