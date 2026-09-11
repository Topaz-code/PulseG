"""Planning Agent: the ``/grillme`` intake and the gate that guards the build.

The conversation is the model's job; the *gate* is not. :func:`grillme.can_hand_off` decides
whether the design is complete, using structured fields the model must fill in, so a fluent
"I think we are ready" cannot start an expensive build with half a design.

State lives in ``memory/planning_state.json`` (files win over the SQLite index). Every turn
appends to ``memory/chat.json`` so the Planning rail can be replayed after a restart.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..core.atomic import atomic_write_json, read_json
from ..core.config import new_id
from ..core.models import ChatMessage, Status, Task, iso, utcnow
from ..orchestration import memory
from ..skills import grillme
from .base import AgentContext, AgentRunResult, note_agent_state, run_text_agent

log = logging.getLogger(__name__)

AGENT_ID = "planning_agent"
STATE_FILE = "planning_state.json"

EMPTY_STATE: dict[str, Any] = {
    "concept": "",
    "title": "",
    "genre": "",
    "genre_scores": {},
    "answered_themes": [],
    "questions_asked": [],
    "mechanics": [],
    "characters": [],
    "art_direction": "",
    "reference_images": [],
    "level_count": None,
    "play_length_minutes": None,
    "linear": None,
    "godot_version": "",
    "ledger": {},
    "handoff_requested": False,
    "handoff_confirmed": False,
    "phase_started": False,
}


# --- state ----------------------------------------------------------------------------


def state_path(project_path: Path) -> Path:
    return project_path / "memory" / STATE_FILE


def read_state(project_path: Path) -> dict[str, Any]:
    payload = read_json(state_path(project_path), default={}) or {}
    merged = dict(EMPTY_STATE)
    if isinstance(payload, dict):
        merged.update(payload)
    return merged


def write_state(project_path: Path, state: dict[str, Any]) -> dict[str, Any]:
    atomic_write_json(state_path(project_path), state)
    return state


def get_ledger(state: dict[str, Any]) -> grillme.Ledger:
    ledger = grillme.evaluate_ledger(
        mechanics=state.get("mechanics") or [],
        characters=state.get("characters") or [],
        art_direction=state.get("art_direction") or "",
        reference_images=state.get("reference_images") or [],
        level_count=state.get("level_count"),
        play_length_minutes=state.get("play_length_minutes"),
        linear=state.get("linear"),
        godot_version=state.get("godot_version") or "",
    )
    return ledger


def refresh_ledger(project_path: Path) -> dict[str, Any]:
    state = read_state(project_path)
    ledger = get_ledger(state)
    state["ledger"] = ledger.as_dict()
    return write_state(project_path, state)


def handoff_status(project_path: Path) -> dict[str, Any]:
    """What the Command Bar's BUILD button and the API both read."""
    state = read_state(project_path)
    ledger = get_ledger(state)
    allowed, blockers = grillme.can_hand_off(ledger)
    return {
        "allowed": allowed,
        "blockers": blockers,
        "ledger": ledger.as_dict(),
        "genre": state.get("genre", ""),
        "concept": state.get("concept", ""),
        "confirmed": bool(state.get("handoff_confirmed")),
        "started": bool(state.get("phase_started")),
        "title": state.get("title", ""),
    }


# --- intake flow ------------------------------------------------------------------------


def begin(project_path: Path, concept: str, *, title: str = "") -> dict[str, Any]:
    """Start an intake from a pasted game idea."""
    genre, scores = grillme.detect_genre(concept)
    state = read_state(project_path)
    state.update(
        {
            "concept": concept.strip(),
            "title": title.strip() or _title_from(concept),
            "genre": state.get("genre") or genre,
            "genre_scores": scores,
        }
    )
    if not state.get("mechanics"):
        state["mechanics"] = []
    write_state(project_path, state)

    scaffold = grillme.draft_gdd_scaffold(state["genre"], concept, state["title"])
    memory.write_gdd(project_path, scaffold)
    append_chat(
        project_path,
        ChatMessage(
            message_id=new_id("msg"),
            role="human",
            content=concept.strip(),
            meta={"kind": "concept"},
        ),
    )
    questions = next_questions(project_path)
    append_chat(
        project_path,
        ChatMessage(
            message_id=new_id("msg"),
            role="planning_agent",
            agent_id=AGENT_ID,
            content=grillme.question_batch_text(questions),
            meta={"kind": "questions", "themes": sorted({item["theme"] for item in questions})},
        ),
    )
    return {"state": read_state(project_path), "questions": questions, "genre": state["genre"]}


def append_chat(project_path: Path, message: ChatMessage) -> None:
    memory.append_chat(project_path, message)


def next_questions(project_path: Path, *, limit: int = grillme.MAX_QUESTIONS_PER_TURN) -> list[dict[str, str]]:
    state = read_state(project_path)
    ledger = get_ledger(state)
    questions = grillme.next_questions(state.get("answered_themes") or [], ledger, limit=limit)
    asked = list(state.get("questions_asked") or [])
    for question in questions:
        if question["text"] not in asked:
            asked.append(question["text"])
    state["questions_asked"] = asked[-60:]
    write_state(project_path, state)
    return questions


