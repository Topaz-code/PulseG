"""Agents: the roster, the model chains, the prompts.

Everything here writes through ``AgentRegistry``, which means every change lands in
``~/.pulsegstudio/agents.yaml``. That is the point of the config-driven design: the UI edits a
file through a validated API, and adding a thirteenth agent needs no Python at all.

Saving a chain or a key also resumes paused tasks, because the reason they paused was exactly
that no provider in their chain could run.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from ...agents.registry import RegistryError, registry
from ...core.events import bus
from ...core.models import Status
from ...orchestration import context_engine, memory
from ...runtime import runtime
from ..deps import guarded, require_project
from ..schemas import AddAgentRequest, UpdateAgentRequest, UpdateChainRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents() -> dict[str, Any]:
    """The roster with per-agent readiness: which chains can run today and which need a key."""
    return registry.describe_for_ui()


@router.get("/summary")
def summary() -> dict[str, Any]:
    return {
        "agents": registry.coverage(),
        "missing_providers": registry.missing_providers(),
        "count": len(registry.agents),
    }


@router.get("/states")
@guarded("read agent states")
def states() -> dict[str, Any]:
    """Live per-agent state for the Overview grid - status, current task, usage so far."""
    context = require_project()
    stored = memory.read_agent_states(context.path)
    rows: list[dict[str, Any]] = []
    in_flight = {
        task.assigned_to: task.task_id
        for task in context.bus.by_status(Status.IN_PROGRESS)
    }
    for agent in registry.agents.values():
        state = stored.get(agent.id)
        rows.append(
            {
                "agent_id": agent.id,
                "name": agent.name,
                "role": agent.role,
                "colour": agent.color,
                "enabled": agent.enabled,
                "status": "working" if agent.id in in_flight else (state.status if state else "idle"),
                "current_task": in_flight.get(agent.id, ""),
                "last_task": state.last_task if state else None,
                "provider": state.active_provider if state else "",
                "model": state.active_model if state else "",
                "tasks_completed": state.tasks_completed if state else 0,
                "tasks_failed": state.tasks_failed if state else 0,
                "avg_latency_ms": state.avg_latency_ms if state else 0,
                "tokens_in": state.tokens_in if state else 0,
                "tokens_out": state.tokens_out if state else 0,
                "note": state.note if state else "",
                "updated_at": state.updated_at if state else "",
            }
        )
    return {"agents": rows}


@router.get("/{agent_id}")
def get_agent(agent_id: str) -> dict[str, Any]:
    agent = registry.get(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_agent", "message": f"No agent called '{agent_id}'."},
        )
    payload = agent.model_dump(mode="json")
    payload["chain_entries"] = [
        {"slot": slot, "provider": ref.provider, "model": ref.model} for slot, ref in agent.chain()
    ]
    if runtime.record is not None:
        path = runtime.project_path
        if path is not None:
            payload["history"] = context_engine.agent_history(path, agent_id)
            payload["recent_verdicts"] = memory.read_audit_history(path, limit=10)
    return payload


@router.put("/{agent_id}/chain")
@guarded("update model chain")
def update_chain(agent_id: str, payload: UpdateChainRequest) -> dict[str, Any]:
    try:
        agent = registry.set_chain(
            agent_id,
            payload.primary.model_dump(),
            [item.model_dump() for item in payload.fallbacks],
        )
    except RegistryError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_chain", "message": str(exc)}) from exc
    _resume_paused(f"fallback chain for {agent_id} changed")
    return {"agent": agent.model_dump(mode="json"), "coverage": _coverage_for(agent_id)}


@router.patch("/{agent_id}")
@guarded("update agent")
def update_agent(agent_id: str, payload: UpdateAgentRequest) -> dict[str, Any]:
    changes = payload.model_dump(exclude_none=True)
    if "system_prompt" in changes:
        registry.set_prompt(agent_id, changes.pop("system_prompt"))
    if changes:
        registry.update_agent(agent_id, **changes)
    agent = registry.require(agent_id)
    if not agent.enabled:
        bus.publish(
            "agent_note",
            {"message": f"{agent_id} was disabled. Its tasks stay queued and are not lost."},
        )
    return {"agent": agent.model_dump(mode="json")}


@router.post("")
@guarded("add agent")
def add_agent(payload: AddAgentRequest) -> dict[str, Any]:
    """Add an agent from config alone - the acceptance test for the config-driven roster."""
    try:
        agent = registry.add_agent(payload.model_dump())
    except RegistryError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_agent", "message": str(exc)}) from exc
    bus.publish("agent_added", {"agent_id": agent.id, "name": agent.name})
    return {"agent": agent.model_dump(mode="json")}


@router.delete("/{agent_id}")
@guarded("remove agent")
def remove_agent(agent_id: str) -> dict[str, Any]:
    if registry.get(agent_id) is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_agent", "message": "Not found."})
    removed = registry.remove_agent(agent_id)
    bus.publish("agent_removed", {"agent_id": agent_id})
    return {"removed": removed, "note": "Tasks assigned to it keep their history; reopen them from the board."}


@router.post("/{agent_id}/reset")
@guarded("reset agent")
def reset_agent(agent_id: str) -> dict[str, Any]:
    """Restore one agent's definition from the shipped roster."""
    from ...agents.roster import AGENTS

    default = next((item for item in AGENTS if item["id"] == agent_id), None)
    if default is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "no_default", "message": f"'{agent_id}' is not part of the shipped roster."},
        )
    registry.update_agent(agent_id, **{key: value for key, value in default.items() if key != "id"})
    return {"agent": registry.require(agent_id).model_dump(mode="json")}


