"""Agent registry: load, validate, edit and persist the roster from ``agents.yaml``.

The registry is data-driven (PROCESS.md rule 6): adding a 13th agent means adding a block to
``agents.yaml``, and a new provider means a config entry - neither needs a code change. The
only code-level knowledge is which *module* implements an agent's side effects, and unknown
agent ids fall back to the generic text runner rather than failing.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from ..core.config import load_agents_raw, save_agents_raw
from ..core.models import AgentDefinition, ModelRef
from ..core.vault import Vault
from ..providers.specs import PROVIDERS, get_spec
from .roster import AGENTS as ROSTER_DEFAULTS

log = logging.getLogger(__name__)


class RegistryError(ValueError):
    pass


def _validate(agent: AgentDefinition, known_ids: set[str]) -> list[str]:
    """Return a list of human-readable problems with one agent definition."""
    problems: list[str] = []
    if agent.id in known_ids:
        problems.append(f"duplicate agent id '{agent.id}'")
    for slot, ref in agent.chain():
        spec = get_spec(ref.provider)
        if spec is None:
            problems.append(
                f"{agent.id}.{slot}: unknown provider '{ref.provider}' - add it to "
                "providers.yaml or pick one of: " + ", ".join(sorted(PROVIDERS))
            )
        elif ref.model and spec.default_models and ref.model not in spec.default_models:
            # Not an error: users legitimately pin models we do not list. Informational.
            pass
    if agent.primary.provider == "" or agent.primary.model == "":
        problems.append(f"{agent.id}: primary model is not set")
    return problems


class AgentRegistry:
    """The live roster. Cached in memory and reloaded when the file changes."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        self._loaded = False
        self.problems: list[str] = []

    # --- loading ----------------------------------------------------------------

    def reload(self) -> dict[str, AgentDefinition]:
        raw = load_agents_raw()
        agents: dict[str, AgentDefinition] = {}
        seen: set[str] = set()
        problems: list[str] = []
        for entry in raw:
            try:
                agent = AgentDefinition.model_validate(entry)
            except Exception as exc:
                problems.append(f"Could not read an agent block: {exc}")
                continue
            agent_problems = _validate(agent, seen)
            problems.extend(agent_problems)
            # An agent with an unknown provider is kept but disabled, so the user can see and
            # fix it in Settings instead of silently losing the agent.
            if any("unknown provider" in problem for problem in agent_problems):
                agent.enabled = False
                agent.description = (
                    agent.description + " [disabled: fix its provider in Settings]"
                ).strip()
            seen.add(agent.id)
            agents[agent.id] = agent
        self._agents = agents
        self.problems = problems
        self._loaded = True
        return agents

    @property
    def agents(self) -> dict[str, AgentDefinition]:
        if not self._loaded:
            self.reload()
        return self._agents

    def get(self, agent_id: str) -> AgentDefinition | None:
        return self.agents.get(agent_id)

    def require(self, agent_id: str) -> AgentDefinition:
        agent = self.get(agent_id)
        if agent is None:
            raise RegistryError(
                f"Unknown agent '{agent_id}'. Known agents: {', '.join(sorted(self.agents))}"
            )
        return agent

    def enabled(self) -> list[AgentDefinition]:
        return [agent for agent in self.agents.values() if agent.enabled]

    def ids(self) -> list[str]:
        return list(self.agents.keys())

    # --- editing (writes agents.yaml) --------------------------------------------

    def _persist(self) -> None:
        save_agents_raw([agent.model_dump(mode="json") for agent in self.agents.values()])

    def update_agent(self, agent_id: str, **changes: Any) -> AgentDefinition:
        agent = self.require(agent_id)
        mutable = agent.model_dump()
        for key, value in changes.items():
            if value is None:
                continue
            if key in ("primary", "fallbacks") and isinstance(value, (dict, list)):
                continue  # handled by set_chain
            if key not in mutable:
                raise RegistryError(f"'{key}' is not a field on an agent.")
            mutable[key] = value
        updated = AgentDefinition.model_validate(mutable)
        self._agents[agent_id] = updated
        self._persist()
        return updated

    def set_chain(self, agent_id: str, primary: dict[str, str], fallbacks: list[dict[str, str]]) -> AgentDefinition:
        """Replace an agent's model chain. The UI's drag-to-reorder writes through here."""
        agent = self.require(agent_id)
        mutable = agent.model_dump()
        mutable["primary"] = ModelRef.model_validate(primary).model_dump()
        mutable["fallbacks"] = [ModelRef.model_validate(item).model_dump() for item in fallbacks[:3]]
        updated = AgentDefinition.model_validate(mutable)
        self._agents[agent_id] = updated
        self._persist()
        log.info("Updated chain for %s: %s -> %s", agent_id, primary, fallbacks)
        return updated

    def set_prompt(self, agent_id: str, prompt: str) -> AgentDefinition:
        return self.update_agent(agent_id, system_prompt=prompt)

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentDefinition:
        return self.update_agent(agent_id, enabled=enabled)

    def add_agent(self, definition: dict[str, Any]) -> AgentDefinition:
        """Add a brand new agent from config alone."""
        agent = AgentDefinition.model_validate(definition)
        if agent.id in self._agents:
            raise RegistryError(f"Agent '{agent.id}' already exists.")
        problems = _validate(agent, set(self._agents))
        if problems:
            raise RegistryError("; ".join(problems))
        self._agents[agent.id] = agent
        self._persist()
        return agent

    def remove_agent(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        del self._agents[agent_id]
        self._persist()
        return True

    def reset_to_defaults(self) -> dict[str, AgentDefinition]:
        from ..core.config import save_agents_raw as _save
        from .roster import default_agents_payload

        _save(default_agents_payload()["agents"])
        return self.reload()

    # --- reporting ---------------------------------------------------------------

    def configured_providers(self) -> set[str]:
        """Providers that currently have a usable key (or need none)."""
        vault = Vault()
        configured: set[str] = set()
        for provider_id, spec in PROVIDERS.items():
            if not spec.requires_key or vault.has(provider_id):
                configured.add(provider_id)
        return configured

    def missing_providers(self) -> list[str]:
        """Providers referenced by the roster that have no key yet - drives the UI prompt."""
        vault = Vault()
        missing: list[str] = []
        for agent in self.enabled():
            for _, ref in agent.chain():
                spec = get_spec(ref.provider)
                if spec and spec.requires_key and not vault.has(ref.provider):
                    if ref.provider not in missing:
                        missing.append(ref.provider)
        return missing

    def coverage(self) -> list[dict[str, Any]]:
        """Per-agent readiness: can it run today, and with how many options?"""
        vault = Vault()
        rows: list[dict[str, Any]] = []
        for agent in self.agents.values():
            chain = agent.chain()
            ready = []
            missing = []
            for slot, ref in chain:
                spec = get_spec(ref.provider)
                usable = bool(spec) and (not spec.requires_key or vault.has(ref.provider))
                entry = {
                    "slot": slot,
                    "provider": ref.provider,
                    "provider_name": spec.name if spec else ref.provider,
                    "model": ref.model,
                    "usable": usable,
                    "verified": spec.verified if spec else "unverified",
                }
                (ready if usable else missing).append(entry)
            rows.append(
                {
                    "agent_id": agent.id,
                    "name": agent.name,
                    "enabled": agent.enabled,
                    "chain": [entry for entry in ready + missing],
                    "usable_options": len(ready),
                    "status": (
                        "ready"
                        if ready
                        else ("awaiting_key" if missing else "no_chain")
                    ),
                    "missing_providers": sorted({entry["provider"] for entry in missing}),
                }
            )
        return rows

    def as_list(self) -> list[dict[str, Any]]:
        return [agent.model_dump(mode="json") for agent in self.agents.values()]

    def describe_for_ui(self) -> dict[str, Any]:
        from ..providers.specs import llm_provider_ids, model_catalogue

        return {
            "agents": self.coverage(),
            "raw": self.as_list(),
            "problems": self.problems,
            "missing_providers": self.missing_providers(),
            "provider_options": llm_provider_ids(),
            "model_catalogue": model_catalogue(),
        }

    def agent_for_task_kind(self, kind: str) -> str:
        """Default routing when the Prompter does not name an agent explicitly."""
        mapping = {
            "research": "researcher",
            "transcribe": "transcriptor",
            "documentation": "documenter",
            "art": "image_generator",
            "audio": "audio_curator",
            "implementation": "programmer",
            "test": "tester",
            "story": "story_writer",
            "audit": "auditor",
            "planning": "planning_agent",
        }
        return mapping.get(kind, "programmer")

    def default_names(self) -> dict[str, str]:
        return {agent.id: agent.name for agent in self.agents.values()}

    def colour_map(self) -> dict[str, str]:
        return {agent.id: agent.color for agent in self.agents.values()}

    def summary_lines(self) -> Iterable[str]:
        for agent in self.agents.values():
            chain = " -> ".join(f"{slot}:{ref.provider}/{ref.model}" for slot, ref in agent.chain())
            yield f"{agent.id:<16} {chain}"


#: Process-wide roster.
registry = AgentRegistry()


def agent_definition(agent_id: str) -> AgentDefinition:
    return registry.require(agent_id)


def all_agent_ids() -> list[str]:
    return registry.ids()


def defaults_snapshot() -> list[dict[str, Any]]:
    return ROSTER_DEFAULTS
