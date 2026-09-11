"""The skills are the studio's quality gate, so they are tested against real behaviour.

Where a threshold could be argued about, the test asserts the *ordering* the paper predicts
(human-shaped text scores differently from uniform text) rather than a magic number.
"""
from __future__ import annotations

import pytest

from backend.skills import adhd_filter, design_taste, grillme, harvard_shape, humanizer
from backend.skills.base import SkillResult


# --- grillme: the gate ------------------------------------------------------------------


def test_character_without_behaviour_pattern_is_incomplete():
    character = grillme.Character(name="Mira", role="pilot", personality="wry", abilities=["glide"])
    assert character.gaps() == ["AI behaviour pattern (patrol, chase, scripted or reactive)"]
    assert not character.complete()


def test_ledger_gate_blocks_until_everything_is_defined():
    ledger = grillme.evaluate_ledger(
        mechanics=[{"name": "wall jump", "rule": "The player may jump once while touching a wall."}],
        characters=[],
        art_direction="",
        level_count=None,
        play_length_minutes=None,
        linear=None,
    )
    allowed, blockers = grillme.can_hand_off(ledger)
    assert allowed is False
    assert len(blockers) >= 3
    assert not ledger.ready


def test_ledger_gate_opens_when_design_is_complete():
    ledger = grillme.evaluate_ledger(
        mechanics=[{"name": "wall jump", "rule": "Jump once while touching a wall, cooldown 0."}],
        characters=[
            {
                "name": "Mira",
                "role": "player",
                "personality": "wry",
                "abilities": ["glide", "wall jump"],
                "ai_behaviour": "scripted",
            },
            {
                "name": "Warden",
                "role": "antagonist",
                "personality": "patient",
                "abilities": ["laser"],
                "ai_behaviour": "patrol then chase",
            },
        ],
        art_direction="32x32 pixel art, four-tone palette, top-left light",
        level_count=6,
        play_length_minutes=25,
        linear=False,
    )
    allowed, blockers = grillme.can_hand_off(ledger)
    assert allowed is True, blockers
    assert ledger.ready


def test_mechanics_without_a_rule_do_not_count():
    ledger = grillme.evaluate_ledger(mechanics=[{"name": "dash", "rule": ""}])
    assert ledger.mechanics_complete is False
    assert any("dash" in item for item in ledger.missing)


def test_genre_detection_and_template():
    genre, scores = grillme.detect_genre("A pixel platformer where you jump between lighthouses")
    assert genre == "platformer"
    assert scores["platformer"] >= 1
    template = grillme.gdd_template_for(genre)
    assert "MOVEMENT FEEL" in template
    scaffold = grillme.draft_gdd_scaffold(genre, "concept", "Lighthouse")
    assert "## MOVEMENT FEEL" in scaffold


def test_question_batches_stay_small_and_themed():
    ledger = grillme.evaluate_ledger()
    questions = grillme.next_questions([], ledger)
    assert 1 <= len(questions) <= grillme.MAX_QUESTIONS_PER_TURN
    assert all("theme" in question and "text" in question for question in questions)


def test_grillme_review_penalises_unanswered_design():
    ledger = grillme.evaluate_ledger(mechanics=[{"name": "x", "rule": ""}])
    result = grillme.review(ledger=ledger, concept="fun game", transcript="")
    assert not result.passed
    assert result.metrics["mechanics_complete"] is False


# --- harvard_shape: the published method ------------------------------------------------


UNIFORM = "\n\n".join(
    [
        "The lighthouse keeper walks to the door and opens it. The keeper looks at the sea "
        "and the sea looks back at the keeper. The keeper thinks about the light and the light "
        "thinks about the keeper." * 3,
    ]
    * 6
)

