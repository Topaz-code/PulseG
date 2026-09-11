"""The fallback chain is the backbone of the zero-abandonment promise, so it is tested hard.

These tests never touch the network: ``respx`` intercepts httpx, which is exactly how the
adapters talk to providers.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from backend.core.models import AgentDefinition, ModelRef
from backend.providers.router import ChainExhausted, FallbackRouter, ProviderRegistry

CHAT_OK = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "test-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": '{"ok": true, "note": "hello"}'},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
}


def agent_with(primary: tuple[str, str], *fallbacks: tuple[str, str]) -> AgentDefinition:
    return AgentDefinition(
        id="programmer",
        name="Programmer",
        role="writes code",
        primary=ModelRef(provider=primary[0], model=primary[1]),
        fallbacks=[ModelRef(provider=provider, model=model) for provider, model in fallbacks],
        temperature=0.2,
        max_tokens=512,
    )


@pytest.fixture
def keys(studio_home, monkeypatch):
    """Put fake keys in the vault so the chain treats the providers as configured."""
    from backend.core.vault import Vault

    vault = Vault()
    for provider in ("groq", "mistral", "sealion", "openrouter", "opencode_zen", "google_ai_studio"):
        vault.set(provider, f"test-key-{provider}")
    return vault


def test_primary_success(keys):
    agent = agent_with(("groq", "llama-3.3-70b-versatile"), ("mistral", "mistral-small-latest"))
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=CHAT_OK)
        )
        result = FallbackRouter(ProviderRegistry()).chat(agent, [{"role": "user", "content": "hi"}])
    assert result.ok
    assert result.provider_used == "groq"
    assert result.slot_used == "primary"
    assert result.response.parsed["ok"] is True


def test_rate_limited_then_fallback(keys):
    agent = agent_with(("groq", "llama-3.3-70b-versatile"), ("mistral", "mistral-small-latest"))
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(429, headers={"retry-after": "0"}, json={"error": {"message": "rate"}})
        )
        mock.post("https://api.mistral.ai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=CHAT_OK)
        )
        result = FallbackRouter(ProviderRegistry()).chat(agent, [{"role": "user", "content": "hi"}])
    assert result.ok, result.attempts
    assert result.provider_used == "mistral"
    kinds = [attempt.failure_kind.value for attempt in result.attempts if attempt.failure_kind]
    assert "rate_limited" in kinds


def test_invalid_key_reports_needs_human(keys, monkeypatch):
    # Demo mode appends a working offline provider to every chain, which would mask the
    # exhaustion this test is about. Turning it off is the honest way to test the real path.
    monkeypatch.delenv("PULSEG_DEMO", raising=False)
    agent = agent_with(("groq", "llama-3.3-70b-versatile"))
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
        )
        result = FallbackRouter(ProviderRegistry()).chat(agent, [{"role": "user", "content": "hi"}])
    assert not result.ok
    assert result.exhausted is not None
    assert result.exhausted.needs_key_from_human is True


def test_missing_key_is_skipped_not_counted_as_failure(studio_home):
    """A provider with no key must not poison the attempt history; it is a skip."""
    agent = agent_with(("groq", "llama-3.3-70b-versatile"), ("demo", "demo-model"))
    result = FallbackRouter(ProviderRegistry()).chat(agent, [{"role": "user", "content": "hi"}])
    assert result.ok
    assert result.provider_used == "demo"
    kinds = {attempt.provider: attempt.failure_kind.value for attempt in result.attempts if attempt.failure_kind}
    assert kinds.get("groq") == "not_configured"


def test_every_step_failing_raises_chain_exhausted(keys, monkeypatch):
    monkeypatch.delenv("PULSEG_DEMO", raising=False)
    agent = agent_with(("groq", "llama-3.3-70b-versatile"), ("mistral", "mistral-small-latest"))
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(503, json={"error": {"message": "down"}})
        )
        mock.post("https://api.mistral.ai/v1/chat/completions").mock(
            return_value=httpx.Response(500, text="boom")
        )
        result = FallbackRouter(ProviderRegistry()).chat(agent, [{"role": "user", "content": "hi"}])
    assert not result.ok
    assert isinstance(result.exhausted, ChainExhausted)
    assert "Needs Intervention" in str(result.exhausted)


def test_openrouter_gets_attribution_headers(keys):
    agent = agent_with(("openrouter", "qwen/qwen3-coder:free"))
    seen: dict[str, str] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(200, json=CHAT_OK)

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=capture)
        FallbackRouter(ProviderRegistry()).chat(agent, [{"role": "user", "content": "hi"}])
    assert seen.get("x-title") == "PulseG Studio"
    assert "http-referer" in {key.lower() for key in seen}


def test_substituted_provider_is_reported():
    from backend.providers.specs import SUBSTITUTIONS, effective_chain_entry, get_spec

    assert "zai" in SUBSTITUTIONS
    provider, model, note = effective_chain_entry("zai", "glm-4.6")
    assert provider == "openrouter"
    assert ":free" in model
    assert "substituted" in note
    assert get_spec("openrouter").verified == "verified"


def test_decline_rotation_after_three_declines(keys):
    """Rule 5: three consecutive Auditor declines rotate the model within the chain."""
    from backend.core.models import Status, Task

    agent = agent_with(("groq", "llama-3.3-70b-versatile"), ("mistral", "mistral-small-latest"))
    task = Task(
        task_id="TASK_001",
        project_id="proj_x",
        phase=1,
        created_by="prompter",
        assigned_to="programmer",
        status=Status.IN_PROGRESS,
        instruction="do a thing",
        decline_count=3,
    )
    router = FallbackRouter(ProviderRegistry())
    chain = router._effective_chain(agent, __import__("backend.core.models", fromlist=["ProviderKind"]).ProviderKind.LLM, task)
    assert chain, "chain should not be empty"
    assert chain[0][1].model != agent.primary.model or chain[0][0] != "primary"
