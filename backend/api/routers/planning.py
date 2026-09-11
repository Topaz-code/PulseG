"""Planning: the ``/grillme`` intake, the design ledger, and the build handoff gate.

The Command Bar's BUILD button reads ``/api/planning/gate``; it stays disabled while that
endpoint returns blockers. The gate is evaluated in ``skills.grillme``, not here, so a client
cannot talk its way past it.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from ...agents import planning_agent
from ...core.events import bus
from ...orchestration import memory, projects
from ...runtime import runtime
from ...skills import grillme
from ..deps import guarded, require_project
from ..schemas import IntakeAnswerRequest, IntakeConfirmRequest, IntakeRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/planning", tags=["planning"])


@router.get("/state")
@guarded("read planning state")
def state() -> dict[str, Any]:
    context = require_project()
    stored = planning_agent.read_state(context.path)
    ledger = planning_agent.get_ledger(stored)
    allowed, blockers = grillme.can_hand_off(ledger)
    return {
        "state": stored,
        "ledger": ledger.as_dict(),
        "allowed": allowed,
        "blockers": blockers,
        "checklist": grillme.blockers_markdown(blockers),
        "genre": stored.get("genre", ""),
        "questions": grillme.next_questions(stored.get("answered_themes") or [], ledger),
        "chat": [message.model_dump(mode="json") for message in memory.read_chat(context.path)][-80:],
        "concept": stored.get("concept", ""),
        # What the last answer actually produced, so the rail can explain a checklist that did not
        # move instead of leaving the human to wonder whether the studio heard them.
        "extraction": stored.get("extraction") or {},
        "confirmed": bool(stored.get("handoff_confirmed")),
        "started": bool(stored.get("phase_started")),
    }


@router.post("/intake")
@guarded("start intake")
def intake(payload: IntakeRequest) -> dict[str, Any]:
    """Start (or restart) the interrogation from a pasted idea."""
    context = require_project()
    result = planning_agent.begin(context.path, payload.concept, title=payload.title)
    bus.publish(
        "intake_started",
        {"project_id": context.project_id, "genre": result["genre"], "questions": len(result["questions"])},
    )
    return {
        "state": result["state"],
        "questions": result["questions"],
        "question_text": grillme.question_batch_text(result["questions"]),
        "genre": result["genre"],
    }


@router.post("/answer")
@guarded("record intake answers")
def answer(payload: IntakeAnswerRequest) -> dict[str, Any]:
    context = require_project()
    result = planning_agent.record_answers(context.path, payload.answers)
    return {
        **result,
        "question_text": grillme.question_batch_text(result.get("questions") or []),
        "checklist": grillme.blockers_markdown(result["ledger"].get("missing") or []),
    }


@router.get("/questions")
@guarded("read intake questions")
def questions() -> dict[str, Any]:
    context = require_project()
    batch = planning_agent.next_questions(context.path)
    return {"questions": batch, "text": grillme.question_batch_text(batch)}


@router.get("/gate")
@guarded("read build gate")
def gate() -> dict[str, Any]:
    """The single source of truth for whether the build may start."""
    context = require_project()
    status = planning_agent.handoff_status(context.path)
    if projects.reconciliation_blocks_new_work(context.record):
        status = {
            **status,
            "allowed": False,
            "blockers": [
                *status.get("blockers", []),
                "the project you connected has not been reconciled yet - approve that task first",
            ],
        }
    return {
        **status,
        "checklist": grillme.blockers_markdown(status["blockers"]),
        "labels": {
            "mechanics": "every core mechanic has a defined rule",
            "characters": "every named character has role, personality, abilities and behaviour",
            "art": "art direction is specified or a reference image is attached",
            "scope": "level count, session length and linear/branching are decided",
        },
    }


@router.post("/handoff")
@guarded("request handoff")
def handoff() -> dict[str, Any]:
    """The human pressed Send to Build Team. Returns the draft GDD for explicit confirmation."""
    context = require_project()
    result = planning_agent.request_handoff(context.path)
    if not result["allowed"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "design_incomplete",
                "message": (
                    "The design is not complete yet. Keep answering the Planning Agent's "
                    "questions - nothing is queued until every item is defined."
                ),
                "blockers": result["blockers"],
            },
        )
    return result


@router.post("/confirm")
@guarded("confirm handoff")
def confirm(payload: IntakeConfirmRequest) -> dict[str, Any]:
    """Explicit confirmation. This is the moment ``gdd.md`` becomes final and Phase 0 starts."""
    context = require_project()
    if projects.reconciliation_blocks_new_work(context.record):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "reconciliation_pending",
                "message": (
                    "PulseG Studio is still reading the project you connected. Approve the "
                    "reconciliation task on the Task Board first, so the team builds on what "
                    "already exists instead of guessing."
                ),
                "action": "open_board",
            },
        )
    result = planning_agent.confirm_handoff(context.path, confirmed_text=payload.confirmed_text)
    if not result.get("finalised"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "design_incomplete",
                "message": "The design ledger is still incomplete, so the build cannot start.",
                "blockers": result.get("blockers", []),
            },
        )
    planning_agent.mark_phase_started(context.path)
    runtime.wake()
    bus.publish("build_started", {"project_id": context.project_id})
    return {**result, "next": "The Prompter is seeding Phase 0 tasks. Watch the Task Board."}


@router.get("/gdd")
@guarded("read gdd")
def gdd() -> dict[str, Any]:
    context = require_project()
    return {
        "markdown": memory.read_gdd(context.path),
        "sections": memory.gdd_sections(context.path),
        "summary": memory.gdd_summary(context.path),
    }


@router.get("/chat")
@guarded("read planning chat")
def chat(limit: int = 200) -> dict[str, Any]:
    context = require_project()
    messages = memory.read_chat(context.path)
    return {"messages": [message.model_dump(mode="json") for message in messages[-max(1, min(limit, 500)):]]}


@router.post("/seed-tasks")
@guarded("seed first tasks")
def seed_tasks() -> dict[str, Any]:
    """Create the Phase 1 task set. Only allowed after the design is confirmed."""
    context = require_project()
    state = planning_agent.read_state(context.path)
    if projects.reconciliation_blocks_new_work(context.record):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "reconciliation_pending",
                "message": (
                    "An existing project must be reconciled into the design document before new "
                    "build tasks are queued. Approve the reconciliation task on the Task Board."
                ),
                "action": "open_board",
            },
        )
    if not state.get("handoff_confirmed"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "design_not_confirmed",
                "message": (
                    "The design has not been confirmed yet. Finish the intake and press Send to "
                    "Build Team first."
                ),
            },
        )
    dispatcher = runtime.dispatcher()
    if dispatcher is None:
        raise HTTPException(status_code=409, detail={"error": "no_active_project", "message": "No project open."})
    created = dispatcher.seed_first_tasks(genre=state.get("genre", ""))
    runtime.wake()
    return {"created": created, "note": "The dispatch loop will start working through these now."}


@router.get("/templates")
def templates() -> dict[str, Any]:
    """The genre-adaptive GDD templates, so the UI can show what a design will contain."""
    return {
        "genres": sorted(grillme.GDD_TEMPLATES.keys()),
        "templates": grillme.GDD_TEMPLATES,
        "questions": grillme.QUESTION_BANK,
        "behaviour_patterns": list(grillme.BEHAVIOUR_PATTERNS),
    }


@router.get("/design-doc")
@guarded("read design doc")
def design_doc() -> dict[str, Any]:
    """The draft GDD plus the section-by-section view for the GDD/Story screen."""
    context = require_project()
    text = memory.read_gdd(context.path)
    sections = []
    import re

    for match in re.finditer(r"^##\s+(.+?)\s*$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL):
        sections.append({"title": match.group(1).strip(), "body": match.group(2).strip()})
    return {"markdown": text, "sections": sections, "versioned_by": "git"}
