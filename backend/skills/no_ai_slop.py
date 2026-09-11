"""no_ai_slop - detects generic filler, boilerplate and vague text.

What it catches, and why each one is a real failure rather than a style preference:

* **Banned connectives** ("delve into", "it's not just X, it's Y", "in today's world")
  signal that the model reached for a default sentence instead of the one this project
  needed.
* **Triple-list padding** - "fast, fun, and rewarding" - is the classic way to say nothing
  in three words. Real design text commits to one specific claim.
* **Hedging** ("various", "somewhat", "generally") hides missing decisions.
* **Placeholder text** ("Lorem ipsum", "TBD", "example dialogue") means the task was not
  actually done.
* **Preamble/postamble** ("Certainly!", "I hope this helps") wastes the human's attention
  and, worse, hides the real output.

Deliberately *not* flagged: long sentences, simple words, or technical density. Those are
not slop.
"""
from __future__ import annotations

import re
from typing import Iterable

from .base import (
    HEDGE_WORDS,
    SkillResult,
    count_phrase_hits,
    sentences,
    strip_code,
    strip_markup,
    words,
)

SKILL = "no_ai_slop"

#: Multi-word phrases that almost always indicate default model output.
BANNED_PHRASES: tuple[str, ...] = (
    "delve into",
    "delving into",
    "it's not just",
    "it is not just",
    "not just a",
    "but a testament to",
    "a testament to",
    "in today's world",
    "in today's fast-paced",
    "in the realm of",
    "realm of possibilities",
    "tapestry of",
    "rich tapestry",
    "navigate the complexities",
    "navigating the complexities",
    "at the end of the day",
    "when it comes to",
    "it's important to note",
    "it is important to note",
    "it's worth noting",
    "needless to say",
    "last but not least",
    "in conclusion",
    "in summary,",
    "unlock the potential",
    "unleash the power",
    "elevate your",
    "seamlessly integrate",
    "game-changer",
    "cutting-edge",
    "state-of-the-art",
    "robust and scalable",
    "dive deep into",
    "embark on a journey",
    "a journey of",
    "harness the power",
    "paradigm shift",
    "synergy",
    "leverage the",
    "myriad of",
    "plethora of",
    "as an ai",
    "i hope this helps",
    "feel free to",
    "let me know if you",
    "in this article",
    "without further ado",
)

PREAMBLE_RE = re.compile(
    r"^\s*(certainly|of course|sure[,!]|absolutely|great question|here('s| is) (the|a|your)\b)",
    re.IGNORECASE | re.MULTILINE,
)

TRIPLE_LIST_RE = re.compile(
    r"\b(\w+),\s+(\w+),?\s+and\s+(\w+)\b",
)

PLACEHOLDER_RE = re.compile(
    r"\b(lorem ipsum|tbd|todo|fixme|placeholder|dummy|example text|sample text|"
    r"insert here|xxx+|foo bar|your name here|coming soon)\b",
    re.IGNORECASE,
)

#: Phrases that are fine in moderation but slop when repeated.
SOFT_PHRASES: tuple[str, ...] = (
    "vibrant",
    "immersive",
    "engaging",
    "stunning",
    "captivating",
    "dynamic",
    "innovative",
    "exciting",
    "unique",
    "intuitive",
    "powerful",
)


