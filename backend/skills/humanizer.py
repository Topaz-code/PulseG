"""humanizer - keeps agent-written prose and dialogue from reading like a machine wrote it.

The Auditor runs this on every narrative submission, and the Story Writer runs it on itself
before submitting. It is deliberately *not* a spell-and-grammar pass: it looks for the four
things that actually give machine writing away in games, and it names the specific line.

1. **Suspiciously even rhythm.** Human dialogue is bursty - a three-word reply, then a
   thirty-word explanation. Machine dialogue has a low coefficient of variation in line
   length because the model writes to a consistent "reasonable sentence" template.
2. **No contractions and no discourse markers.** Real speech is full of "I'm", "don't",
   "well", "look", "I mean". If a hundred words of dialogue contain none of them, it reads
   like a briefing document read aloud.
3. **Voice collapse.** Two characters who differ in function-word profile by almost nothing
   are the same voice wearing two name tags. This is measured the same way authorship
   attribution measures it, so the finding is reproducible rather than a matter of taste.
4. **On-the-nose emotion.** "I am angry with you" is exposition; a person who slams a cup
   down is angry. Named emotions in dialogue are flagged with the line quoted.

Every threshold here is a heuristic, stated in ``metrics['thresholds']`` so nobody has to
guess where a number came from. The function-word comparison is the only part that inherits
a published method.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from .base import SkillResult, count_phrase_hits, mean, stdev

SKILL = "humanizer"

#: Words that carry almost no meaning and therefore carry style. This is the operational
#: version of "the most frequent words" used in authorship attribution: function words are
#: the top of every frequency list in every corpus, which is why the method travels.
FUNCTION_WORDS: tuple[str, ...] = (
    "a", "about", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both",
    "but", "by", "can", "did", "do", "does", "doing", "don", "down", "during", "each",
    "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here",
    "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "just",
    "me", "more", "most", "my", "no", "nor", "not", "now", "of", "off", "on", "once",
    "only", "or", "other", "our", "out", "over", "own", "same", "she", "should", "so",
    "some", "such", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "under", "until", "up", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who", "why", "will",
    "with", "would", "you", "your",
)

CONTRACTIONS = re.compile(r"\b\w+(?:'|’)t\b|\b\w+'(?:s|re|ll|ve|d|m)\b", re.IGNORECASE)
DIALOGUE_LINE = re.compile(
    r"""^\s*(?:[-*]\s*)?(?:(?P<speaker>[A-Z][A-Za-z0-9 _.'-]{1,28}?)\s*[:\u2014-]\s*)?["“](?P<line>[^"”]{1,400})["”]\s*$""",
    re.MULTILINE,
)
SPEAKER_TAG = re.compile(r'"([^"]{1,400})"\s*(?:,|)\s*(?:said|asked|replied|shouted|whispered)\s+([A-Z][A-Za-z]+)')
ON_THE_NOSE = (
    "i am angry", "i'm angry", "i am sad", "i'm sad", "i am happy", "i'm happy",
    "i am scared", "i'm scared", "i am excited", "i'm excited", "i feel angry",
    "i feel sad", "i feel happy", "he was angry", "she was angry", "he was sad",
    "she was sad", "he was happy", "she was happy", "he felt angry", "she felt sad",
    "i am furious", "i am terrified", "i am overjoyed", "i am very upset",
)
DISCOURSE_MARKERS = (
    "well,", "look,", "listen,", "hey,", "hmm", "uh,", "um,", "i mean", "you know",
    "okay,", "right,", "so,", "actually,", "honestly,", "anyway,",
)
#: Supplementary rhythm markers. These are *heuristics*, not findings from the paper.
THRESHOLDS: dict[str, float] = {
    "min_dialogue_lines_for_rhythm": 8,
    "line_length_cv_floor": 0.35,          # below this the rhythm is suspiciously even
    "min_words_for_contraction_check": 80,
    "contraction_rate_floor": 0.8,          # per 100 words of dialogue
    "min_lines_for_marker_check": 14,
    "discourse_marker_floor": 0.5,          # per 100 lines
    "voice_distance_floor": 0.05,           # cosine distance between two speakers' profiles
    "min_lines_per_speaker_for_voice": 3,
}


def extract_dialogue(text: str) -> list[dict[str, str]]:
    """Pull quoted lines (and their speakers, when tagged) out of prose or script text."""
    lines: list[dict[str, str]] = []
    for match in DIALOGUE_LINE.finditer(text or ""):
        speaker = (match.group("speaker") or "").strip().rstrip(":")
        lines.append({"speaker": speaker.lower(), "line": match.group("line").strip()})
    for match in SPEAKER_TAG.finditer(text or ""):
        line, speaker = match.group(1).strip(), match.group(2).strip()
        if not any(entry["line"] == line for entry in lines):
            lines.append({"speaker": speaker.lower(), "line": line})
    return lines


def _function_vector(text: str) -> list[float]:
    words = re.findall(r"[a-zA-Z']+", (text or "").lower())
    total = len(words) or 1
    counts: dict[str, int] = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    return [counts.get(word, 0) / total for word in FUNCTION_WORDS]


def _cosine_distance(first: Sequence[float], second: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(first, second))
    norm_a = sum(a * a for a in first) ** 0.5
    norm_b = sum(b * b for b in second) ** 0.5
    if not norm_a or not norm_b:
        return 0.0
    return max(0.0, 1.0 - dot / (norm_a * norm_b))


def _voice_distances(lines: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    """Cosine distance between each pair of speakers' function-word profiles.

    Same measurement family as Burrows' Delta and Cosine Delta in authorship attribution:
    if two speakers land within a few percent of each other on function words, they are
    stylistically the same speaker no matter how different their vocabulary looks.
    """
    by_speaker: dict[str, list[str]] = {}
    for entry in lines:
        speaker = entry.get("speaker") or ""
        if not speaker:
            continue
        by_speaker.setdefault(speaker, []).append(entry.get("line", ""))
    usable = {
        speaker: texts
        for speaker, texts in by_speaker.items()
        if len(texts) >= THRESHOLDS["min_lines_per_speaker_for_voice"]
    }
    pairs: list[dict[str, Any]] = []
    names = sorted(usable)
    vectors = {name: _function_vector(" ".join(usable[name])) for name in names}
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            pairs.append(
                {
                    "speakers": [first, second],
                    "cosine_distance": round(_cosine_distance(vectors[first], vectors[second]), 4),
                }
            )
    return sorted(pairs, key=lambda pair: pair["cosine_distance"])


def _summarise(result: SkillResult, line_count: int) -> None:
    if result.passed:
        result.summary = (
            f"Reads like a person wrote it. {line_count} dialogue line(s) checked for rhythm, "
            "contractions, voice separation and on-the-nose emotion."
        )
    else:
        result.summary = (
            f"{len(result.findings)} thing(s) make this read machine-written across "
            f"{line_count} dialogue line(s). Fix the high-severity items before resubmitting."
        )


def review(
    text: str,
    *,
    context: str = "narrative",
    document_kind: str = "narrative",
    speakers: Iterable[str] = (),
) -> SkillResult:
    """Run every humanizer check over ``text`` and return one combined result."""
    result = SkillResult(skill=SKILL)
    result.metrics["thresholds"] = dict(THRESHOLDS)
    result.metrics["context"] = context
    result.metrics["document_kind"] = document_kind
    result.metrics["method_note"] = (
        "Rhythm, contraction and emotion checks are heuristics with the thresholds recorded "
        "above. The cross-speaker comparison reuses the function-word profile method from "
        "authorship attribution (the same family as Burrows' Delta), which is why its "
        "distances are reported to four decimal places."
    )
    body = text or ""
    if not body.strip():
        result.summary = "Nothing to review: the submission was empty."
        result.add("Empty submission passed to the humanizer", severity="medium", location=context)
        return result

    lines = extract_dialogue(body)
    result.metrics["dialogue_lines"] = len(lines)
    words = re.findall(r"[a-zA-Z']+", body)
    result.metrics["words"] = len(words)

    # --- 1. rhythm ------------------------------------------------------------------
    if len(lines) >= THRESHOLDS["min_dialogue_lines_for_rhythm"]:
        lengths = [len(re.findall(r"[a-zA-Z']+", entry["line"])) for entry in lines]
        average = mean(lengths)
        spread = stdev(lengths)
        cv = spread / average if average else 0.0
        result.metrics["line_length_mean"] = round(average, 2)
        result.metrics["line_length_cv"] = round(cv, 3)
        if cv < THRESHOLDS["line_length_cv_floor"]:
            result.add(
                f"Dialogue rhythm is suspiciously even: every line lands near {average:.0f} words "
                f"(cv {cv:.2f}, floor {THRESHOLDS['line_length_cv_floor']})",
                severity="high" if len(lines) >= 20 else "medium",
                location=context,
                recommendation=(
                    "Rewrite at least a third of the lines as fragments or single-word replies. "
                    "People interrupt, trail off, and answer a question with one word."
                ),
            )
        if cv > 1.2:
            result.notes.append(
                "Line length varies more than typical speech; check the long lines are not "
                "monologues by a character who should be terse."
            )

    # --- 2. contractions and discourse markers --------------------------------------
    dialogue_text = " ".join(entry["line"] for entry in lines) or body
    dialogue_words = re.findall(r"[a-zA-Z']+", dialogue_text)
    if len(dialogue_words) >= THRESHOLDS["min_words_for_contraction_check"]:
        contractions = CONTRACTIONS.findall(dialogue_text)
        rate = 100.0 * len(contractions) / max(1, len(dialogue_words))
        result.metrics["contraction_rate_per_100w"] = round(rate, 2)
        if rate < THRESHOLDS["contraction_rate_floor"]:
            result.add(
                f"Almost no contractions in {len(dialogue_words)} words of speech "
                f"({rate:.2f} per 100 words)",
                severity="high",
                location=context,
                recommendation=(
                    'Speech contracts: "I am" becomes "I\'m", "do not" becomes "don\'t". '
                    "Keep full forms only where a character is being deliberately formal."
                ),
            )
    if len(lines) >= THRESHOLDS["min_lines_for_marker_check"]:
        hits = count_phrase_hits(dialogue_text, DISCOURSE_MARKERS)
        per_hundred = 100.0 * len(hits) / max(1, len(lines))
        result.metrics["discourse_marker_rate_per_100_lines"] = round(per_hundred, 2)
        if per_hundred < THRESHOLDS["discourse_marker_floor"]:
            result.add(
                "No hesitation, filler or address in a long stretch of dialogue",
                severity="medium",
                location=context,
                recommendation=(
                    'Give at least one character a verbal habit - "look,", "I mean", "hmm". '
                    "It is the cheapest way to make a voice recognisable."
                ),
            )

    # --- 3. voice collapse ------------------------------------------------------------
    pairs = _voice_distances(lines)
    result.metrics["voice_pairs"] = pairs[:8]
    for pair in pairs[:3]:
        if pair["cosine_distance"] < THRESHOLDS["voice_distance_floor"]:
            result.add(
                f"{pair['speakers'][0].title()} and {pair['speakers'][1].title()} sound like the "
                f"same person (function-word distance {pair['cosine_distance']:.4f}, floor "
                f"{THRESHOLDS['voice_distance_floor']})",
                severity="high",
                location=context,
                recommendation=(
                    "Differentiate the voices structurally, not with catchphrases: one speaks in "
                    "short declaratives, the other in hedged compound sentences. Re-measure after."
                ),
            )

    # --- 4. on-the-nose emotion -------------------------------------------------------
    for phrase, excerpt in count_phrase_hits(body, ON_THE_NOSE):
        result.add(
            f'Stated emotion instead of shown: "{phrase}"',
            severity="medium",
            location=context,
            excerpt=excerpt,
            recommendation="Replace with a physical action or a choice the character makes.",
        )

    # --- 5. punctuation life ------------------------------------------------------------
    dashes = len(re.findall(r"—|--", body))
    ellipses = len(re.findall(r"\.\.\.|…", body))
    result.metrics["interruptions"] = dashes + ellipses
    if len(lines) >= THRESHOLDS["min_lines_for_marker_check"] and dashes + ellipses == 0:
        result.notes.append(
            "No dashes or ellipses anywhere: nobody interrupts anybody, and nothing trails off."
        )

    _summarise(result, len(lines))
    return result


def fix_instructions(result: SkillResult) -> str:
    """The rejection note a human sees, phrased as work for the writing agent to do."""
    if result.passed:
        return ""
    lines = ["The writing reads machine-made. Fix these specifically:"]
    ordered = sorted(result.findings, key=lambda finding: {"high": 0, "medium": 1}.get(finding.severity, 2))
    for finding in ordered[:6]:
        line = f"- {finding.message}"
        if finding.recommendation:
            line += f" {finding.recommendation}"
        lines.append(line)
    return "\n".join(lines)


def review_files(files: Iterable[tuple[str, str]]) -> SkillResult:
    """Review several dialogue files as one voice-space, which is the only way voice collapse shows."""
    combined: list[dict[str, str]] = []
    labels: list[str] = []
    for label, text in files:
        labels.append(label)
        combined.extend(extract_dialogue(text))
    # Re-render with the speaker tags preserved, so the cross-speaker comparison still works.
    rendered = "\n".join(
        f'{entry.get("speaker") or "unknown"}: "{entry.get("line", "")}"' for entry in combined
    )
    result = review(rendered, context=", ".join(labels))
    result.metrics["files"] = labels
    result.metrics["dialogue_lines"] = len(combined)
    return result
