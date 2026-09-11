"""deslop - detects low-effort output, missing fields and unfinished work.

Where ``no_ai_slop`` looks at prose quality, this skill looks at *completeness*: did the
agent actually do the task, or did it produce the shape of the task?

It is the skill that catches the specific failure modes this pipeline hits in practice:

* a scene file with no ``CollisionShape2D``,
* a GDScript function that is ``pass``,
* ``# TODO`` in submitted work,
* an agent that reported ``files`` but emitted no ``file:`` block with real contents,
* a payload missing the fields the next agent needs (silent downstream breakage),
* a ``RESULT:`` line claiming work that the artifacts do not contain.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from .base import SkillResult, extract_code_blocks, strip_code

SKILL = "deslop"

STUB_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"#\s*(todo|fixme|implement|add .*later|placeholder)", "TODO comment left in submitted code"),
    (r"\bpass\s*(#.*)?$", "empty `pass` body - the function does nothing"),
    (r"\.\.\.\s*(#.*)?$", "ellipsis as an implementation body"),
    (r"\braise\s+NotImplementedError\b", "NotImplementedError left in place"),
    (r"\breturn\s+(null|None)\s*#.*(stub|placeholder|for now)", "stubbed return value"),
    (r'"\{\{.*?\}\}"', "template variable left unsubstituted"),
    (r"\bYourClassName\b|\bMyClass\b|\bfunc_name\b", "generated placeholder identifier"),
)

REQUIRED_GODOT_TERMS = {
    "collision": ("CollisionShape2D", "shape"),
    "signal": ("connect", ".emit("),
    "export": ("@export",),
    "class": ("extends", "class_name"),
}

#: Field names the orchestrator parses out of each agent's JSON. A payload missing these
#: breaks the *next* agent, which is much more expensive to debug than a clear error here.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "programmer": ("files",),
    "tester": ("verdict", "checks"),
    "auditor": ("verdict", "score", "skill_flags"),
    "documenter": ("progress_lines",),
    "researcher": ("notes_markdown",),
    "story_writer": ("files", "missions"),
    "transcriptor": ("clean_markdown",),
    "image_generator": ("generations",),
    "audio_curator": ("picked",),
    "planning_agent": ("reply", "ledger"),
    "prompter": ("dispatch",),
}


def review(
    text: str,
    *,
    agent_id: str = "",
    payload: dict[str, Any] | None = None,
    expected_outputs: Sequence[str] = (),
    artifacts: Sequence[str] = (),
    context: str = "output",
) -> SkillResult:
    """Inspect an agent submission.

    ``expected_outputs`` comes straight from the task record, which is what makes the
    "did it do the job" check objective rather than vibes-based.
    """
    result = SkillResult(skill=SKILL)
    code = strip_code(text)
    blocks = extract_code_blocks(text)
    block_paths = {path for path, _ in blocks if path}
    block_bodies = "\n".join(body for _, body in blocks)

    # 1. stubs and placeholders in code
    for pattern, label in STUB_PATTERNS:
        for match in re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE):
            result.add(
                label,
                severity="high",
                location=context,
                excerpt=code[max(0, match.start() - 50) : match.end() + 50].replace("\n", " "),
                recommendation="Implement it fully; a stub fails the Tester two tasks later.",
            )

    # 2. expected files actually present
    recorded = {item.split("#")[0].strip() for item in artifacts}
    for expected in expected_outputs:
        name = expected.split("#")[0].strip()
        if not name:
            continue
        if name in recorded:
            # Already written to disk by the agent's own writer (the Documenter edits
            # memory/gdd.md section by section); nothing to complain about.
            continue
        present = name in block_paths
        if not present:
            # Some agents legitimately write via tools instead of fenced blocks; accept a
            # mention of the exact path anywhere in the response as weaker evidence.
            present = name in text
            if present:
                result.add(
                    f"Expected output {name} is mentioned but no full file block was emitted",
                    severity="medium",
                    location=context,
                    recommendation=f"Emit the complete contents of {name} in a `file:` block.",
                )
                continue
        if not present:
            result.add(
                f"Expected output {name} was not produced",
                severity="high",
                location=context,
                recommendation=f"Produce {name} or explain precisely why it is not needed.",
            )

    # 3. Godot-specific structural checks on emitted scene/script files
    if re.search(r"```file:\s*\S*\.tscn", text):
        if "CollisionShape2D" not in text and re.search(r"CharacterBody2D|Area2D|RigidBody2D", text):
            result.add(
                "Scene uses a physics body with no CollisionShape2D child - it will run but "
                "nothing will collide",
                severity="high",
                location=context,
                recommendation=(
                    "Add a CollisionShape2D node with a real Shape2D sub-resource under "
                    "every physics body."
                ),
            )
        if re.search(r"\[gd_scene[^\]]*load_steps=(\d+)", text):
            declared = int(re.search(r"load_steps=(\d+)", text).group(1))  # type: ignore[union-attr]
            actual = len(re.findall(r"^\[(ext_resource|sub_resource)", text, re.MULTILINE)) + 1
            if declared != actual:
                result.add(
                    f"Scene header declares load_steps={declared} but {actual - 1} resources "
                    "are defined - Godot may fail to load the scene",
                    severity="high",
                    location=context,
                    recommendation=f"Set load_steps={actual} (resources + 1).",
                )

    if re.search(r"```file:\s*\S*\.gd", text):
        for label, terms in REQUIRED_GODOT_TERMS.items():
            if label == "collision":
                continue  # scene-level concern, checked above
            if not any(term in block_bodies for term in terms):
                result.add(
                    f"Script is missing expected Godot 4 construct: {label} ({' or '.join(terms)})",
                    severity="low",
                    location=context,
                    recommendation="Confirm this is intentional for this script.",
                )
        if re.search(r"\b(KinematicBody2D|RigidBody2D\.move_and_slide|yield\()", block_bodies):
            result.add(
                "Godot 3.x API used in a Godot 4 project",
                severity="high",
                location=context,
                recommendation="Use CharacterBody2D + move_and_slide() with await instead of yield.",
            )

    # 4. payload completeness
    if payload is not None:
        required = REQUIRED_FIELDS.get(agent_id, ())
        for field_name in required:
            if field_name not in payload:
                result.add(
                    f'Response payload is missing required field "{field_name}"',
                    severity="high",
                    location=f"{agent_id} payload",
                    recommendation=(
                        f"Add `{field_name}` to the JSON response; the orchestrator parses it."
                    ),
                )
            elif payload.get(field_name) in (None, "", [], {}):
                result.add(
                    f'Field "{field_name}" is present but empty',
                    severity="medium",
                    location=f"{agent_id} payload",
                    recommendation=f"Fill in `{field_name}` or state why it is empty.",
                )

    # 5. claims vs artifacts
    claimed = re.findall(r"RESULT:\s*(.+)", text)
    if claimed and not blocks and payload is None:
        result.add(
            "Output claims a result but contains no files or structured payload",
            severity="medium",
            location=context,
            excerpt=claimed[0][:160],
            recommendation="Emit the actual artifacts, not just a summary of them.",
        )

    # 6. suspiciously short submissions
    if len(text.strip()) < 120 and expected_outputs:
        result.add(
            f"Submission is only {len(text.strip())} characters but {len(expected_outputs)} "
            "outputs were expected",
            severity="high",
            location=context,
            recommendation="The task was not completed. Produce the files.",
        )

    result.metrics.update(
        {
            "characters": len(text),
            "code_blocks": len(blocks),
            "expected_outputs": len(expected_outputs),
            "stub_hits": len([f for f in result.findings if f.severity == "high"]),
        }
    )
    highs = [f for f in result.findings if f.severity == "high"]
    result.summary = (
        f"{len(highs)} blocking completeness issue(s): "
        + "; ".join(f.message for f in highs[:3])
        if highs
        else "Submission is structurally complete."
    )
    return result
