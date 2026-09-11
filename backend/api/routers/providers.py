"""Providers: BYOK cards, real connection tests, quotas and substitutions.
Three things this router refuses to do:
* return a full API key (only the masked form, last four characters),
* report a test result it did not actually observe,
* hide a substitution. Every provider whose specification claim did not survive verification
  carries its substitute and the reason in the payload the UI renders.
"""
from __future__ import annotations
import logging
from typing import Any
from fastapi import APIRouter, HTTPException
from ...core.config import ProviderOverride, load_providers, save_providers
from ...core.events import bus
from ...core.quota import quota
from ...core.vault import Vault
from ...providers.specs import (
    FREE_FOREVER,
    ONE_TIME_CREDIT,
    PROVIDERS,
    SUBSTITUTIONS,
    VERIFIED_ON,
    get_spec,
    llm_provider_ids,
)
from ..deps import guarded
from ..schemas import ProviderOverrideRequest, SaveKeyRequest, TestProviderRequest
log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/providers", tags=["providers"])
@router.get("")
def list_providers() -> dict[str, Any]:
    """Every provider, with its verification status, quota and whether a key is present."""
    vault = Vault()
    overrides = load_providers().providers
    rows: list[dict[str, Any]] = []
    for spec in PROVIDERS.values():
        override = overrides.get(spec.id)
        masked = vault.describe(spec.id)
        rows.append(
            {
                "id": spec.id,
                "name": spec.name,
                "kinds": [kind.value for kind in spec.kind],
                "requires_key": spec.requires_key,
                "keyless": not spec.requires_key,
                "configured": vault.has(spec.id) or not spec.requires_key,
                "has_key": vault.has(spec.id),
                "masked_key": masked.display if vault.has(spec.id) else ("Not needed" if not spec.requires_key else "Not set"),
                "last_test_status": masked.last_test_status,
                "last_test_at": masked.last_tested_at,
                "verified": spec.verified,
                "verified_on": spec.verified_on,
                "free_tier": spec.free_tier,
                "limits": spec.limits,
                "signup_url": spec.signup_url,
                "docs_url": spec.docs_url,
                "notes": spec.notes,
                "default_models": spec.default_models,
                "quota_visible": spec.quota_visible,
                "supports_vision": spec.supports_vision,
                "free_forever": spec.id in FREE_FOREVER,
                "one_time_credit": spec.id in ONE_TIME_CREDIT,
                "substitution": SUBSTITUTIONS.get(spec.id),
                "usage": quota.usage_for(spec),
                "override": override.model_dump(mode="json") if override else None,
                "enabled": override.enabled if override else True,
            }
        )
    rows.sort(key=lambda row: (not row["free_forever"], row["name"]))
    return {
        "providers": rows,
        "verified_on": VERIFIED_ON,
        "substitutions": SUBSTITUTIONS,
        "llm_options": llm_provider_ids(),
        "aggregate_quota_percent": quota.aggregate_percent(list(PROVIDERS.values())),
        "vault_file": str(__import__("backend.core.paths", fromlist=["vault_file"]).vault_file()),
    }
@router.get("/verification")
def verification() -> dict[str, Any]:
    """The provider verification record, straight from the source of truth."""
    from ...providers.specs import verification_report

    return {
        "verified_on": VERIFIED_ON,
        "providers": verification_report(),
        "substitutions": SUBSTITUTIONS,
        "free_forever": sorted(FREE_FOREVER),
        "one_time_credit": sorted(ONE_TIME_CREDIT),
        "markdown_path": "project-log/PROVIDER_VERIFICATION.md",
    }


@router.get("/quota")
def quota_snapshot() -> dict[str, Any]:
    """The Overview screen's quota meters, including the proactive fallback warnings."""
    rows = quota.snapshot(list(PROVIDERS.values()))
    warnings: list[dict[str, Any]] = []
    for row in rows:
        if row.get("warning"):
            warnings.append(
                {
                    "provider": row["provider"],
                    "name": row["name"],
                    "percent": row["percent"],
                    "message": row["warning"],
                    "fallback": _fallback_for(row["provider"]),
                }
            )
    return {
        "providers": rows,
        "aggregate_percent": quota.aggregate_percent(list(PROVIDERS.values())),
        "warnings": warnings,
    }


@router.put("/key")
@router.post("/key", include_in_schema=False)  # POST alias: the natural verb for "save this key"
@guarded("save provider key")
def save_key(payload: SaveKeyRequest) -> dict[str, Any]:
    """Store a key in the encrypted vault, then immediately test it.
    Saving and testing in one call is deliberate: a key that is stored but never verified is a
    support question waiting to happen, and the resume-what-paused logic depends on knowing
    whether the chain is usable now.
    """
    spec = get_spec(payload.provider_id)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_provider", "message": f"No provider called '{payload.provider_id}'."},
        )
    vault = Vault()
    key = payload.api_key.strip()
    vault.set(payload.provider_id, key)
    test = _test(payload.provider_id, "")
    resumed = _resume_paused(f"{spec.name} key added")
    return {
        "provider_id": payload.provider_id,
        "masked_key": vault.describe(payload.provider_id).display,
        "test": test,
        "resumed_tasks": resumed,
    }