@router.post("/reset-all")
@guarded("reset roster")
def reset_all() -> dict[str, Any]:
    """Restore every agent to the shipped default. The user's own agents are not touched."""
    agents = registry.reset_to_defaults()
    return {"agents": [item.model_dump(mode="json") for item in agents.values()]}


@router.get("/{agent_id}/history")
@guarded("read agent history")
def history(agent_id: str, limit: int = 40) -> dict[str, Any]:
    context = require_project()
    return {
        "history": context_engine.agent_history(context.path, agent_id, limit=limit),
        "failure_patterns": context_engine.failure_patterns(context.path),
    }


@router.get("/{agent_id}/context-preview")
@guarded("preview agent context")
def context_preview(agent_id: str, task_id: str = "") -> dict[str, Any]:
    context = require_project()
    return context_engine.preview(context.path, agent_id, task_id)


@router.get("/{agent_id}/prompt-preview")
@guarded("preview agent prompt")
def prompt_preview(agent_id: str, task_id: str) -> dict[str, Any]:
    """The exact system and user messages for a task. Nothing about an agent call is hidden."""
    context = require_project()
    messages = context_engine.messages_for_task(context.path, agent_id, task_id)
    return {
        "messages": messages,
        "stats": {
            "system_chars": len(messages[0]["content"]) if messages else 0,
            "user_chars": len(messages[-1]["content"]) if messages else 0,
        },
    }


@router.get("/{agent_id}/usage")
@guarded("read agent usage")
def usage(agent_id: str) -> dict[str, Any]:
    context = require_project()
    tasks = [task for task in context.bus.all() if task.assigned_to == agent_id]
    attempts = [attempt for task in tasks for attempt in task.attempts]
    verdicts = [task.auditor_verdict for task in tasks if task.auditor_verdict]
    return {
        "agent_id": agent_id,
        "tasks": len(tasks),
        "attempts": len(attempts),
        "failed_attempts": sum(1 for attempt in attempts if not attempt.success),
        "tokens_in": sum(attempt.tokens_in for attempt in attempts),
        "tokens_out": sum(attempt.tokens_out for attempt in attempts),
        "providers": sorted({attempt.provider for attempt in attempts}),
        "models": sorted({attempt.model for attempt in attempts}),
        "average_score": round(sum(item.score for item in verdicts) / len(verdicts), 2) if verdicts else None,
        "declines": sum(1 for item in verdicts if item.is_declined),
    }


def _coverage_for(agent_id: str) -> dict[str, Any]:
    for row in registry.coverage():
        if row["agent_id"] == agent_id:
            return row
    return {}


def _resume_paused(reason: str) -> None:
    """The spec's rule B.2.4: a chain edit or a new key resumes what was paused."""
    bus_ = runtime.bus()
    if bus_ is None:
        return
    try:
        resumed = bus_.resume_all_paused(reason=reason)
    except Exception as exc:  # pragma: no cover - resume is best effort
        log.info("Could not resume paused tasks: %s", exc)
        return
    if resumed:
        log.info("Resumed %s paused task(s): %s", len(resumed), reason)
        runtime.wake()

