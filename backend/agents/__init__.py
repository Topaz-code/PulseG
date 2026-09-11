"""PulseG Studio agents.

Twelve agents, each with a user-editable primary + two fallbacks from ``agents.yaml``. The
modules here are thin wrappers around :mod:`backend.agents.base`; the roster, prompts and
model chains are data, so a thirteenth agent needs no code.
"""
from __future__ import annotations

from .base import AgentContext, AgentRunResult, build_agent_messages, run_and_write, run_text_agent
from .loader import RUNNERS, generic, run_agent, runner_for
from .registry import AgentRegistry, registry

__all__ = [
    "AgentContext",
    "AgentRunResult",
    "AgentRegistry",
    "RUNNERS",
    "build_agent_messages",
    "generic",
    "registry",
    "run_agent",
    "run_and_write",
    "run_text_agent",
    "runner_for",
]