def review(text: str, *, context: str = "output", strict: bool = False) -> SkillResult:
    """Run the detector over an agent's output.

    ``context`` is a label ("dialogue", "code", "gdd") used in messages so the human knows
    where the problem is. Code fences are ignored: a variable named ``placeholder`` in real
    code is not slop.
    """
    result = SkillResult(skill=SKILL)
    prose = strip_markup(text)
    code = strip_code(text)
    word_list = words(prose)
    word_count = len(word_list) or 1

    # 1. banned phrases
    for phrase, excerpt in count_phrase_hits(prose, BANNED_PHRASES):
        result.add(
            f'Generic model phrasing: "{phrase}"',
            severity="high" if strict else "medium",
            location=context,
            excerpt=excerpt,
            recommendation=(
                f'Replace the sentence containing "{phrase}" with one that states a '
                "project-specific fact (a name, a number, a mechanic, a constraint)."
            ),
        )

    # 2. assistant preamble
    for match in PREAMBLE_RE.finditer(prose[:600]):
        result.add(
            "Response opens with an assistant preamble instead of the work",
            severity="low",
            location=context,
            excerpt=match.group(0),
            recommendation="Delete the preamble; start with the deliverable.",
        )
        break

    # 3. triple-list padding (only when it is decoration, not specification)
    triples = TRIPLE_LIST_RE.findall(prose)
    vague_triples = [
        triple
        for triple in triples
        if all(len(item) < 12 for item in triple) and not any(char.isdigit() for item in triple for char in item)
    ]
    if len(vague_triples) >= 3 or (vague_triples and word_count < 200):
        sample = ", ".join(vague_triples[0]) if vague_triples else ""
        result.add(
            f"{len(vague_triples)} decorative three-item lists - padding rather than content",
            severity="medium",
            location=context,
            excerpt=sample,
            recommendation=(
                "Keep at most one list and make each item carry information that changes "
                "what gets built."
            ),
        )

    # 4. hedging density
    hedge_hits = [w for w in word_list if w in HEDGE_WORDS]
    hedge_ratio = len(hedge_hits) / word_count
    result.metrics["hedge_ratio"] = round(hedge_ratio, 4)
    if hedge_ratio > 0.02 or len(hedge_hits) > 12:
        unique = sorted(set(hedge_hits))[:6]
        result.add(
            f"Hedging is doing the work of a decision ({len(hedge_hits)} hedge words, "
            f"{hedge_ratio * 100:.1f}% of the text)",
            severity="medium",
            location=context,
            excerpt=", ".join(unique),
            recommendation="Replace each hedge with the actual number, range or choice.",
        )
    elif hedge_ratio > 0.012:
        result.add(
            f"Some hedging ({len(hedge_hits)} instances) - mostly acceptable",
            severity="low",
            location=context,
            excerpt=", ".join(sorted(set(hedge_hits))[:5]),
            recommendation="Tighten the two or three softest sentences.",
        )

    # 5. placeholders
    for match in PLACEHOLDER_RE.finditer(prose):
        result.add(
            f'Placeholder text left in place: "{match.group(0)}"',
            severity="high",
            location=context,
            excerpt=prose[max(0, match.start() - 60) : match.end() + 60].replace("\n", " "),
            recommendation="Write the real content or move the item to an asset request.",
        )

    # 6. soft adjective stacking
    soft_hits = [w for w in word_list if w in SOFT_PHRASES]
    if len(soft_hits) >= 4 and len(soft_hits) / word_count > 0.015:
        result.add(
            f"Marketing adjectives standing in for description ({len(soft_hits)} uses: "
            f"{', '.join(sorted(set(soft_hits))[:5])})",
            severity="medium",
            location=context,
            excerpt="",
            recommendation=(
                "Describe what the player sees or does instead of how it feels to read "
                "in a store page."
            ),
        )

    # 7. repeated sentence openers (a strong uniformity signal)
    opener_counts: dict[str, int] = {}
    sentence_list = sentences(prose)
    for sentence in sentence_list:
        first = " ".join(words(sentence)[:2])
        if len(first.split()) == 2:
            opener_counts[first] = opener_counts.get(first, 0) + 1
    repeated = [(opener, n) for opener, n in opener_counts.items() if n >= 3]
    if repeated:
        opener, count = max(repeated, key=lambda pair: pair[1])
        result.add(
            f'{count} sentences start with the same words ("{opener}")',
            severity="low",
            location=context,
            excerpt=opener,
            recommendation="Vary sentence openings; this pattern reads as generated.",
        )

    result.metrics.update(
        {
            "words": len(word_list),
            "sentences": len(sentence_list),
            "code_chars": len(code),
            "banned_phrase_hits": sum(1 for p, _ in count_phrase_hits(prose, BANNED_PHRASES)),
            "placeholder_hits": len(PLACEHOLDER_RE.findall(prose)),
        }
    )
    result.summary = _summarise(result)
    return result


def review_many(texts: Iterable[tuple[str, str]], *, strict: bool = False) -> SkillResult:
    """Run over several labelled documents (e.g. one note per file) and merge findings."""
    merged = SkillResult(skill=SKILL)
    for label, text in texts:
        partial = review(text, context=label, strict=strict)
        merged.findings.extend(partial.findings)
        for key, value in partial.metrics.items():
            if isinstance(value, (int, float)):
                merged.metrics[f"{label}.{key}"] = value
    merged.summary = _summarise(merged)
    return merged


def _summarise(result: SkillResult) -> str:
    highs = [f for f in result.findings if f.severity == "high"]
    mediums = [f for f in result.findings if f.severity == "medium"]
    if highs:
        return f"{len(highs)} serious issue(s) and {len(mediums)} warning(s) found."
    if mediums:
        return f"{len(mediums)} warning(s) found; nothing blocking."
    return "No generic filler, hedging or placeholder text detected."


def is_slop(text: str, *, threshold: int = 12) -> bool:
    """Convenience predicate used by quick gates (e.g. polling for a good GDD summary)."""
    return review(text).severity_score >= threshold