def record_answers(project_path: Path, answers: str) -> dict[str, Any]:
    """Store the human's answers and re-evaluate the ledger through the model."""
    answers = (answers or "").strip()
    if not answers:
        raise ValueError("Empty answer batch.")
    append_chat(
        project_path, ChatMessage(message_id=new_id("msg"), role="human", content=answers, meta={"kind": "answers"})
    )
    state = read_state(project_path)
    updated, extraction = _extract_design(project_path, state, answers)
    # Keep the report of what the last answer produced. It is the difference between "the checklist
    # did not move" and "the checklist did not move because no provider answered" - the second one
    # is actionable, and the rail can only say it if the attempt was recorded.
    updated["extraction"] = {**extraction, "at": iso(utcnow())}
    write_state(project_path, updated)
    ledger = get_ledger(updated)
    updated["ledger"] = ledger.as_dict()
    write_state(project_path, updated)

    questions = next_questions(project_path) if not ledger.ready else []
    if questions:
        append_chat(
            project_path,
            ChatMessage(
                message_id=new_id("msg"),
                role="planning_agent",
                agent_id=AGENT_ID,
                content=grillme.question_batch_text(questions),
                meta={"kind": "questions"},
            ),
        )
    elif ledger.ready:
        append_chat(
            project_path,
            ChatMessage(
                message_id=new_id("msg"),
                role="planning_agent",
                agent_id=AGENT_ID,
                content=(
                    "The design is complete enough to build. Here is the draft GDD for you to "
                    "confirm - nothing is queued until you press Send to Build Team."
                ),
                meta={"kind": "handoff_ready"},
            ),
        )
    return {
        "state": read_state(project_path),
        "ledger": ledger.as_dict(),
        "questions": questions,
        "extraction": extraction,
    }