VARIED = "\n\n".join(
    [
        "Rain. The keeper did not move for a long time. When he finally did, it was to check the "
        "wick, and the wick was fine, and that was the problem: everything was fine, which meant "
        "the wreck offshore last Tuesday had nothing to do with him, which meant the letter in "
        "his coat pocket was the only thing on this island that was not fine. He read it again. "
        "Twelve words. He had counted them eleven times.",
        "The lamp burned. Ships came and went. Nobody landed. He cooked potatoes, ate them "
        "standing up, and did not wash the pan.",
        "'You are not sleeping,' said the gull, which was not a gull, and the keeper said "
        "'I know', and neither of them mentioned the obvious.",
        "He remembered the harbourmaster's ledger, the way the ink had pooled where the man "
        "had hesitated. Nobody hesitates over a routine sinking. He began to write his own "
        "account, in the margins, in pencil, because pencil could be erased and ink could not. "
        "That, he decided, was a plan.",
        "Morning was grey and loud. The supply boat did not come. The radio spoke in numbers. "
        "He answered in numbers. It felt like lying. It felt awake.",
        "By the third night he had stopped counting. The light turned, the sea did not care, "
        "and the keeper wrote the last line of his account in ink, deliberately, and signed it.",
        "Gulls, then. Then nothing. Then the sound of an engine being switched off, which is a "
        "different sound from an engine being quiet, and he knew the difference because he had "
        "spent nine years learning which sounds meant rescue and which meant company. He put "
        "his boots on the wrong feet and did not notice until the door.",
    ]
)


def test_burrows_delta_is_zero_for_identical_texts():
    first = harvard_shape.profile("the cat sat on the mat and the dog looked on")
    second = harvard_shape.profile("the cat sat on the mat and the dog looked on")
    harvard_shape.z_normalise([first, second])
    assert harvard_shape.burrows_delta(first, second) == pytest.approx(0.0)
    assert harvard_shape.cosine_delta(first, second) == pytest.approx(0.0)


def test_uniform_text_scores_more_uniform_than_varied_text():
    uniform = harvard_shape.review(UNIFORM, context="uniform", document_kind="narrative")
    varied = harvard_shape.review(VARIED, context="varied", document_kind="narrative")
    assert uniform.metrics["distributional_check_ran"] is True
    assert varied.metrics["distributional_check_ran"] is True
    assert uniform.metrics["delta_cv"] < varied.metrics["delta_cv"]
    assert uniform.metrics["average_linkage_height"] <= varied.metrics["average_linkage_height"]


def test_average_linkage_height_is_normalised():
    texts = [("a", VARIED), ("b", UNIFORM), ("c", "Someone else entirely wrote this sentence, differently.")]
    profiles = [harvard_shape.profile(text, label=label) for label, text in texts]
    harvard_shape.z_normalise(profiles)
    height = harvard_shape.average_linkage_height(profiles)
    assert 0.0 <= height <= 1.0


def test_compare_to_corpus_refuses_to_conclude_on_a_small_corpus():
    report = harvard_shape.compare_to_corpus("one short text", human_texts=["a"], machine_texts=["b"])
    assert report["verdict"] == "inconclusive"
    assert "at least three" in report["reason"]


def test_compare_to_corpus_places_text_nearer_its_own_kind():
    machine_samples = [UNIFORM, UNIFORM.replace("keeper", "watchman"), UNIFORM.replace("sea", "water")]
    human_samples = [
        VARIED,
        VARIED.replace("lighthouse", "harbour"),
        VARIED.replace("pencil", "chalk"),
    ]
    report = harvard_shape.compare_to_corpus(
        machine_samples[0], human_texts=human_samples, machine_texts=machine_samples
    )
    assert report["verdict"] in {"closer_to_machine", "closer_to_human"}
    assert report["delta_to_human"] != report["delta_to_machine"]


def test_harvard_shape_reports_its_method_and_paper():
    result = harvard_shape.review(VARIED, context="story", document_kind="narrative")
    assert result.metrics["paper"] == "s41599-025-05986-3"
    assert "Burrows" in result.metrics["method"]
    assert "heuristics" in result.metrics["thresholds_note"]


