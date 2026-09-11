"""Shared plumbing for skills.

Skills are discrete, named, reusable modules (spec B.3). Each one is:

* **deterministic first** - pure Python over the text, so the same input always produces
  the same flags and the Auditor's evidence is reproducible;
* **independent** - no skill imports another skill, so each can be improved on its own;
* **explainable** - every finding carries a location, an excerpt and a recommendation,
  because the Auditor's fix note is handed to an agent as its next instruction.

An optional model pass can be layered on top by an agent, but the numbers the verdict
quotes always come from here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..core.models import SkillFinding

# --- text utilities -------------------------------------------------------------------

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z\"'(])")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
CODE_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]+`")
URL_RE = re.compile(r"https?://\S+")
HTML_TAG = re.compile(r"<[a-zA-Z/][^>]*>")

#: Common English function words. Used by the stylometry skill (Burrows' Delta works on
#: the most frequent words, which in English are overwhelmingly function words).
FUNCTION_WORDS: tuple[str, ...] = (
    "the", "of", "and", "to", "a", "in", "that", "it", "is", "was", "i", "for", "as",
    "with", "his", "he", "be", "on", "at", "by", "had", "not", "are", "but", "from",
    "or", "have", "an", "they", "which", "one", "you", "were", "her", "all", "she",
    "there", "would", "their", "we", "him", "been", "has", "when", "who", "will", "more",
    "no", "if", "out", "so", "said", "what", "up", "its", "about", "into", "than", "them",
    "can", "only", "other", "new", "some", "could", "time", "these", "two", "may", "then",
    "do", "first", "any", "my", "now", "such", "like", "our", "over", "man", "me", "even",
    "most", "made", "after", "also", "did", "many", "before", "must", "through", "back",
    "years", "where", "much", "your", "way", "well", "down", "should", "because", "each",
    "just", "those", "people", "how", "too", "little", "state", "good", "very", "make",
    "world", "still", "own", "see", "men", "work", "long", "get", "here", "between", "both",
    "life", "being", "under", "never", "day", "same", "another", "know", "while", "last",
    "might", "us", "great", "old", "year", "off", "come", "since", "against", "go", "came",
    "right", "used", "take", "three",
)

#: Words that mark a passage as machine-default rather than considered.
HEDGE_WORDS = (
    "arguably", "essentially", "basically", "generally", "typically", "various",
    "several", "numerous", "certain", "somewhat", "fairly", "quite", "rather",
    "relatively", "significantly", "notably", "importantly", "ultimately",
)


def words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text or "")]


def sentences(text: str) -> list[str]:
    stripped = strip_code(text)
    parts = [s.strip() for s in SENTENCE_SPLIT.split(stripped) if s and s.strip()]
    return parts


def strip_code(text: str) -> str:
    """Remove fenced and inline code so prose checks do not flag real code."""
    text = CODE_FENCE.sub(" ", text or "")
    text = INLINE_CODE.sub(" ", text)
    return text


def strip_markup(text: str) -> str:
    text = strip_code(text)
    text = URL_RE.sub(" ", text)
    text = HTML_TAG.sub(" ", text)
    text = re.sub(r"^[#>\-\*\s]+", " ", text, flags=re.MULTILINE)
    return text


def chunks(text: str, count: int = 6) -> list[str]:
    """Split prose into near-equal chunks for intra-document variance analysis."""
    parts = sentences(text)
    if len(parts) < count * 2:
        words_all = words(strip_markup(text))
        if len(words_all) < 80:
            return [strip_markup(text)] if text.strip() else []
        size = max(40, len(words_all) // count)
        return [" ".join(words_all[i : i + size]) for i in range(0, len(words_all), size)]
    size = max(1, len(parts) // count)
    return [" ".join(parts[i : i + size]) for i in range(0, len(parts), size)]


def estimate_tokens(text: str) -> int:
    """Cheap, dependency-free estimate (~4 characters per token for English)."""
    return max(1, len(text or "") // 4)


def extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """Return ``(path, contents)`` for every ``file:`` block in an agent response."""
    blocks: list[tuple[str, str]] = []
    pattern = re.compile(r"```file:\s*([^\n]+)\n(.*?)```", re.DOTALL)
    for match in pattern.finditer(text or ""):
        blocks.append((match.group(1).strip(), match.group(2)))
    if blocks:
        return blocks
    for match in CODE_FENCE.finditer(text or ""):
        header = match.group(0).split("\n", 1)[0].strip("` ")
        path = ""
        if header and re.search(r"[./\\]", header):
            path = header
        blocks.append((path, match.group(2) if path else match.group(1)))
    return blocks


def severity_weight(severity: str) -> int:
    return {"info": 0, "low": 3, "medium": 8, "high": 15}.get(severity, 5)


@dataclass
class SkillResult:
    """Uniform return type for every skill."""

    skill: str
    findings: list[SkillFinding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity in ("medium", "high") for f in self.findings)

    @property
    def severity_score(self) -> int:
        """0 (clean) to 100 (unusable). The Auditor uses this as one input to its score."""
        total = sum(severity_weight(f.severity) for f in self.findings)
        return min(100, total)

    @property
    def has_high_severity(self) -> bool:
        return any(f.severity == "high" for f in self.findings)

    def flags(self) -> list[str]:
        return [
            f"{f.message}" + (f" (at {f.location})" if f.location else "")
            for f in self.findings
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "passed": self.passed,
            "severity_score": self.severity_score,
            "summary": self.summary,
            "metrics": self.metrics,
            "findings": [f.model_dump(mode="json") for f in self.findings],
            "notes": self.notes,
        }

    def add(
        self,
        message: str,
        *,
        severity: str = "medium",
        location: str = "",
        excerpt: str = "",
        recommendation: str = "",
    ) -> None:
        self.findings.append(
            SkillFinding(
                skill=self.skill,
                severity=severity,  # type: ignore[arg-type]
                message=message,
                location=location,
                excerpt=excerpt[:280],
                recommendation=recommendation,
            )
        )


def excerpt_around(text: str, needle: str, span: int = 120) -> str:
    index = (text or "").lower().find(needle.lower())
    if index == -1:
        return ""
    start = max(0, index - span // 2)
    return text[start : start + span].replace("\n", " ").strip()


def count_phrase_hits(text: str, phrases: Iterable[str]) -> list[tuple[str, str]]:
    """Return ``(phrase, excerpt)`` for each phrase present, case-insensitively."""
    hits: list[tuple[str, str]] = []
    lowered = (text or "").lower()
    for phrase in phrases:
        if phrase.lower() in lowered:
            hits.append((phrase, excerpt_around(text, phrase)))
    return hits


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return variance**0.5


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
