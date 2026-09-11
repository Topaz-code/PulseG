"""grillme - the Planning Agent's intake interrogation and handoff gate (spec E.1).

Two jobs, and the second one is the important one:

1. **Ask well.** Genre-aware question batches of at most four, ordered so each answer
   unblocks the most later decisions. Never a forty-question wall.
2. **Refuse to hand off.** "Send to Build Team" stays disabled until every named character
   has a defined behaviour, every core mechanic has a rule, art direction is specified or
   referenced, and scope is bounded.

The gate is deterministic. A model may *propose* that the ledger is complete, but
:func:`evaluate_ledger` decides, using the structured signals the Planning Agent returned.
That is deliberate: a fluent "great, I have everything I need" must not be able to start an
expensive build with half a design.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .base import SkillResult, count_phrase_hits

SKILL = "grillme"

MAX_QUESTIONS_PER_TURN = 4

#: Character AI movement/behaviour patterns the gate accepts (spec E.1).
BEHAVIOUR_PATTERNS = (
    "patrol",
    "chase",
    "scripted",
    "reactive",
    "stationary",
    "flee",
    "wander",
    "formation",
    "custom",
)

#: Genre detection signals. Ordered: the first genre whose score is highest wins, ties are
#: reported so the human can correct the guess in one click.
GENRE_SIGNALS: dict[str, tuple[str, ...]] = {
    "board_game": ("board game", "dice", "tile placement", "turn-based board", "pieces",
                   "deck", "card game", "hex grid", "chess", "checkers", "boardgame"),
    "rpg": ("rpg", "experience points", "level up", "inventory", "party", "quest",
            "stats", "skill tree", "turn-based combat", "dungeon crawl"),
    "metroidvania": ("metroidvania", "ability gate", "backtrack", "double jump", "map unlock",
                     "interconnected map"),
    "platformer": ("platformer", "jump", "run and gun", "precision platform", "wall jump",
                   "collectathon"),
    "roguelike": ("roguelike", "roguelite", "procedural", "permadeath", "run-based",
                  "randomly generated"),
    "narrative_adventure": ("narrative", "adventure", "story", "dialogue", "visual novel",
                            "point and click", "choices", "branching story", "cutscene"),
    "puzzle": ("puzzle", "match", "sokoban", "logic", "grid puzzle", "physics puzzle"),
    "simulation": ("simulation", "farming", "management", "tycoon", "city builder",
                   "life sim", "economy"),
    "open_world": ("open world", "sandbox", "exploration", "free roam", "map to explore"),
    "arcade": ("arcade", "shooter", "shmup", "twin stick", "bullet hell", "endless runner",
               "high score", "wave-based"),
    "tower_defense": ("tower defense", "tower defence", "waves", "lanes", "defend the base"),
    "card_battler": ("deckbuilder", "deck builder", "card battler", "draw a card", "energy cost"),
    "horror": ("horror", "survival", "sanctuary", "stealth", "chase sequence", "darkness"),
}

#: GDD template per genre. Genre-adaptive on purpose: a board game has no "level layout".
GDD_TEMPLATES: dict[str, list[str]] = {
    "board_game": ["SUMMARY", "COMPONENTS", "RULES", "TURN STRUCTURE", "WIN CONDITIONS",
                   "PLAYER COUNT AND LENGTH", "ART DIRECTION", "DECISIONS"],
    "rpg": ["SUMMARY", "WORLD", "CHARACTERS", "MECHANICS", "PROGRESSION", "LEVELS",
            "ITEMS", "DIALOGUE SYSTEM", "ART DIRECTION", "DECISIONS"],
    "metroidvania": ["SUMMARY", "WORLD", "CHARACTERS", "MECHANICS", "ABILITIES AND GATES",
                     "MAP LAYOUT", "PROGRESSION", "ART DIRECTION", "DECISIONS"],
    "platformer": ["SUMMARY", "MECHANICS", "MOVEMENT FEEL", "LEVELS", "ENEMIES",
                   "PICKUPS", "CHARACTERS", "ART DIRECTION", "DECISIONS"],
    "roguelike": ["SUMMARY", "CORE LOOP", "MECHANICS", "PROCEDURAL RULES", "ROOMS",
                  "ENEMIES", "ITEMS", "RUN LENGTH", "ART DIRECTION", "DECISIONS"],
    "narrative_adventure": ["SUMMARY", "WORLD", "CHARACTERS", "SCENE FLOW",
                            "MECHANICS", "DIALOGUE ECONOMY", "BRANCHING", "ART DIRECTION",
                            "DECISIONS"],
    "puzzle": ["SUMMARY", "CORE RULE", "MECHANICS", "LEVEL PROGRESSION", "DIFFICULTY CURVE",
               "UI", "ART DIRECTION", "DECISIONS"],
    "simulation": ["SUMMARY", "SYSTEMS", "ECONOMY", "MECHANICS", "PROGRESSION", "UI",
                   "CHARACTERS AND AI", "ART DIRECTION", "DECISIONS"],
    "open_world": ["SUMMARY", "WORLD", "REGIONS", "MECHANICS", "TRAVERSAL", "CHARACTERS",
                   "PROGRESSION", "ART DIRECTION", "DECISIONS"],
    "arcade": ["SUMMARY", "CORE LOOP", "MECHANICS", "ENEMIES AND WAVES", "SCORING",
               "DIFFICULTY", "UI", "ART DIRECTION", "DECISIONS"],
    "tower_defense": ["SUMMARY", "CORE LOOP", "TOWERS", "ENEMIES AND WAVES", "ECONOMY",
                      "MAP AND LANES", "WIN CONDITIONS", "ART DIRECTION", "DECISIONS"],
    "card_battler": ["SUMMARY", "CORE LOOP", "CARDS", "ENERGY AND TURN STRUCTURE",
                     "ENCOUNTERS", "DECK BUILDING", "WIN CONDITIONS", "ART DIRECTION",
                     "DECISIONS"],
    "horror": ["SUMMARY", "PREMISE", "CHARACTERS", "MECHANICS", "TENSION SYSTEM", "LEVELS",
               "AUDIO DIRECTION", "ART DIRECTION", "DECISIONS"],
    "generic": ["SUMMARY", "WORLD", "CHARACTERS", "MECHANICS", "LEVELS", "ART DIRECTION",
                "DECISIONS"],
}

#: Questions per theme, most load-bearing first. Each question is chosen because its answer
#: changes what gets built, not because it is nice to know.
QUESTION_BANK: dict[str, list[str]] = {
    "mechanics": [
        "What is the player's moment-to-moment action loop, in one sentence?",
        "What does failure look like, and what does it cost the player?",
        "Pick your single most important mechanic: what are its exact rules, including values (speeds, cooldowns, damage) and any edge cases?",
        "Which mechanics from your reference games are you deliberately not copying?",
        "How does the player learn each mechanic without a tutorial wall of text?",
    ],
    "characters": [
        "Who does the player control, and what can that character do that nobody else can?",
        "List every character the player will meet. For each: role, personality, abilities, and how they behave in the world (patrol, chase, scripted, reactive).",
        "Which character is the antagonist, and what do they want that puts them in the player's way?",
        "Do any characters have dialogue? Roughly how many lines, and does anything they say change what the player can do?",
    ],
    "art": [
        "What art style is this, in enough detail that an artist could match it (pixel size, palette, outline, lighting direction)?",
        "Which existing game or artwork is the closest visual reference, and can you attach an image?",
        "How does the player tell apart friend, enemy, hazard and pickup at a glance?",
        "What does the game look like in the first five seconds?",
    ],
    "scope": [
        "How many levels or scenes does a complete version have?",
        "How long should one play session be, and can a player finish it in one sitting?",
        "Is the game linear or branching, and how much content sits off the critical path?",
        "What is explicitly out of scope for version one?",
    ],
    "tech": [
        "Which Godot 4.x version are you running, and do you have export templates installed?",
        "Which platforms must the final export support?",
        "Will this be keyboard only, or does it need gamepad and touch input?",
        "Does the game need saving and loading, and if so what exactly is saved?",
    ],
}


@dataclass
class Ledger:
    """Completeness ledger. The gate reads exactly these five booleans plus ``missing``."""

    mechanics_complete: bool = False
    characters_complete: bool = False
    art_complete: bool = False
    scope_complete: bool = False
    tech_complete: bool = True  # Godot version / existing project; defaulted, not blocking
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return (
            self.mechanics_complete
            and self.characters_complete
            and self.art_complete
            and self.scope_complete
            and self.tech_complete
        )

    @property
    def completion(self) -> float:
        flags = [
            self.mechanics_complete,
            self.characters_complete,
            self.art_complete,
            self.scope_complete,
            self.tech_complete,
        ]
        return sum(1 for flag in flags if flag) / len(flags)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mechanics_complete": self.mechanics_complete,
            "characters_complete": self.characters_complete,
            "art_complete": self.art_complete,
            "scope_complete": self.scope_complete,
            "tech_complete": self.tech_complete,
            "completion": round(self.completion, 3),
            "missing": list(self.missing),
            "notes": list(self.notes),
            "ready": self.ready,
        }


@dataclass
class Character:
    """A named character and the behaviour the gate requires for it."""

    name: str = ""
    role: str = ""
    personality: str = ""
    abilities: list[str] = field(default_factory=list)
    ai_behaviour: str = ""

    def complete(self) -> bool:
        return bool(
            self.name
            and self.role
            and self.personality
            and self.abilities
            and self.ai_behaviour
            and self.ai_behaviour in BEHAVIOUR_PATTERNS
        )

    def behaviour_pattern(self) -> str:
        text = (self.ai_behaviour or "").lower()
        for pattern in BEHAVIOUR_PATTERNS:
            if pattern in text:
                return pattern
        return ""

    def gaps(self) -> list[str]:
        missing: list[str] = []
        if not self.role:
            missing.append("role")
        if not self.personality:
            missing.append("personality")
        if not self.abilities:
            missing.append("abilities")
        if not self.behaviour_pattern():
            missing.append("AI behaviour pattern (patrol, chase, scripted or reactive)")
        return missing


# --- genre and templates ----------------------------------------------------------------


def detect_genre(concept: str) -> tuple[str, dict[str, int]]:
    """Score the concept against the genre signal table. Returns (genre, all scores)."""
    lowered = (concept or "").lower()
    scores: dict[str, int] = {}
    for genre, signals in GENRE_SIGNALS.items():
        score = sum(1 for signal in signals if signal in lowered)
        scores[genre] = score
    best = max(scores.items(), key=lambda item: item[1]) if scores else ("generic", 0)
    if best[1] == 0:
        return "generic", scores
    return best[0], scores


def gdd_template_for(genre: str) -> list[str]:
    return GDD_TEMPLATES.get(genre, GDD_TEMPLATES["generic"])


def draft_gdd_scaffold(genre: str, concept: str, title: str = "") -> str:
    """The draft GDD: every section present with a prompt, nothing invented."""
    sections = gdd_template_for(genre)
    lines = [
        f"# {title or 'Untitled Game'}",
        "",
        f"_Genre: {genre.replace('_', ' ')}._",
        "",
        f"**Concept as written by the human:** {concept.strip()}",
        "",
    ]
    prompts = {
        "SUMMARY": "One paragraph: what the player does, why it is fun, and what a session looks like.",
        "ART DIRECTION": "Style, palette, reference images, and how each asset class must look.",
        "DECISIONS": "Dated log of decisions and reversals. Append-only.",
        "MECHANICS": "Each mechanic with its exact rule, inputs, feedback and failure state.",
        "CHARACTERS": "Each character with role, personality, abilities and AI behaviour pattern.",
        "LEVELS": "Each level: objective, layout, challenge, and exit condition.",
        "AUDIO DIRECTION": "Music mood per area, and the SFX vocabulary for feedback.",
    }
    for section in sections:
        lines.append(f"## {section}")
        lines.append(prompts.get(section, "To be defined with the human."))
        lines.append("")
    return "\n".join(lines)


# --- questions ---------------------------------------------------------------------------


def next_questions(answered_themes: Iterable[str], ledger: Ledger, limit: int = MAX_QUESTIONS_PER_TURN) -> list[dict[str, str]]:
    """Pick the next small batch of questions, missing-ledger items first."""
    answered = set(answered_themes)
    ordered_themes: list[str] = []
    if not ledger.mechanics_complete:
        ordered_themes.append("mechanics")
    if not ledger.characters_complete:
        ordered_themes.append("characters")
    if not ledger.art_complete:
        ordered_themes.append("art")
    if not ledger.scope_complete:
        ordered_themes.append("scope")
    if not ledger.tech_complete:
        ordered_themes.append("tech")
    for theme in ("mechanics", "characters", "art", "scope", "tech"):
        if theme not in ordered_themes:
            ordered_themes.append(theme)

    out: list[dict[str, str]] = []
    for theme in ordered_themes:
        if theme in answered and len(ordered_themes) > 1:
            continue
        for index, question in enumerate(QUESTION_BANK.get(theme, [])):
            if len(out) >= limit:
                return out
            out.append({"theme": theme, "text": question, "index": str(index)})
        if len(out) >= limit:
            return out
    return out


def question_batch_text(questions: Sequence[dict[str, str]]) -> str:
    """Render a batch for the chat UI, numbered and themed."""
    if not questions:
        return "No open questions."
    lines = []
    for index, question in enumerate(questions, start=1):
        lines.append(f"{index}. [{question['theme']}] {question['text']}")
    return "\n".join(lines)


# --- the gate ------------------------------------------------------------------------------


def _as_character(entry: "Character | dict[str, Any]") -> Character:
    if isinstance(entry, Character):
        return entry
    abilities = entry.get("abilities") or []
    if isinstance(abilities, str):
        abilities = [part.strip() for part in re.split(r"[,;]", abilities) if part.strip()]
    return Character(
        name=str(entry.get("name", "")).strip(),
        role=str(entry.get("role", "")).strip(),
        personality=str(entry.get("personality", "")).strip(),
        abilities=[str(item) for item in abilities],
        ai_behaviour=str(entry.get("ai_behaviour") or entry.get("behaviour") or "").strip().lower(),
    )


def evaluate_ledger(
    *,
    mechanics: Sequence[dict[str, Any]] = (),
    characters: Sequence["Character | dict[str, Any]"] = (),
    art_direction: str = "",
    reference_images: Sequence[str] = (),
    level_count: int | None = None,
    play_length_minutes: int | None = None,
    linear: bool | None = None,
    godot_version: str = "",
) -> Ledger:
    """The gate. Deterministic, and deliberately strict.

    Mechanics are "complete" when each has an explicit rule; characters when every named
    character has a behaviour pattern; art when there is either a written direction or at
    least one reference image; scope when level count, length and linear/branching are set.
    """
    ledger = Ledger()
    missing: list[str] = []

    # --- mechanics -----------------------------------------------------------------
    if not mechanics:
        ledger.notes.append("No mechanics defined yet.")
    incomplete_mechanics: list[str] = []
    for entry in mechanics:
        name = str(entry.get("name", "unnamed")).strip()
        rule = str(entry.get("rule", "")).strip()
        if len(rule) < 12:
            incomplete_mechanics.append(name)
    if mechanics and not incomplete_mechanics:
        ledger.mechanics_complete = True
    elif incomplete_mechanics:
        missing.append(
            f"{len(incomplete_mechanics)} mechanic(s) have no defined rule: "
            + ", ".join(incomplete_mechanics[:4])
        )
    else:
        missing.append("core mechanics have no defined rules yet")

    # --- characters ------------------------------------------------------------------
    parsed = [_as_character(entry) for entry in characters]
    parsed = [character for character in parsed if character.name]
    incomplete_characters: list[tuple[str, list[str]]] = []
    for character in parsed:
        gaps = character.gaps()
        if gaps:
            incomplete_characters.append((character.name, gaps))
    if parsed and not incomplete_characters:
        ledger.characters_complete = True
    elif incomplete_characters:
        detail = "; ".join(f"{name} needs {', '.join(gaps)}" for name, gaps in incomplete_characters[:4])
        missing.append(f"characters incomplete: {detail}")
    else:
        missing.append("no characters defined yet")

    # --- art ---------------------------------------------------------------------------
    if (art_direction or "").strip() or reference_images:
        ledger.art_complete = True
        if not (art_direction or "").strip() and reference_images:
            ledger.notes.append(
                f"Art direction is carried by {len(reference_images)} reference image(s) rather "
                "than a written description; the Image Generator will derive a style lock from them."
            )
    else:
        missing.append("no art direction and no reference images")

    # --- scope --------------------------------------------------------------------------
    scope_gaps: list[str] = []
    if not level_count:
        scope_gaps.append("number of levels")
    if not play_length_minutes:
        scope_gaps.append("target session length")
    if linear is None:
        scope_gaps.append("linear or branching")
    if scope_gaps:
        missing.append("scope undefined: " + ", ".join(scope_gaps))
    else:
        ledger.scope_complete = True

    # --- tech ---------------------------------------------------------------------------
    if not godot_version:
        ledger.tech_complete = True  # The Story/Planning agent can default to the installed version.
        ledger.notes.append(
            "Godot version not specified; Phase 0 detects the installed 4.x version and records it."
        )
    else:
        ledger.tech_complete = True

    ledger.missing = missing
    return ledger


def can_hand_off(ledger: Ledger) -> tuple[bool, list[str]]:
    """Gate check used by the Command Bar's BUILD button and the build API.

    Returns ``(allowed, blockers)``. The UI shows the blockers as a checklist with the
    outstanding questions, and the button stays disabled while any remain.
    """
    if ledger.ready:
        return True, []
    blockers = list(ledger.missing)
    if not blockers:
        blockers = ["design is not yet complete enough to start building"]
    return False, blockers


def review(
    *,
    ledger: Ledger,
    concept: str = "",
    transcript: str = "",
    context: str = "planning",
) -> SkillResult:
    """Skill interface for the Auditor: is this a genuine, complete intake?

    Catches the two ways a planning phase passes without actually being done: a ledger that
    claims completeness while the transcript shows unanswered questions, and a concept so
    thin that no ledger could honestly be complete.
    """
    result = SkillResult(skill=SKILL)
    result.metrics.update(ledger.as_dict())
    result.metrics["concept_words"] = len((concept or "").split())
    result.metrics["transcript_words"] = len((transcript or "").split())

    if ledger.ready and len(concept.split()) < 15:
        result.add(
            "Ledger claims completeness but the original concept is only "
            f"{len(concept.split())} words",
            severity="high",
            location=context,
            recommendation="Confirm the design with the human in their own words before building.",
        )
    if ledger.ready and len((transcript or "").split()) < 60:
        result.add(
            "Ledger claims completeness with almost no planning conversation on record",
            severity="medium",
            location=context,
            recommendation="Ask at least the mechanics and character batches before handoff.",
        )
    vague = count_phrase_hits(concept, ("fun", "engaging", "unique", "innovative", "immersive"))
    if len(vague) >= 3:
        result.add(
            "The concept leans on unspecific praise words rather than describable mechanics",
            severity="medium",
            location=context,
            recommendation="Ask what the player physically does, and for how long.",
        )
    for item in ledger.missing:
        result.add(f"Outstanding: {item}", severity="medium", location=context)
    if ledger.ready:
        result.summary = "Design is complete enough to build."
    else:
        result.summary = f"{len(ledger.missing)} item(s) still open before the build can start."
    return result


def blockers_markdown(blockers: Sequence[str]) -> str:
    """The checklist shown under the disabled BUILD button."""
    if not blockers:
        return "Ready to build."
    return "\n".join(f"- [ ] {item}" for item in blockers)