def test_structure_check_needs_both_signals():
    """A flat structure is only called out when sizes AND style are uniform."""
    flat = "\n\n".join(f"## Section {index}\n" + ("word " * 60).strip() for index in range(6))
    result = harvard_shape.review(flat, context="doc", document_kind="documentation")
    assert result.metrics["structure"]["section_size_cv"] < 0.2


# --- humanizer ------------------------------------------------------------------------------


def test_even_dialogue_without_contractions_is_flagged():
    lines = "\n".join('"We should proceed to the northern gate now."' for _ in range(10))
    result = humanizer.review(lines, context="dialogue")
    checks = " ".join(finding.message for finding in result.findings)
    assert "rhythm" in checks.lower()
    assert "contractions" in checks.lower()


def test_natural_dialogue_passes_the_rhythm_checks():
    natural = "\n".join(
        [
            '"Did you hear that?"',
            '"No," I said, and then, because the wind had changed and I am not a brave person, '
            '"yes. Let\'s go. Now."',
            '"Wait —"',
            '"I\'m not waiting. Look, whatever it is, it knows the path better than we do. Come on."',
            '"Hmm."',
            '"Hmm is not a plan."',
            '"It\'s the beginning of one. Give me a second, would you?"',
            '"One."',
            '"You\'re impossible, you know that?"',
        ]
    )
    result = humanizer.review(natural, context="dialogue")
    rhythm = [finding for finding in result.findings if "rhythm" in finding.message.lower()]
    assert not rhythm


def test_on_the_nose_emotion_is_quoted_back():
    text = '"I am angry with you," she said.'
    result = humanizer.review(text, context="line")
    assert any("stated emotion" in finding.message.lower() for finding in result.findings)


def test_voice_collapse_detected_between_two_speakers():
    lines = []
    for _ in range(5):
        lines.append('Ada: "I have considered the problem and I believe the solution is clear."')
        lines.append('Bram: "I have considered the problem and I believe the solution is clear."')
    result = humanizer.review("\n".join(lines), context="scene")
    assert any("same person" in finding.message for finding in result.findings)


def test_humanizer_handles_empty_input():
    result = humanizer.review("", context="nothing")
    assert isinstance(result, SkillResult)
    assert result.findings


# --- design_taste ---------------------------------------------------------------------------


def test_palette_contrast_meets_wcag_aa():
    rows = {row["pair"]: row for row in design_taste.contrast_pairs()}
    assert rows["primary button label"]["passes"] is True
    assert rows["body text on app background"]["passes"] is True


def test_contrast_ratio_matches_known_values():
    assert design_taste.contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, rel=0.01)
    assert design_taste.contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0)


GOOD_SCREEN = """
export function TaskBoard() {
  const tasks = useTasks();
  return (
    <section className="p-8 gap-6">
      <button data-primary-action className="bg-canary-yellow text-charcoal-bg px-6 py-3 hover:opacity-90 disabled:opacity-50 focus-visible:ring-2" aria-busy={isSaving} onClick={save}>
        Approve task
      </button>
      {tasks.length === 0 ? <EmptyState title="No tasks yet" body="Send an idea to the build team to get started." /> : null}
      <ul>{(tasks ?? []).map((task) => <li key={task.id}>{task.title}</li>)}</ul>
    </section>
  );
}
"""

BAD_SCREEN = """
export function BadScreen() {
  return (
    <div className="p-[7px] mt-1.5">
      <button data-primary-action className="bg-[#ff00aa]" onClick={go}>Submit payload</button>
      <button data-primary-action onClick={go} className="outline-none">Also submit</button>
      <ul>{items.map((item) => <li>{item.name}</li>)}</ul>
      <div className="transition duration-1000 animate-spin" />
    </div>
  );
}
"""


def test_good_screen_passes_the_checklist():
    analysis = design_taste.analyse_screen_source(GOOD_SCREEN, "TaskBoard")
    failing = [item for item in analysis["findings"] if item["severity"] != "pass"]
    assert failing == [], failing