def _extract_design(
    project_path: Path, state: dict[str, Any], answers: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Turn free-text answers into the structured fields the gate reads.

    Returns the updated state and a short report of what happened, because the failure mode this
    function used to have was silence: with no working provider it returned the state untouched,
    the ledger never moved, and the screen kept asking the same questions with no explanation.
    """
    from .registry import registry

    agent = registry.get(AGENT_ID)
    if agent is None:
        return state, {
            "ok": False,
            "reason": "the Planning Agent is missing from the roster",
            "changed": [],
        }
    context = AgentContext(agent=agent, project_path=project_path)
    context.extra["planner_state"] = {
        key: state.get(key)
        for key in (
            "genre",
            "mechanics",
            "characters",
            "art_direction",
            "level_count",
            "play_length_minutes",
            "linear",
            "godot_version",
        )
    }
    run = run_text_agent(
        context,
        extra_instruction=(
            "The human answered these intake questions. Merge the answers into the current "
            "design state and return the updated structured object.\n\n"
            f"CURRENT DESIGN STATE:\n{json.dumps(context.extra['planner_state'], indent=1)}\n\n"
            f"NEW ANSWERS:\n{answers}\n\n"
            "Rules: never drop an existing mechanic or character; add or update entries; a "
            "character is only complete with role, personality, abilities and ai_behaviour; a "
            "mechanic is only complete with rule, inputs, feedback and failure state."
        ),
        require_json=True,
    )
    if not run.ok or not run.payload:
        return state, {
            "ok": False,
            "reason": (
                run.pause_reason
                or "the Planning Agent could not read that answer, so the design state is unchanged"
            ),
            "changed": [],
            "needs_key": bool(run.paused),
        }
    payload = run.payload
    updated = dict(state)
    changed: list[str] = []
    for key in ("mechanics", "characters", "reference_images"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            updated[key] = value
            changed.append(key)
    for key in ("art_direction", "level_count", "play_length_minutes", "linear", "godot_version", "title"):
        value = payload.get(key)
        if value not in (None, ""):
            updated[key] = value
            changed.append(key)
    themes = payload.get("answered_themes")
    if isinstance(themes, list):
        existing = list(state.get("answered_themes") or [])
        for theme in themes:
            if isinstance(theme, str) and theme not in existing:
                existing.append(theme)
        updated["answered_themes"] = existing
    elif state.get("genre"):
        # Infer progress so the question engine moves forward even if the model omits it.
        existing = list(state.get("answered_themes") or [])
        if updated.get("mechanics") and "mechanics" not in existing:
            existing.append("mechanics")
        if updated.get("characters") and "characters" not in existing:
            existing.append("characters")
        if updated.get("art_direction") and "art" not in existing:
            existing.append("art")
        updated["answered_themes"] = existing
        if existing:
            changed.append("answered_themes")
    return updated, {
        "ok": bool(changed),
        "reason": "" if changed else "the answers did not add or change any design field",
        "changed": sorted(set(changed)),
    }


def request_handoff(project_path: Path) -> dict[str, Any]:
    """The human pressed Send to Build Team. Still gated."""
    state = read_state(project_path)
    ledger = get_ledger(state)
    allowed, blockers = grillme.can_hand_off(ledger)
    state["handoff_requested"] = True
    state["ledger"] = ledger.as_dict()
    write_state(project_path, state)
    if not allowed:
        return {"allowed": False, "blockers": blockers, "ledger": ledger.as_dict()}
    draft = grillme.draft_gdd_scaffold(state["genre"], state["concept"], state["title"])
    draft += "\n## DESIGN CONFIRMATION\n"
    draft += _render_design(state)
    return {"allowed": True, "blockers": [], "draft_gdd": draft, "ledger": ledger.as_dict()}


def _render_design(state: dict[str, Any]) -> str:
    lines = ["", f"- Title: {state.get('title', '')}", f"- Genre: {state.get('genre', '')}"]
    lines.append("- Mechanics:")
    lines.extend(
        [f"  - {item.get('name', 'unnamed')}: {item.get('rule', '')}" for item in state.get("mechanics") or []]
    )
    lines.append("- Characters:")
    lines.extend(
        [
            f"  - {item.get('name', 'unnamed')} ({item.get('role', '')}): "
            f"behaviour {item.get('ai_behaviour', '')}"
            for item in state.get("characters") or []
        ]
    )
    lines.append(f"- Art direction: {state.get('art_direction', '')}")
    lines.append(f"- Levels: {state.get('level_count')}  Length: {state.get('play_length_minutes')} min")
    lines.append(f"- Structure: {'linear' if state.get('linear') else 'branching/open'}")
    lines.append("")
    return "\n".join(lines)


def confirm_handoff(project_path: Path, *, confirmed_text: str = "") -> dict[str, Any]:
    """The human explicitly confirmed the draft. Now the GDD is finalised."""
    state = read_state(project_path)
    ledger = get_ledger(state)
    allowed, blockers = grillme.can_hand_off(ledger)
    if not allowed:
        return {"finalised": False, "blockers": blockers}
    existing = memory.read_gdd(project_path)
    body = existing
    if "## DESIGN CONFIRMATION" not in existing:
        body = existing + "\n## DESIGN CONFIRMATION\n" + _render_design(state)
    if confirmed_text.strip():
        body += "\n\n### Human confirmation\n" + confirmed_text.strip() + "\n"
    memory.write_gdd(project_path, body)
    memory.write_gdd_section(
        project_path,
        "SUMMARY",
        _summary_from_state(state),
    )
    state["handoff_confirmed"] = True
    write_state(project_path, state)
    append_chat(
        project_path,
        ChatMessage(
            message_id=new_id("msg"),
            role="system",
            agent_id=AGENT_ID,
            content="Design confirmed by the human. memory/gdd.md is final and Phase 0 can start.",
            meta={"kind": "handoff_confirmed"},
        ),
    )
    return {"finalised": True, "blockers": [], "gdd": body}


def _summary_from_state(state: dict[str, Any]) -> str:
    concept = state.get("concept", "")
    mechanics = ", ".join(str(item.get("name", "")) for item in (state.get("mechanics") or [])[:6])
    characters = ", ".join(str(item.get("name", "")) for item in (state.get("characters") or [])[:6])
    return (
        f"{concept}\n\n"
        f"- Genre: {state.get('genre', '')}\n"
        f"- Core mechanics: {mechanics or 'defined in MECHANICS'}\n"
        f"- Characters: {characters or 'defined in CHARACTERS'}\n"
        f"- Art direction: {state.get('art_direction', '')}\n"
        f"- Scope: {state.get('level_count')} levels, ~{state.get('play_length_minutes')} minutes\n"
        f"- Status: design confirmed, entering Phase 0 (Planning).\n"
    )


def mark_phase_started(project_path: Path) -> None:
    state = read_state(project_path)
    state["phase_started"] = True
    write_state(project_path, state)


# --- agent entry point -------------------------------------------------------------------


def run(ctx: AgentContext) -> AgentRunResult:
    """A planning task on the bus: ask the next batch, or update the design from context."""
    if ctx.task and ctx.task.kind == "planning_questions":
        questions = next_questions(ctx.project_path)
        run = AgentRunResult(
            ok=bool(questions),
            agent_id=AGENT_ID,
            output=grillme.question_batch_text(questions),
            payload={"questions": questions},
        )
        if not questions:
            run.ok = True
            run.output = "No open questions: the design ledger is complete."
        note_agent_state(ctx, run)
        return run
    run = run_text_agent(ctx, require_json=True)
    if not run.paused:
        note_agent_state(ctx, run)
    return run


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID and task.status in (Status.PENDING, Status.IN_PROGRESS)


def _title_from(concept: str) -> str:
    words = [word for word in (concept or "").split() if word]
    if not words:
        return "Untitled Game"
    return " ".join(words[:6]).strip(".,:;").title()
