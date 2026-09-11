"""Provider registry and the fallback router (Pillar B.2).

The router implements the Zero-Abandonment Rule literally:

1. Try the PRIMARY model.
2. On failure / rate-limit / empty response: log it, increment ``retry_count``, move to
   FALLBACK 1. Retryable failures (429 with Retry-After, timeouts, 5xx) are retried once
   per slot with backoff before falling through, because a rate limit is a wait, not a
   broken key.
3. Then FALLBACK 2.
4. When every configured slot is exhausted the router raises :class:`ChainExhausted`. The
   caller moves the task to ``NEEDS_INTERVENTION`` - a *pause*, not a failure state - keeps
   the full attempt history, notifies the human, and resumes automatically once the human
   fixes a key, edits the chain, or clicks Retry.
5. If the Auditor declines the same task three times with the same model, the router
   rotates to the next fallback even though every API call succeeded: the model, not the
   plumbing, is the problem.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.config import ProvidersConfig, env_flag, load_providers
from ..core.events import bus
from ..core.models import AgentDefinition, AttemptRecord, FailureKind, ModelRef, ProviderKind, Task
from ..core.quota import quota
from .adapters import OPENAI_COMPATIBLE, SPECIALISED, adapter_class_for
from .base import (
    AudioHit,
    ConnectionTest,
    ImageResult,
    LLMResponse,
    ProviderAdapter,
    ProviderError,
    TranscriptResult,
    VideoHit,
    WebDocument,
)
from .specs import PROVIDERS, get_spec, require_spec

log = logging.getLogger(__name__)

#: Extra headers some providers recommend for attribution / rate-limit fairness.
EXTRA_HEADERS = {
    "openrouter": {
        "HTTP-Referer": "https://github.com/Topaz-code/PulseG",
        "X-Title": "PulseG Studio",
    },
}

BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 30.0


class ChainExhausted(Exception):
    """Every configured provider for this agent failed. The task pauses, it does not die."""

    def __init__(self, agent_id: str, attempts: list[AttemptRecord], reason: str = "") -> None:
        self.agent_id = agent_id
        self.attempts = attempts
        self.reason = reason
        detail = "; ".join(
            f"{a.provider}/{a.model} ({a.failure_kind.value if a.failure_kind else 'error'})"
            for a in attempts[-4:]
        )
        super().__init__(
            f"All providers failed for agent '{agent_id}'. Last attempts: {detail or 'none'}. "
            "The task is paused in 'Needs Intervention' and will resume when a working key "
            "is added, the fallback chain is edited, or the human retries."
        )

    @property
    def needs_key_from_human(self) -> bool:
        return any(a.failure_kind in (FailureKind.INVALID_KEY, FailureKind.NOT_CONFIGURED) for a in self.attempts)


@dataclass
class RouterResult:
    ok: bool
    response: Any = None
    attempts: list[AttemptRecord] = field(default_factory=list)
    slot_used: str = ""
    provider_used: str = ""
    model_used: str = ""
    exhausted: ChainExhausted | None = None


class ProviderRegistry:
    """Builds and caches adapters from specs + user overrides + the encrypted vault."""

    def __init__(self) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        self._providers_config: ProvidersConfig | None = None
        self._vault = None
        self._config = None

    # --- wiring -----------------------------------------------------------------

    @property
    def vault(self):
        if self._vault is None:
            from ..core.vault import Vault

            self._vault = Vault()
        return self._vault

    @property
    def config(self):
        if self._config is None:
            from ..core.config import load_config

            self._config = load_config()
        return self._config

    def providers_config(self) -> ProvidersConfig:
        if self._providers_config is None:
            self._providers_config = load_providers()
        return self._providers_config

    def invalidate(self, provider_id: str | None = None) -> None:
        """Drop cached adapters after a key or setting changes."""
        if provider_id:
            self._adapters.pop(provider_id, None)
        else:
            self._adapters.clear()
        self._providers_config = None
        self._config = None

    # --- access -----------------------------------------------------------------

    def api_key(self, provider_id: str) -> str:
        spec = get_spec(provider_id)
        if spec and not spec.requires_key and not self.vault.has(provider_id):
            # Keyless providers still work with an empty key (Pollinations, Stable Horde
            # anonymous, yt-dlp, Kenney, demo).
            return ""
        return self.vault.get(provider_id) or ""

    def is_configured(self, provider_id: str) -> bool:
        spec = get_spec(provider_id)
        if spec is None:
            return False
        if not spec.requires_key:
            return True
        return bool(self.api_key(provider_id))

    def adapter(self, provider_id: str) -> ProviderAdapter:
        cached = self._adapters.get(provider_id)
        if cached is not None:
            return cached
        spec = require_spec(provider_id)
        override = self.providers_config().providers.get(provider_id)
        if override and not override.enabled:
            raise ProviderError(
                f"{spec.name} is disabled in providers.yaml.",
                kind=FailureKind.NOT_CONFIGURED,
                provider=provider_id,
            )
        base_url = (override.base_url if override and override.base_url else spec.base_url) or ""
        options: dict[str, Any] = {
            "provider_name": spec.name,
            "requires_key": spec.requires_key,
            "extra_headers": EXTRA_HEADERS.get(provider_id, {}),
        }
        if provider_id == "kenney":
            options["cache_dir"] = str(
                getattr(self.config, "kenney_cache_dir", "")
                or (getattr(self.config, "assets_cache_dir", "") or "")
            )
        if provider_id == "telegram":
            options["chat_id"] = self.config.notifications.telegram_chat_id
        adapter_cls = adapter_class_for(provider_id)
        adapter = adapter_cls(api_key=self.api_key(provider_id), base_url=base_url, **options)
        adapter.provider_id = provider_id
        self._adapters[provider_id] = adapter
        return adapter

    def test(self, provider_id: str, model: str = "") -> ConnectionTest:
        """Real API ping used by the BYOK cards. Never raises."""
        spec = get_spec(provider_id)
        if spec is None:
            return ConnectionTest("invalid", f"Unknown provider '{provider_id}'.")
        if spec.requires_key and not self.api_key(provider_id):
            return ConnectionTest("invalid", "No API key saved for this provider.")
        try:
            result = self.adapter(provider_id).test_connection(model or spec.test_model)
        except ProviderError as exc:
            return ConnectionTest("unreachable", str(exc))
        except Exception as exc:  # pragma: no cover - adapters should not raise here
            return ConnectionTest("unreachable", f"{type(exc).__name__}: {exc}")
        if spec.requires_key and self.api_key(provider_id):
            try:
                self.vault.record_test(provider_id, result.status, result.detail)
            except Exception:  # pragma: no cover
                pass
        bus.publish(
            "provider_tested",
            {"provider": provider_id, "status": result.status, "detail": result.detail},
        )
        return result

    def status_all(self) -> list[dict[str, Any]]:
        from .specs import SUBSTITUTIONS

        rows: list[dict[str, Any]] = []
        for spec in PROVIDERS.values():
            masked = self.vault.describe(spec.id).as_dict()
            usage = quota.usage_for(spec)
            row = {
                "id": spec.id,
                "name": spec.name,
                "kinds": [k.value for k in spec.kind],
                "configured": self.is_configured(spec.id),
                "requires_key": spec.requires_key,
                "masked_key": masked["masked"],
                "last_test_status": masked["last_test_status"],
                "last_test_at": masked["last_tested_at"],
                "last_test_detail": (self.vault.describe(spec.id).as_dict() or {}).get("masked", "") and "",
                "verified": spec.verified,
                "verified_on": spec.verified_on,
                "free_tier": spec.free_tier,
                "signup_url": spec.signup_url,
                "docs_url": spec.docs_url,
                "notes": spec.notes,
                "limits": spec.limits,
                "default_models": spec.default_models,
                "quota_visible": spec.quota_visible,
                "usage": usage,
                "substitution": SUBSTITUTIONS.get(spec.id),
                "keyless": not spec.requires_key,
            }
            rows.append(row)
        return rows

    def model_catalogue(self) -> dict[str, list[str]]:
        """Provider -> selectable models, for the agent model pickers in Settings."""
        catalogue: dict[str, list[str]] = {}
        for provider_id, spec in PROVIDERS.items():
            models = list(spec.default_models)
            if spec.test_model and spec.test_model not in models:
                models.insert(0, spec.test_model)
            catalogue[provider_id] = [m for m in models if m]
        return catalogue


#: Process-wide registry.
registry = ProviderRegistry()


class FallbackRouter:
    """Executes a capability call down an agent's fallback chain."""

    def __init__(self, provider_registry: ProviderRegistry | None = None) -> None:
        self.registry = provider_registry or registry

    # --- public API -------------------------------------------------------------

    def chat(
        self,
        agent: AgentDefinition,
        messages: Sequence[dict[str, Any]],
        *,
        task: Task | None = None,
        json_schema: dict[str, Any] | None = None,
        images: Sequence[str] | None = None,
        timeout: float | None = None,
        max_tokens: int | None = None,
    ) -> RouterResult:
        """Run a completion down the chain. Vision is used automatically when images exist."""
        kind = ProviderKind.VISION if images else ProviderKind.LLM

        def call(adapter: ProviderAdapter, model: str, seconds: float) -> LLMResponse:
            if images:
                return adapter.vision(
                    messages[-1].get("content", "") if isinstance(messages[-1].get("content"), str) else "",
                    images,
                    model,
                    temperature=agent.temperature,
                    max_tokens=max_tokens or agent.max_tokens,
                    timeout=seconds,
                )
            return adapter.chat(
                messages,
                model,
                temperature=agent.temperature,
                max_tokens=max_tokens or agent.max_tokens,
                timeout=seconds,
                json_schema=json_schema,
            )

        return self._run(agent, kind, call, timeout or agent.timeout_s, task)

    def chat_messages(
        self,
        agent: AgentDefinition,
        system_prompt: str,
        user_prompt: str,
        *,
        task: Task | None = None,
        json_schema: dict[str, Any] | None = None,
        images: Sequence[str] | None = None,
    ) -> RouterResult:
        """Convenience wrapper: the shape every agent actually uses.

        The system prompt is the agent's role prompt plus the compressed context block;
        the user prompt is the task instruction.
        """
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        return self.chat(agent, messages, task=task, json_schema=json_schema, images=images)

    def image(
        self,
        agent: AgentDefinition,
        prompt: str,
        out_path: str,
        *,
        width: int = 512,
        height: int = 512,
        negative_prompt: str = "",
        reference_image: str = "",
        denoise: float = 0.55,
        task: Task | None = None,
    ) -> RouterResult:
        def call(adapter: ProviderAdapter, model: str, seconds: float) -> ImageResult:
            return adapter.image(
                prompt,
                out_path,
                model=model,
                width=width,
                height=height,
                negative_prompt=negative_prompt,
                reference_image=reference_image,
                denoise=denoise,
                timeout=max(seconds, 300.0),
            )

        return self._run(agent, ProviderKind.IMAGE, call, 600.0, task)

    def transcribe(
        self, agent: AgentDefinition, audio_path: str, *, task: Task | None = None, language: str = ""
    ) -> RouterResult:
        def call(adapter: ProviderAdapter, model: str, seconds: float) -> TranscriptResult:
            return adapter.transcribe(audio_path, model, language=language, timeout=seconds)

        return self._run(agent, ProviderKind.STT, call, 600.0, task)

    def scrape(self, agent: AgentDefinition, url: str, *, task: Task | None = None) -> RouterResult:
        def call(adapter: ProviderAdapter, model: str, seconds: float) -> WebDocument:
            return adapter.scrape(url, timeout=seconds)

        return self._run(agent, ProviderKind.SEARCH, call, 120.0, task)

    def video_search(
        self, agent: AgentDefinition, query: str, *, limit: int = 8, task: Task | None = None
    ) -> RouterResult:
        def call(adapter: ProviderAdapter, model: str, seconds: float) -> list[VideoHit]:
            return adapter.search_video(query, limit=limit)

        return self._run(agent, ProviderKind.SEARCH, call, 90.0, task)

    def subtitles(
        self, agent: AgentDefinition, url: str, *, language: str = "en", task: Task | None = None
    ) -> RouterResult:
        def call(adapter: ProviderAdapter, model: str, seconds: float) -> TranscriptResult:
            return adapter.subtitles(url, language=language)

        return self._run(agent, ProviderKind.STT, call, 300.0, task)

    def audio_search(
        self,
        agent: AgentDefinition,
        query: str,
        *,
        limit: int = 10,
        licence: str = "cc0",
        task: Task | None = None,
    ) -> RouterResult:
        def call(adapter: ProviderAdapter, model: str, seconds: float) -> list[AudioHit]:
            return adapter.search_audio(query, limit=limit, licence=licence)

        return self._run(agent, ProviderKind.AUDIO_SEARCH, call, 90.0, task)

    def notify(self, agent: AgentDefinition, title: str, body: str, *, task: Task | None = None) -> RouterResult:
        def call(adapter: ProviderAdapter, model: str, seconds: float) -> bool:
            return adapter.notify(title, body, chat_id=self.registry.config.notifications.telegram_chat_id)

        return self._run(agent, ProviderKind.NOTIFY, call, 30.0, task)

    # --- the chain --------------------------------------------------------------

    def _run(self, agent: AgentDefinition, kind: ProviderKind, call, timeout: float, task: Task | None) -> RouterResult:
        chain = self._effective_chain(agent, kind, task)
        if not chain:
            exhausted = ChainExhausted(agent.id, [], reason="no capable provider in chain")
            bus.publish(
                "chain_exhausted",
                {"agent": agent.id, "task_id": getattr(task, "task_id", ""), "reason": str(exhausted)},
            )
            return RouterResult(ok=False, exhausted=exhausted)

        attempts: list[AttemptRecord] = []
        max_retries = max(0, int(self.registry.config.runtime.max_retries_per_slot))

        for slot, ref in chain:
            spec = get_spec(ref.provider)
            if spec is None:
                attempts.append(
                    AttemptRecord(
                        provider=ref.provider,
                        model=ref.model,
                        slot=slot,  # type: ignore[arg-type]
                        ok=False,
                        failure_kind=FailureKind.NOT_CONFIGURED,
                        message="provider is not in the registry",
                    )
                )
                continue
            if spec.requires_key and not self.registry.api_key(ref.provider):
                # Not configured is not a failure: the human simply has not added this key.
                # Record it so the UI can explain the skip, but do not alarm.
                attempts.append(
                    AttemptRecord(
                        provider=ref.provider,
                        model=ref.model,
                        slot=slot,  # type: ignore[arg-type]
                        ok=False,
                        failure_kind=FailureKind.NOT_CONFIGURED,
                        message=f"No {spec.name} key saved - skipped.",
                    )
                )
                self._publish_skip(agent, ref, task)
                continue

            attempt_index = 0
            while attempt_index <= max_retries:
                attempt_index += 1
                bus.publish(
                    "provider_attempt",
                    {
                        "agent": agent.id,
                        "task_id": getattr(task, "task_id", ""),
                        "provider": ref.provider,
                        "model": ref.model,
                        "slot": slot,
                        "attempt": attempt_index,
                    },
                )
                started = time.perf_counter()
                try:
                    adapter = self.registry.adapter(ref.provider)
                    response = call(adapter, ref.model, timeout)
                    latency = int((time.perf_counter() - started) * 1000)
                    if isinstance(response, LLMResponse) and response.is_empty:
                        raise ProviderError(
                            f"{spec.name} returned an empty response for {ref.model}.",
                            kind=FailureKind.EMPTY_RESPONSE,
                            provider=ref.provider,
                            model=ref.model,
                        )
                    tokens_in = getattr(response, "tokens_in", 0) or 0
                    tokens_out = getattr(response, "tokens_out", 0) or 0
                    attempt = AttemptRecord(
                        provider=ref.provider,
                        model=ref.model,
                        slot=slot,  # type: ignore[arg-type]
                        ok=True,
                        latency_ms=latency,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                    attempts.append(attempt)
                    if task is not None:
                        task.record_attempt(attempt)
                        task.provider_used = ref.provider
                        task.model_used = ref.model
                    quota.record_success(ref.provider, tokens_in=tokens_in, tokens_out=tokens_out)
                    quota.warn_if_needed(spec)
                    bus.publish(
                        "provider_success",
                        {
                            "agent": agent.id,
                            "provider": ref.provider,
                            "model": ref.model,
                            "slot": slot,
                            "latency_ms": latency,
                        },
                    )
                    return RouterResult(
                        ok=True,
                        response=response,
                        attempts=attempts,
                        slot_used=slot,
                        provider_used=ref.provider,
                        model_used=ref.model,
                    )
                except ProviderError as exc:
                    latency = int((time.perf_counter() - started) * 1000)
                    attempt = AttemptRecord(
                        provider=ref.provider,
                        model=ref.model,
                        slot=slot,  # type: ignore[arg-type]
                        ok=False,
                        failure_kind=exc.kind,
                        message=str(exc)[:500],
                        latency_ms=latency,
                        http_status=exc.status_code,
                    )
                    attempts.append(attempt)
                    if task is not None:
                        task.record_attempt(attempt)
                        task.retry_count += 1
                    quota.record_failure(ref.provider, exc.kind.value, str(exc), exc.retry_after)
                    self._publish_failure(agent, ref, slot, exc, task)
                    if exc.kind.retryable_on_same_model and attempt_index <= max_retries:
                        delay = min(BACKOFF_MAX_S, BACKOFF_BASE_S * attempt_index)
                        if exc.retry_after:
                            delay = min(BACKOFF_MAX_S * 2, exc.retry_after)
                        log.info(
                            "Retrying %s/%s in %.1fs after %s",
                            ref.provider, ref.model, delay, exc.kind.value,
                        )
                        time.sleep(delay)
                        continue
                    break
                except Exception as exc:  # unexpected adapter bug: treat as unavailable
                    latency = int((time.perf_counter() - started) * 1000)
                    attempt = AttemptRecord(
                        provider=ref.provider,
                        model=ref.model,
                        slot=slot,  # type: ignore[arg-type]
                        ok=False,
                        failure_kind=FailureKind.UNKNOWN,
                        message=f"{type(exc).__name__}: {exc}"[:500],
                        latency_ms=latency,
                    )
                    attempts.append(attempt)
                    if task is not None:
                        task.record_attempt(attempt)
                        task.retry_count += 1
                    quota.record_failure(ref.provider, "unknown", str(exc))
                    log.exception("Unhandled adapter error for %s", ref.provider)
                    break
            # end retry loop -> next slot
            if slot != "primary":
                continue

        exhausted = ChainExhausted(agent.id, attempts)
        bus.publish(
            "chain_exhausted",
            {
                "agent": agent.id,
                "task_id": getattr(task, "task_id", ""),
                "message": str(exhausted),
                "needs_key": exhausted.needs_key_from_human,
                "attempts": [a.model_dump(mode="json") for a in attempts[-6:]],
            },
        )
        return RouterResult(
            ok=False,
            attempts=attempts,
            exhausted=exhausted,
            provider_used=attempts[-1].provider if attempts else "",
            model_used=attempts[-1].model if attempts else "",
        )

    # --- chain construction -----------------------------------------------------

    def _effective_chain(
        self, agent: AgentDefinition, kind: ProviderKind, task: Task | None
    ) -> list[tuple[str, ModelRef]]:
        """Ordered chain, filtered to capable/available providers, decline-rotated."""
        chain = agent.chain()
        usable: list[tuple[str, ModelRef]] = []
        for slot, ref in chain:
            spec = get_spec(ref.provider)
            if spec is None:
                continue
            if kind not in (ProviderKind.LLM, ProviderKind.VISION) and kind not in spec.kind:
                continue
            if kind is ProviderKind.VISION:
                adapter_cls = adapter_class_for(ref.provider) if ref.provider in SPECIALISED or ref.provider in OPENAI_COMPATIBLE else None
                supports = bool(spec.supports_vision) or (
                    adapter_cls is not None and getattr(adapter_cls, "supports_vision", False)
                )
                if not supports:
                    continue
            if not self.registry.is_configured(ref.provider) and spec.requires_key:
                # Keep it in the chain: the human will see "skipped, no key" in the log,
                # which is much clearer than silently pretending the chain is shorter.
                pass
            usable.append((slot, ref))
        # Demo mode is a hard offline guarantee: the whole studio runs with no network at all,
        # so the demo provider goes on the end of every chain that could reach it and is
        # labelled in the UI. Without this, "demo mode" would still burn real keys.
        if _demo_mode() and all(ref.provider != "demo" for _, ref in usable):
            demo_spec = get_spec("demo")
            capable = demo_spec is not None and kind in demo_spec.kind
            if kind is ProviderKind.VISION:
                capable = demo_spec is not None and demo_spec.supports_vision
            if capable:
                usable.append(("extra", ModelRef(provider="demo", model="demo-1")))
        return self._apply_decline_rotation(agent, usable, task)

    def _apply_decline_rotation(
        self, agent: AgentDefinition, chain: list[tuple[str, ModelRef]], task: Task | None
    ) -> list[tuple[str, ModelRef]]:
        """Rule B.2.5: three declines with the same model -> rotate to the next fallback."""
        if task is None or not chain:
            return chain
        threshold = agent.max_decline_rotations or 3
        if task.decline_count < threshold:
            return chain
        rotations = task.decline_count // threshold
        if rotations <= 0 or len(chain) == 1:
            return chain
        rotated = chain[rotations % len(chain):] + chain[: rotations % len(chain)]
        bus.publish(
            "model_rotated",
            {
                "agent": agent.id,
                "task_id": task.task_id,
                "declines": task.decline_count,
                "from": chain[0][1].model,
                "to": rotated[0][1].model,
                "message": (
                    f"{agent.name} was declined {task.decline_count} times with the same "
                    f"model; switching to {rotated[0][1].provider}/{rotated[0][1].model}."
                ),
            },
        )
        return rotated

    # --- events -----------------------------------------------------------------

    def _publish_skip(self, agent: AgentDefinition, ref: ModelRef, task: Task | None) -> None:
        bus.publish(
            "provider_skipped",
            {
                "agent": agent.id,
                "task_id": getattr(task, "task_id", ""),
                "provider": ref.provider,
                "message": f"{agent.name}: no {ref.provider} key yet, trying the next provider.",
            },
        )

    def _publish_failure(
        self, agent: AgentDefinition, ref: ModelRef, slot: str, exc: ProviderError, task: Task | None
    ) -> None:
        bus.publish(
            "provider_failed",
            {
                "agent": agent.id,
                "task_id": getattr(task, "task_id", ""),
                "provider": ref.provider,
                "model": ref.model,
                "slot": slot,
                "kind": exc.kind.value,
                "status_code": exc.status_code,
                "message": str(exc)[:500],
                "human_actionable": exc.human_actionable,
            },
        )


def _demo_mode() -> bool:
    """True when ``PULSEG_DEMO=1`` is set (tests, CI, first-run tours).

    In demo mode the demo provider is appended to every capable chain, so nothing in the
    studio can reach the network by accident - not even a chain whose real keys happen to be
    present in the vault.
    """
    return env_flag("PULSEG_DEMO")


#: Process-wide router.
router = FallbackRouter()