def test_bad_screen_fails_on_jargon_spacing_primary_action_and_colour():
    analysis = design_taste.analyse_screen_source(BAD_SCREEN, "Bad")
    checks = {item["check"]: item for item in analysis["findings"]}
    assert checks["one primary action"]["severity"] == "high"
    assert checks["8px spacing grid"]["severity"] == "medium"
    assert checks["no jargon in user-facing copy"]["severity"] == "medium"
    assert checks["no ad-hoc colours"]["severity"] == "high"


def test_corrected_screen_passes():
    screen = """
    <div className="p-6 gap-4">
      <button data-primary-action className="bg-canary-yellow text-charcoal-bg hover:opacity-90 disabled:opacity-50 aria-busy focus-visible:ring-2" onClick={go}>
        Approve task
      </button>
      {items.length === 0 ? <p>Nothing here yet.</p> : null}
      {items.map((item) => <div key={item.id}>{item.name}</div>)}
    </div>
    """
    analysis = design_taste.analyse_screen_source(screen, "Fixed")
    failing = [item for item in analysis["findings"] if item["severity"] != "pass"]
    assert failing == [], failing


def test_checklist_has_eight_items():
    assert len(design_taste.checklist()) == 8


# --- adhd_filter ------------------------------------------------------------------------------


def test_context_bundle_sends_only_the_summary_and_progress_tail(project):
    from backend.orchestration import memory, projects

    path = projects.project_path_of(project)
    memory.append_progress(path, "seeded the player scene", agent="programmer", task_id="TASK_001")
    memory.write_gdd_section(path, "SUMMARY", "A platformer about a lighthouse keeper.")
    memory.write_gdd_section(path, "CHARACTERS", "Mira: wry pilot, patrols the north wall.")
    bundle = adhd_filter.build_context(path, "programmer")
    rendered = bundle.render()
    assert "lighthouse keeper" in rendered
    assert "patrols the north wall" not in rendered  # only ## SUMMARY is shared
    assert "seeded the player scene" in rendered
    assert bundle.stats["rendered_tokens"] > 0


def test_context_budget_trims_and_records_what_it_dropped(project):
    from backend.orchestration import memory, projects

    path = projects.project_path_of(project)
    for index in range(30):
        memory.append_progress(path, f"line {index} " + "detail " * 20, agent="programmer")
    bundle = adhd_filter.build_context(path, "programmer", budget_tokens=200)
    assert bundle.omitted, "the filter must say what it dropped"
    assert bundle.stats["rendered_tokens"] <= 400


def test_submission_review_catches_a_missing_expected_output(project, bus):
    task = bus.create(
        assigned_to="programmer",
        instruction="Create `player.gd` with movement.",
        title="player",
        expected_outputs=["godot_project/scripts/player.gd"],
        file_claims=["godot_project/scripts/player.gd"],
    )
    result = adhd_filter.review(task=task, submission="I wrote the player script.", artifacts=[])
    assert not result.passed
    assert any("player.gd" in finding.message for finding in result.findings)


def test_submission_review_passes_when_outputs_exist(project, bus):
    task = bus.create(
        assigned_to="programmer",
        instruction="Create `player.gd` with movement.",
        title="player",
        expected_outputs=["godot_project/scripts/player.gd"],
    )
    result = adhd_filter.review(
        task=task,
        submission="```file: godot_project/scripts/player.gd\nextends CharacterBody2D\n```",
        artifacts=["godot_project/scripts/player.gd"],
    )
    assert result.passed, result.flags()


def test_compression_report_shows_the_saving(project):
    from backend.orchestration import memory, projects

    path = projects.project_path_of(project)
    memory.write_gdd_section(path, "CHARACTERS", "long section " * 200)
    report = adhd_filter.compression_report(path)
    assert report["gdd_tokens_full"] >= report["gdd_tokens_sent"]
