"""Story Writer agent: narrative, characters, dialogue, localisation-ready strings.

Two structural rules that come from the specification rather than taste:

* Dialogue lives in ``story/dialogue/*.json`` with an ``id`` per line, so the game never has
  strings hardcoded in scenes and a later translation pass is a data change, not a rewrite.
* Character canon is read from and written back to ``memory/gdd.md`` - a character invented in
  Phase 3 must exist in the GDD before Phase 4 writes dialogue for them.
"""
from __future__ import annotations

import json
import logging

from ..core.models import Task
from ..mcp.filesystem_mcp import write_project_file
from ..orchestration import memory
from ..skills import humanizer
from .base import AgentContext, AgentRunResult, apply_skill_checks, note_agent_state, run_text_agent

log = logging.getLogger(__name__)

AGENT_ID = "story_writer"


def _dialogue_to_json(payload: dict) -> tuple[dict[str, dict], list[str]]:
    """Convert the model's dialogue list into the stable id-keyed JSON the engine reads."""
    problems: list[str] = []
    out: dict[str, dict] = {}
    entries = payload.get("dialogue") or []
    if not isinstance(entries, list):
        return {}, ["`dialogue` was not a list."]
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"dialogue entry {index} was not an object.")
            continue
        line_id = str(entry.get("id") or "").strip()
        speaker = str(entry.get("speaker") or "").strip()
        text = str(entry.get("text") or "").strip()
        if not line_id or not text:
            problems.append(f"dialogue entry {index} is missing an id or text.")
            continue
        if not speaker:
            problems.append(f"{line_id}: no speaker - the engine cannot render an unattributed line.")
            speaker = "narrator"
        out[line_id] = {
            "speaker": speaker,
            "text": text,
            "next": entry.get("next") or "",
            "conditions": entry.get("conditions") or {},
            "voice": entry.get("voice") or "",
        }
    return out, problems


def _character_canon(payload: dict) -> str:
    characters = payload.get("characters") or []
    lines: list[str] = []
    for character in characters[:20]:
        if not isinstance(character, dict):
            continue
        name = character.get("name", "Unnamed")
        lines.append(f"### {name}")
        for field in ("role", "personality", "abilities", "ai_behaviour", "arc"):
            value = character.get(field)
            if value:
                lines.append(f"- **{field.replace('_', ' ').title()}:** {value}")
        lines.append("")
    return "\n".join(lines).strip()


def run(ctx: AgentContext) -> AgentRunResult:
    run_result = run_text_agent(ctx, document_kind="narrative", require_json=True)
    if run_result.paused:
        note_agent_state(ctx, run_result)
        return run_result

    payload = run_result.payload or {}
    dialogue, problems = _dialogue_to_json(payload)
    run_result.problems.extend(problems)

    if dialogue:
        chapter = str(payload.get("chapter") or "chapter_01").lower().replace(" ", "_")
        relative = f"story/dialogue/{chapter}.json"
        write_project_file(ctx.project_path, relative, json.dumps(dialogue, indent=2, ensure_ascii=False) + "\n")
        run_result.artifacts.append(relative)

    canon = _character_canon(payload)
    if canon:
        existing = memory.gdd_summary(ctx.project_path, max_lines=1000)
        if "## CHARACTERS" in existing:
            memory.write_gdd_section(ctx.project_path, "CHARACTERS", canon)
        run_result.artifacts.append("memory/gdd.md#CHARACTERS")

    for note in payload.get("world_notes") or []:
        if isinstance(note, str) and note.strip():
            memory.write_gdd_section(ctx.project_path, "WORLD", note.strip())
            run_result.artifacts.append("memory/gdd.md#WORLD")
            break

    # Narrative is the place AI slop is most obvious: triads, "a testament to", adverb
    # stacking. Run the humanizer on the dialogue text specifically.
    dialogue_text = "\n".join(entry["text"] for entry in dialogue.values())
    if dialogue_text:
        run_result.skill_results["humanizer:dialogue"] = humanizer.review(dialogue_text, context="dialogue")
    apply_skill_checks(ctx, run_result, extra_texts=[("story/dialogue", dialogue_text)] if dialogue_text else [])

    if not dialogue and not canon:
        run_result.ok = False
        run_result.error = (
            "The Story Writer produced neither dialogue nor character canon. Check the task "
            "instruction names the scene or chapter to write."
        )
    note_agent_state(ctx, run_result)
    return run_result


def character_coverage(ctx: AgentContext) -> dict:
    """Which named characters still have no dialogue - feeds the Planning Agent's gate."""
    gdd = memory.gdd_summary(ctx.project_path, max_lines=1000)
    characters = [match.strip() for match in _character_names(gdd)]
    spoken: set[str] = set()
    for path in sorted((ctx.project_path / "story" / "dialogue").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for entry in payload.values():
            if isinstance(entry, dict) and entry.get("speaker"):
                spoken.add(str(entry["speaker"]).strip().lower())
    missing = [name for name in characters if name.lower() not in spoken]
    return {"characters": characters, "with_dialogue": sorted(spoken), "without_dialogue": missing}


def _character_names(gdd: str) -> list[str]:
    names: list[str] = []
    inside = False
    for line in (gdd or "").splitlines():
        if line.strip().startswith("## "):
            inside = line.strip().upper().startswith("## CHARACTER")
            continue
        if inside and line.startswith("### "):
            names.append(line[4:].strip())
    return names


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID
