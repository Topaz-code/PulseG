"""harvard_shape - stylometric and structural review, built on a real published method.

**Source.** The method here follows "Stylometric comparisons of human versus AI-generated
creative writing" (*Humanities and Social Sciences Communications*, 2025,
s41599-025-05986-3). What that paper actually does, and what we therefore do:

* Burrows' Delta over **most frequent words** - the words are turned into relative
  frequencies, z-score standardised across the texts being compared, and the distance is the
  mean absolute difference of the z-scores.
* A **Cosine Delta** variant (Jannidis et al., 2015), which the literature reports as less
  sensitive to text length - useful here because chunks of a design document are short.
* **Average-linkage hierarchical clustering** of the texts in that distance space, plus the
  MDS-style intuition that human writing spreads out while model output collapses into a
  tight cluster.
* The paper's corpus was narrative continuations (250 human, ~130 machine) written to the
  same prompt. Its central finding is that the human texts are *heterogeneous* and the model
  texts are *uniform* - so uniformity, not any particular phrase, is the signal we act on.

**What we do not pretend.** The paper reports distances between texts; it does not publish a
magic cutoff that says "this paragraph is AI". Any single absolute threshold would be
invented, so this module is honest in two ways:

1. The primary evidence is *relative*: :func:`compare_to_corpus` compares a submission to the
   project's own genuinely human-authored text (the human's planning messages, supplied
   references) against other agent-produced text. That is the same shape of comparison the
   paper makes, applied to one project instead of a research corpus.
2. The absolute thresholds in :data:`THRESHOLDS_BY_KIND` are labelled as heuristics. They
   differ by document kind because they must: a reference document is *supposed* to be
   stylistically flat, while a story is not. Every threshold used is echoed into
   ``metrics`` so a finding can always be traced to a number.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .base import SkillResult, mean, stdev
from .humanizer import FUNCTION_WORDS

SKILL = "harvard_shape"
PAPER = "s41599-025-05986-3"

#: The single module-level named-entity pattern. Compiling this once per call was a measurable
#: cost during the audit loop, and duplicated patterns drift apart.
_NAMED_ENTITY_RE = re.compile(
    r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z]{2,}\b|\b\d+(?:\.\d+)?\b"
)

#: Heuristic thresholds, by document kind. Narrative is the calibrated case (it is what the
#: paper studied); documentation is looser because reference text is meant to be consistent.
THRESHOLDS_BY_KIND: dict[str, dict[str, float]] = {
    "narrative": {
        "delta_cv_floor": 0.28,
        "merge_height_floor": 0.30,
        "ttr_floor": 0.34,
        "named_entity_floor": 1.0,
        "min_chunks": 4.0,
        "min_words": 250.0,
    },
    "dialogue": {
        "delta_cv_floor": 0.30,
        "merge_height_floor": 0.32,
        "ttr_floor": 0.30,
        "named_entity_floor": 2.0,
        "min_chunks": 3.0,
        "min_words": 200.0,
    },
    "documentation": {
        "delta_cv_floor": 0.14,
        "merge_height_floor": 0.18,
        "ttr_floor": 0.22,
        "named_entity_floor": 0.0,
        "min_chunks": 4.0,
        "min_words": 300.0,
    },
    "code": {
        "delta_cv_floor": 0.10,
        "merge_height_floor": 0.12,
        "ttr_floor": 0.15,
        "named_entity_floor": 0.0,
        "min_chunks": 3.0,
        "min_words": 200.0,
    },
}
DEFAULT_KIND = "narrative"

#: Supplementary markers of over-balanced prose. These are *not* from the paper - the paper's
#: signal is distributional - so they are reported as notes and low-severity findings only.
BALANCED_PROSE_MARKERS = (
    "on the one hand", "on the other hand", "it is worth noting", "it is important to note",
    "furthermore", "moreover", "in conclusion", "in summary", "additionally,", "overall,",
    "when it comes to", "plays a crucial role", "in today's world", "delve into",
)


@dataclass
class StylometryProfile:
    """One text (or chunk) in function-word space."""

    label: str
    words: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    relative: dict[str, float] = field(default_factory=dict)
    z: dict[str, float] = field(default_factory=dict)

    def vector(self) -> list[float]:
        return [self.z.get(word, 0.0) for word in FUNCTION_WORDS]

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "words": self.words, "top_words": sorted(self.relative.items(), key=lambda item: -item[1])[:10]}


def profile(text: str, label: str = "text", mfw_count: int = 100) -> StylometryProfile:
    """Build the relative-frequency profile over the most frequent (function) words.

    ``mfw_count`` caps how many of the list are kept - the paper varies this and reports that
    the signal is stable across list sizes, so we record the size used for reproducibility.
    """
    tokens = re.findall(r"[a-zA-Z']+", (text or "").lower())
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    total = len(tokens) or 1
    words = FUNCTION_WORDS[:mfw_count] if mfw_count else FUNCTION_WORDS
    relative = {word: counts.get(word, 0) / total for word in words}
    return StylometryProfile(label=label, words=len(tokens), counts=counts, relative=relative)


def z_normalise(profiles: Sequence[StylometryProfile]) -> None:
    """Z-score each word's relative frequency across the supplied profiles, in place."""
    if not profiles:
        return
    for word in FUNCTION_WORDS:
        values = [item.relative.get(word, 0.0) for item in profiles]
        average = mean(values)
        spread = stdev(values)
        for item in profiles:
            value = item.relative.get(word, 0.0)
            item.z[word] = 0.0 if spread == 0 else (value - average) / spread


def burrows_delta(first: StylometryProfile, second: StylometryProfile) -> float:
    """Mean absolute difference of z-scored relative frequencies (Burrows' Delta)."""
    words = [word for word in FUNCTION_WORDS if word in first.z or word in second.z]
    if not words:
        return 0.0
    return sum(abs(first.z.get(word, 0.0) - second.z.get(word, 0.0)) for word in words) / len(words)


def cosine_delta(first: StylometryProfile, second: StylometryProfile) -> float:
    """Cosine Delta: 1 - cosine similarity of the z-scored vectors (Jannidis et al., 2015)."""
    left, right = first.vector(), second.vector()
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))
    if not norm_left or not norm_right:
        return 0.0
    return 1.0 - dot / (norm_left * norm_right)


def delta_matrix(profiles: Sequence[StylometryProfile]) -> dict[tuple[str, str], float]:
    """Pairwise Burrows' Delta keyed by ``(label_a, label_b)``, order-insensitive."""
    matrix: dict[tuple[str, str], float] = {}
    for index, first in enumerate(profiles):
        for second in profiles[index + 1 :]:
            matrix[(first.label, second.label)] = burrows_delta(first, second)
    return matrix


def average_linkage_height(profiles: Sequence[StylometryProfile]) -> float:
    """Normalised height of the final merge under average-linkage (UPGMA) clustering.

    High means the texts are genuinely different from each other; low means they all sit on
    top of each other in style space. The raw height is divided by the largest distance seen,
    so the number is comparable between runs of different sizes.
    """
    if len(profiles) < 3:
        return 1.0
    distances = delta_matrix(profiles)
    if not distances:
        return 1.0
    ceiling = max(distances.values()) or 1.0
    clusters: list[list[int]] = [[index] for index in range(len(profiles))]
    heights: list[float] = []

    def cluster_distance(a: list[int], b: list[int]) -> float:
        pairs = [
            distances.get((profiles[first].label, profiles[second].label))
            or distances.get((profiles[second].label, profiles[first].label))
            or 0.0
            for first in a
            for second in b
        ]
        return mean(pairs)

    while len(clusters) > 1:
        best: tuple[float, int, int] | None = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                distance = cluster_distance(clusters[i], clusters[j])
                if best is None or distance < best[0]:
                    best = (distance, i, j)
        assert best is not None
        distance, i, j = best
        heights.append(distance / ceiling)
        merged = clusters[i] + clusters[j]
        clusters = [cluster for index, cluster in enumerate(clusters) if index not in (i, j)]
        clusters.append(merged)
    return heights[-1] if heights else 1.0


def _chunk(text: str, minimum: int) -> list[str]:
    """Split into roughly equal chunks on paragraph boundaries."""
    paragraphs = [block.strip() for block in re.split(r"\n\s*\n", text or "") if block.strip()]
    if len(paragraphs) < 2:
        words = (text or "").split()
        size = max(minimum, len(words) // 4 or 1)
        return [" ".join(words[index : index + size]) for index in range(0, len(words), size)][:6]
    target_words = max(minimum, sum(len(block.split()) for block in paragraphs) // 4)
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for block in paragraphs:
        words = len(block.split())
        if current_words + words > target_words and current:
            chunks.append("\n\n".join(current))
            current, current_words = [], 0
        current.append(block)
        current_words += words
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _structural_metrics(text: str) -> dict[str, Any]:
    """Shape of the document: sections, their sizes, and whether they are all the same size."""
    lines = (text or "").splitlines()
    sections: list[tuple[str, int]] = []
    current_title = "(preamble)"
    current_words = 0
    heading_depth: list[int] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            depth = len(stripped) - len(stripped.lstrip("#"))
            heading_depth.append(depth)
            if sections or current_words:
                sections.append((current_title, current_words))
            current_title = stripped.lstrip("# ").strip() or "(untitled)"
            current_words = 0
            continue
        current_words += len(stripped.split())
    sections.append((current_title, current_words))
    sizes = [size for _, size in sections if size > 0]
    paragraphs = [block for block in re.split(r"\n\s*\n", text or "") if block.strip()]
    paragraph_sizes = [len(block.split()) for block in paragraphs]
    return {
        "sections": len(sections),
        "section_titles": [title for title, _ in sections][:30],
        "section_sizes": sizes,
        "section_size_cv": round(stdev(sizes) / mean(sizes), 3) if sizes and mean(sizes) else 0.0,
        "heading_depths": heading_depth,
        "distinct_heading_depths": len(set(heading_depth)),
        "paragraphs": len(paragraphs),
        "paragraph_size_cv": round(stdev(paragraph_sizes) / mean(paragraph_sizes), 3)
        if paragraph_sizes and mean(paragraph_sizes)
        else 0.0,
    }


def _supplementary_metrics(text: str) -> dict[str, float]:
    tokens = re.findall(r"[a-zA-Z']+", (text or "").lower())
    unique = set(tokens)
    sentences = [part for part in re.split(r"[.!?]+", text or "") if part.strip()]
    lengths = [len(part.split()) for part in sentences]
    return {
        "tokens": float(len(tokens)),
        "type_token_ratio": round(len(unique) / len(tokens), 4) if tokens else 0.0,
        "hapax_ratio": round(sum(1 for word in unique if tokens.count(word) == 1) / len(tokens), 4) if tokens else 0.0,
        "mean_sentence_length": round(mean(lengths), 2),
        "sentence_length_cv": round(stdev(lengths) / mean(lengths), 3) if lengths and mean(lengths) else 0.0,
    }


def _check_structure(result: SkillResult, text: str, context: str, thresholds: dict[str, float]) -> dict[str, Any]:
    metrics = _structural_metrics(text)
    result.metrics["structure"] = metrics
    sizes = metrics["section_sizes"]
    # A flat-structure finding requires BOTH signals: identical section sizes alone can be a
    # legitimate outline, and low stylistic variance alone can be a short document. Together
    # they mean the writer produced a template and filled it in.
    delta_cv = result.metrics.get("delta_cv")
    if (
        sizes
        and len(sizes) >= 4
        and metrics["section_size_cv"] < 0.12
        and delta_cv is not None
        and float(delta_cv) < thresholds["delta_cv_floor"]
    ):
        result.add(
            f"Flat structure: {len(sizes)} sections of nearly equal size "
            f"(spread {metrics['section_size_cv']:.2f}) and minimal stylistic variation between "
            f"them (delta cv {float(delta_cv):.2f})",
            severity="medium",
            location=context,
            recommendation=(
                "Spend the words where the design is actually uncertain. If every section is the "
                "same length, none of them was prioritised."
            ),
        )
    if metrics["sections"] == 1 and metrics["paragraphs"] <= 2 and len((text or "").split()) > 600:
        result.add(
            f"{len(text.split())} words with no sectioning at all",
            severity="low",
            location=context,
            recommendation="Split it into named sections so agents can cite a part of it.",
        )
    return metrics


def review(
    text: str,
    *,
    context: str = "prose",
    document_kind: str = "narrative",
    reference_texts: Sequence[str] = (),
    machine_texts: Sequence[str] = (),
    mfw_count: int = 100,
) -> SkillResult:
    """Full stylometric + structural review of one document."""
    kind = document_kind if document_kind in THRESHOLDS_BY_KIND else DEFAULT_KIND
    thresholds = THRESHOLDS_BY_KIND[kind]
    result = SkillResult(skill=SKILL)
    result.metrics.update(
        {
            "paper": PAPER,
            "document_kind": kind,
            "context": context,
            "mfw_count": mfw_count,
            "thresholds": thresholds,
            "thresholds_note": (
                "Absolute thresholds are heuristics tuned for this studio's documents; the "
                "paper's method is comparative and publishes no cutoff. Treat "
                "compare_to_corpus() as the primary evidence and these numbers as triage."
            ),
            "method": "Burrows' Delta over most frequent (function) words + Cosine Delta + average-linkage clustering",
        }
    )
    body = text or ""
    if not body.strip():
        result.summary = "Nothing to measure: the document is empty."
        return result

    supplementary = _supplementary_metrics(body)
    result.metrics["supplementary"] = supplementary
    entities = _NAMED_ENTITY_RE.findall(body)
    result.metrics["named_entities"] = len(entities)
    result.metrics["named_entity_sample"] = entities[:12]

    # --- distributional check (the paper's part) --------------------------------------
    chunks = _chunk(body, minimum=int(thresholds["min_words"] / 4))
    profiles = [profile(chunk, label=f"{context}#{index + 1}", mfw_count=mfw_count) for index, chunk in enumerate(chunks)]
    sufficient = (
        len(profiles) >= int(thresholds["min_chunks"])
        and supplementary["tokens"] >= thresholds["min_words"]
    )
    result.metrics["chunks"] = len(profiles)
    result.metrics["distributional_check_ran"] = sufficient

    if not sufficient:
        result.notes.append(
            f"Too short for the distributional comparison ({supplementary['tokens']:.0f} words, "
            f"{len(profiles)} chunks; needs {thresholds['min_words']:.0f} words and "
            f"{thresholds['min_chunks']:.0f} chunks). Rhythm and structure checks still ran."
        )
    else:
        z_normalise(profiles)
        pairs = [
            burrows_delta(profiles[index], profiles[other])
            for index in range(len(profiles))
            for other in range(index + 1, len(profiles))
        ]
        cosines = [
            cosine_delta(profiles[index], profiles[other])
            for index in range(len(profiles))
            for other in range(index + 1, len(profiles))
        ]
        average = mean(pairs)
        spread = stdev(pairs)
        cv = spread / average if average else 0.0
        height = average_linkage_height(profiles)
        result.metrics.update(
            {
                "delta_mean": round(average, 4),
                "delta_sd": round(spread, 4),
                "delta_cv": round(cv, 4),
                "delta_min": round(min(pairs), 4),
                "delta_max": round(max(pairs), 4),
                "cosine_delta_mean": round(mean(cosines), 4),
                "average_linkage_height": round(height, 4),
            }
        )
        if cv < thresholds["delta_cv_floor"] and height < thresholds["merge_height_floor"]:
            severity = "high" if kind == "narrative" else "low"
            result.add(
                f"Stylometrically uniform: the chunks cluster tightly (delta cv {cv:.2f}, merge "
                f"height {height:.2f}). The paper's signature of machine text is exactly this "
                "evenness across a whole document",
                severity=severity,
                location=context,
                recommendation=(
                    "Vary the register deliberately: a tense section in shorter sentences, a "
                    "reflective one with longer ones. Uniformity across a long document is the "
                    "signal, not any single sentence."
                ),
            )
        if average > 0 and result.metrics["delta_max"] - result.metrics["delta_min"] < 0.08:
            result.notes.append(
                "Every pair of chunks is almost equally distant from every other: the document "
                "has no internal contrast at all."
            )

    # --- supplementary heuristics (not from the paper) ---------------------------------
    if supplementary["tokens"] >= thresholds["min_words"]:
        if supplementary["type_token_ratio"] < thresholds["ttr_floor"]:
            result.add(
                f"Lexical diversity is low for {supplementary['tokens']:.0f} words "
                f"(type-token ratio {supplementary['type_token_ratio']:.2f}, floor "
                f"{thresholds['ttr_floor']:.2f})",
                severity="low",
                location=context,
                recommendation="Reach for the specific noun instead of the safe generic one.",
            )
        if (
            kind in ("narrative", "dialogue")
            and supplementary["tokens"] >= 400
            and result.metrics["named_entities"] < thresholds["named_entity_floor"]
        ):
            result.add(
                "No named people, places or numbers anywhere in the text",
                severity="medium",
                location=context,
                recommendation=(
                    "Specificity is where machine text is thinnest. Name the town, the ship, the "
                    "debt, the date."
                ),
            )
        balanced = [marker for marker in BALANCED_PROSE_MARKERS if marker in body.lower()]
        if len(balanced) >= 3:
            result.metrics["balanced_prose_markers"] = balanced
            result.add(
                f"Essay-style hedging borrowed from expository writing: {', '.join(balanced[:4])}",
                severity="low",
                location=context,
                recommendation="Cut them. They add length without adding information.",
            )

    _check_structure(result, body, context, thresholds)

    if result.passed:
        result.summary = (
            f"Reads as human-shaped: {'measured' if sufficient else 'assessed'} across "
            f"{len(profiles)} chunk(s), {supplementary['tokens']:.0f} words."
        )
    else:
        result.summary = f"{len(result.findings)} stylometric or structural concern(s) in {context}."
    return result


def compare_to_corpus(
    text: str,
    *,
    human_texts: Sequence[str] = (),
    machine_texts: Sequence[str] = (),
    label: str = "submission",
    mfw_count: int = 100,
    margin: float = 0.02,
) -> dict[str, Any]:
    """Compare a submission against the project's own human and machine corpora.

    This is the paper's comparison, scaled down to one project: the human texts are the
    things a person actually wrote (their planning messages, their reference notes), the
    machine texts are other agent outputs. The verdict is stated with its evidence, and
    refuses to conclude when the corpus is too small - which is the honest answer.
    """
    human = [item for item in human_texts if len(item.split()) >= 40]
    machine = [item for item in machine_texts if len(item.split()) >= 40]
    report: dict[str, Any] = {
        "label": label,
        "human_samples": len(human),
        "machine_samples": len(machine),
        "method": "Burrows' Delta and Cosine Delta against group centroids",
        "paper": PAPER,
    }
    if len(human) < 3 or len(machine) < 3:
        report.update(
            {
                "verdict": "inconclusive",
                "reason": (
                    "Need at least three human-authored and three machine-authored samples of 40+ "
                    "words each before a comparison means anything. The project accumulates these "
                    "naturally: every message you type in the Planning rail is a human sample."
                ),
            }
        )
        return report

    submission = profile(text, label=label, mfw_count=mfw_count)
    human_profiles = [profile(item, label=f"human{i}", mfw_count=mfw_count) for i, item in enumerate(human)]
    machine_profiles = [profile(item, label=f"machine{i}", mfw_count=mfw_count) for i, item in enumerate(machine)]
    z_normalise([submission, *human_profiles, *machine_profiles])

    def centroid_delta(group: Sequence[StylometryProfile], use_cosine: bool = False) -> float:
        if use_cosine:
            return mean([cosine_delta(submission, item) for item in group])
        return mean([burrows_delta(submission, item) for item in group])

    human_delta = centroid_delta(human_profiles)
    machine_delta = centroid_delta(machine_profiles)
    human_cosine = centroid_delta(human_profiles, use_cosine=True)
    machine_cosine = centroid_delta(machine_profiles, use_cosine=True)
    human_spread = mean([burrows_delta(a, b) for i, a in enumerate(human_profiles) for b in human_profiles[i + 1 :]])
    machine_spread = mean([burrows_delta(a, b) for i, a in enumerate(machine_profiles) for b in machine_profiles[i + 1 :]])

    report.update(
        {
            "delta_to_human": round(human_delta, 4),
            "delta_to_machine": round(machine_delta, 4),
            "cosine_delta_to_human": round(human_cosine, 4),
            "cosine_delta_to_machine": round(machine_cosine, 4),
            "human_internal_spread": round(human_spread, 4),
            "machine_internal_spread": round(machine_spread, 4),
        }
    )
    if abs(human_delta - machine_delta) <= margin:
        report["verdict"] = "inconclusive"
        report["reason"] = (
            f"The two centroids are within the {margin} margin of each other "
            f"({human_delta:.4f} vs {machine_delta:.4f}); this text is not clearly either."
        )
    elif human_delta < machine_delta:
        report["verdict"] = "closer_to_human"
        report["reason"] = (
            f"Delta to the human centroid {human_delta:.4f} is below the machine centroid "
            f"{machine_delta:.4f}. The human samples also spread wider internally "
            f"({human_spread:.4f} vs {machine_spread:.4f}), which matches the paper's finding."
        )
    else:
        report["verdict"] = "closer_to_machine"
        report["reason"] = (
            f"Delta to the machine centroid {machine_delta:.4f} is below the human centroid "
            f"{human_delta:.4f}."
        )
    return report


def build_human_baseline(project_path: Path, limit: int = 40) -> list[str]:
    """Gather text a person actually wrote, for the corpus comparison.

    Sources, in order of how human they are: the human's own planning messages, files they
    dropped into ``assets/references/`` as notes, then anything in ``story/`` that is not
    marked as agent-written. We never guess authorship from style here - guessing authors
    with a detector and then using the guess as ground truth is circular.
    """
    texts: list[str] = []
    try:
        from ..orchestration import memory as memory_module

        texts.extend(memory_module.human_text_corpus(project_path, limit=limit))
    except Exception:  # pragma: no cover - memory module is optional here
        pass
    references = project_path / "assets" / "references"
    if references.exists():
        for path in sorted(references.glob("*.md"))[:10]:
            try:
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return [text for text in texts if len(text.split()) >= 40]


def machine_baseline(project_path: Path, limit: int = 12) -> list[str]:
    """Agent-authored samples: progress notes and knowledge digests are exactly that."""
    texts: list[str] = []
    progress = project_path / "memory" / "progress.md"
    if progress.exists():
        blocks = progress.read_text(encoding="utf-8", errors="replace").split("\n\n")
        texts.extend(blocks[-limit:])
    knowledge = project_path / "knowledge"
    if knowledge.exists():
        for path in sorted(knowledge.rglob("*.md"))[:limit]:
            try:
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return [text for text in texts if len(text.split()) >= 60]


def format_report(result: SkillResult) -> str:
    """Markdown rendering for the audit report and the task drawer."""
    lines = [
        f"### {SKILL} review",
        "",
        result.summary,
        "",
        f"- Paper: {PAPER} (Burrows' Delta over most frequent words, Cosine Delta, average-linkage clustering)",
        f"- Document kind: {result.metrics.get('document_kind')}",
        f"- Delta cv: {result.metrics.get('delta_cv', 'not measured')}"
        f"  merge height: {result.metrics.get('average_linkage_height', 'not measured')}",
    ]
    for finding in result.findings:
        lines.append(f"- [{finding.severity}] {finding.message}")
        if finding.recommendation:
            lines.append(f"  - Fix: {finding.recommendation}")
    for note in result.notes:
        lines.append(f"- note: {note}")
    return "\n".join(lines)


def distances_between(texts: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
    """Pairwise distances for a labelled set - used by tests and by the audit report."""
    profiles = [profile(text, label=label) for label, text in texts]
    if len(profiles) < 2:
        return []
    z_normalise(profiles)
    rows: list[dict[str, Any]] = []
    for index, first in enumerate(profiles):
        for second in profiles[index + 1 :]:
            rows.append(
                {
                    "a": first.label,
                    "b": second.label,
                    "burrows_delta": round(burrows_delta(first, second), 4),
                    "cosine_delta": round(cosine_delta(first, second), 4),
                }
            )
    return rows