@router.delete("/key/{provider_id}")
@guarded("delete provider key")
def delete_key(provider_id: str) -> dict[str, Any]:
    removed = Vault().delete(provider_id)
    bus.publish("provider_key_removed", {"provider_id": provider_id})
    return {"removed": removed, "note": "Tasks that needed this provider will pause, not fail."}
@router.post("/test")
@guarded("test provider")
def test_provider(payload: TestProviderRequest) -> dict[str, Any]:
    return _test(payload.provider_id, payload.model)
@router.post("/test-all")
@guarded("test all providers")
def test_all() -> dict[str, Any]:
    """Test every configured provider. Rate limits mean this is a real ping per provider."""
    rows: list[dict[str, Any]] = []
    for spec in PROVIDERS.values():
        if spec.requires_key and not Vault().has(spec.id):
            continue
        rows.append(_test(spec.id, ""))
    return {
        "results": rows,
        "valid": sum(1 for row in rows if row["status"] == "valid"),
        "invalid": sum(1 for row in rows if row["status"] == "invalid"),
        "rate_limited": sum(1 for row in rows if row["status"] == "rate_limited"),
        "note": "A rate-limited result means the key works; the provider is just busy right now.",
    }
@router.get("/{provider_id}/models")
@guarded("list provider models")
def models(provider_id: str) -> dict[str, Any]:
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_provider", "message": "Not found."})
    return {
        "provider_id": provider_id,
        "models": spec.default_models,
        "source": "spec",
        "supports_vision": spec.supports_vision,
        "note": (
            "These are the ids verified working on " + spec.verified_on + ". Model ids change "
            "faster than anything else in software; the agent editor accepts any id you type, so "
            "a retired one is a Settings change, not a code change."
        ),
    }
@router.put("/{provider_id}/override")
@guarded("update provider override")
def set_override(provider_id: str, payload: ProviderOverrideRequest) -> dict[str, Any]:
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_provider", "message": "Not found."})
    config = load_providers()
    current = config.providers.get(provider_id) or ProviderOverride()
    changes = payload.model_dump(exclude_none=True)
    merged = ProviderOverride(**{**current.model_dump(), **changes})
    config.providers[provider_id] = merged
    save_providers(config)
    return {"override": merged.model_dump(mode="json")}


# Declared after every literal path on purpose: a parameterised route defined earlier would
# swallow "/verification" and "/quota" and answer "No provider called 'verification'".
@router.get("/{provider_id}")
@guarded("read provider")
def get_provider(provider_id: str) -> dict[str, Any]:
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_provider", "message": f"No provider called '{provider_id}'."},
        )
    vault = Vault()
    masked = vault.describe(provider_id)
    override = load_providers().providers.get(provider_id)
    return {
        "id": spec.id,
        "name": spec.name,
        "kind": spec.kind,
        "has_key": vault.has(provider_id),
        "masked_key": masked.display if vault.has(provider_id) else "",
        "requires_key": spec.requires_key,
        "free_tier": spec.free_tier,
        "signup_url": spec.signup_url,
        "notes": spec.notes,
        "default_models": list(spec.default_models),
        "test_model": spec.test_model,
        "override": override.model_dump(mode="json") if override else None,
        "verified_on": spec.verified_on,
        "verification_note": spec.verification_note,
    }


def _test(provider_id: str, model: str) -> dict[str, Any]:
    from ...providers.router import ProviderRegistry
    spec = get_spec(provider_id)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_provider", "message": f"No provider called '{provider_id}'."},
        )
    registry = ProviderRegistry()
    try:
        result = registry.test(provider_id, model or spec.test_model)
    except Exception as exc:
        return {
            "provider_id": provider_id,
            "status": "unreachable",
            "detail": str(exc)[:300],
            "tested_at": spec.verified_on,
        }
    status = getattr(result, "status", "unknown")
    detail = getattr(result, "detail", "")
    latency = getattr(result, "latency_ms", 0)
    payload = {
        "provider_id": provider_id,
        "status": status,
        "detail": detail,
        "latency_ms": latency,
        "model": model or spec.test_model,
        "keyless": not spec.requires_key,
    }
    if status == "invalid":
        payload["hint"] = (
            "Check the key was copied whole and has not been rotated. Keys are stored encrypted "
            "and never leave this machine."
        )
    elif status == "rate_limited":
        payload["hint"] = "The key works. The provider is throttling right now; this is not an error."
    bus.publish("provider_tested", payload)
    return payload
def _resume_paused(reason: str) -> list[str]:
    from ...runtime import runtime
    bus_ = runtime.bus()
    if bus_ is None:
        return []
    try:
        resumed = bus_.resume_all_paused(reason=reason)
    except Exception as exc:  # pragma: no cover
        log.info("Resume after key change skipped: %s", exc)
        return []
    if resumed:
        runtime.wake()
    return resumed
def _fallback_for(provider_id: str) -> str:
    """Which agent chains would take over if this provider ran out - shown on the warning."""
    from ...agents.registry import registry
    users: list[str] = []
    for agent in registry.enabled():
        chain = agent.chain()
        if chain and chain[0][1].provider == provider_id:
            if len(chain) > 1:
                users.append(f"{agent.name} -> {chain[1][1].provider}")
            else:
                users.append(f"{agent.name} has no fallback")
    return "; ".join(users[:6])
