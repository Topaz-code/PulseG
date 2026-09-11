"""Provider layer: the verified provider table, the adapters, and the fallback router."""
from __future__ import annotations

from .specs import (  # noqa: F401
    FREE_FOREVER,
    ONE_TIME_CREDIT,
    PROJECT_DEFAULT_MODELS,
    PROVIDERS,
    SUBSTITUTIONS,
    VERIFIED_ON,
    all_specs,
    by_kind,
    effective_chain_entry,
    get_spec,
    llm_provider_ids,
    model_catalogue,
    require_spec,
    verification_report,
)

__all__ = [
    "FREE_FOREVER",
    "ONE_TIME_CREDIT",
    "PROJECT_DEFAULT_MODELS",
    "PROVIDERS",
    "SUBSTITUTIONS",
    "VERIFIED_ON",
    "all_specs",
    "by_kind",
    "effective_chain_entry",
    "get_spec",
    "llm_provider_ids",
    "model_catalogue",
    "require_spec",
    "verification_report",
]
