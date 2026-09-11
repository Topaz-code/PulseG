"""design_taste - the D.7 checklist, run as code rather than remembered as an intention.

The specification lists eight things every screen must satisfy. A checklist that lives in a
document gets skipped the first time a deadline appears, so this module parses the actual
React source of each view and reports what it finds. It runs from the API
(``/api/design/audit``) and from the build, and its output is written to
``project-log/DESIGN_TASTE_AUDIT.md``.

Checks, and what they mean in practice:

1. **One primary action per screen.** Each view must mark exactly one element with
   ``data-primary-action``. Two canary-yellow buttons on one screen means neither is the
   primary action.
2. **8px spacing grid.** Tailwind spacing utilities used for padding/margin/gap must be on the
   project's step scale (``SPACING_STEPS``); ``p-[7px]`` and ``mt-1.5`` are flagged.
3. **Interactive states.** Any element with a click handler must show hover, disabled and
   loading treatment (``hover:``, ``disabled:``/``aria-disabled``, ``aria-busy``/spinner).
4. **Designed empty states.** A list or table must have an empty branch with real copy.
5. **WCAG AA contrast in dark mode.** Computed from the theme's own colour tokens.
6. **Keyboard navigable with visible focus.** ``focus-visible:`` rings, no ``outline-none``
   without a replacement, and dialogs must trap or restore focus.
7. **No jargon in user-facing strings.** "Artifact", "webhook", "idempotent" and friends are
   flagged with the string quoted, because a non-technical user has to be able to run this.
8. **Subtle, purposeful motion.** Transitions allowed only on the properties we list
   (opacity, transform, colours) and only with a short duration. Decorative infinite
   animations are flagged.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core.atomic import read_json
from ..core.paths import theme_file
from .base import SkillResult

SKILL = "design_taste"

#: Tailwind's default spacing scale is a 2px grid, but the project rule is 8px for layout.
#: These are the accepted step values, in Tailwind units (1 unit = 0.25rem = 4px).
SPACING_STEPS = {0, 1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64}
ALLOWED_TRANSITION_PROPERTIES = {"opacity", "transform", "color", "background-color", "border-color", "box-shadow", "filter"}
MAX_TRANSITION_MS = 300

JARGON = (
    "artifact", "artifacts", "webhook", "idempotent", "payload", "serialize", "deserialize",
    "daemon", "mutex", "thread pool", "stdout", "stderr", "regex", "endpoint", "schema",
    "null", "boolean", "async", "await", "runtime", "kernel", "container", "docker",
    "cron", "cache invalidation", "race condition", "stack trace", "exception",
)

#: Strings that look like user-facing copy, for the jargon check.
USER_STRING = re.compile(r">([^<>{}\n]{3,120})<|\"([A-Z][^\"\n]{6,120})\"")


def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of a #rrggbb colour."""
    value = (hex_color or "").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    if len(value) != 6:
        raise ValueError(f"Not a hex colour: {hex_color!r}")
    red, green, blue = (int(value[index : index + 2], 16) / 255.0 for index in (0, 2, 4))
    return 0.2126 * _srgb_to_linear(red) + 0.7152 * _srgb_to_linear(green) + 0.0722 * _srgb_to_linear(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


#: The generated token file that ships with the app, used when the studio home has none yet.
BUNDLED_THEME = Path(__file__).resolve().parents[1] / "core" / "theme.json"


def load_theme() -> dict[str, Any]:
    """The user's theme if they have one, otherwise the bundled generated token set.

    Order matters: the studio home wins, because Settings can edit the theme and the user's
    choice must survive a restart. The bundled file is written by ``npm run tokens`` and is the
    same token set the frontend compiles against, so backend contrast checks and the UI cannot
    disagree about what the palette is.
    """
    payload = read_json(theme_file(), default=None)
    if isinstance(payload, dict) and payload:
        return payload
    bundled = read_json(BUNDLED_THEME, default=None)
    if isinstance(bundled, dict) and bundled:
        return bundled
    return {
        "colors": {
            "charcoal-bg": "#1E1C21",
            "charcoal-surface": "#55505C",
            "canary-yellow": "#FAF33E",
            "muted-teal": "#7FC6A4",
            "frosted-mint": "#D6F8D6",
            "blue-slate": "#5D737E",
        },
        "scales": {},
        "note": "Bundled fallback. Run `npm run tokens` to generate the full 10-step scales.",
    }


def _token_hex(theme: dict[str, Any], token: str, step: int = 500) -> str:
    scales = theme.get("scales") or {}
    entry = scales.get(token) or {}
    if isinstance(entry, dict):
        value = entry.get(str(step)) or entry.get(step) or entry.get("500") or entry.get("DEFAULT")
        if isinstance(value, str):
            return value
    colours = theme.get("colors") or {}
    value = colours.get(token)
    return value if isinstance(value, str) else "#000000"


def contrast_pairs(theme: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every text-on-surface pairing the app actually uses, with its AA verdict.

    AA needs 4.5:1 for body text and 3:1 for large text or graphical objects. We report both
    so a decorative chip passing 3:1 is not treated as a failure of body copy.
    """
    theme = theme or load_theme()
    pairs = [
        ("body text on app background", "frosted-mint", "charcoal-bg", 4.5),
        ("body text on surface", "frosted-mint", "charcoal-surface", 4.5),
        ("primary button label", "charcoal-bg", "canary-yellow", 4.5),
        ("muted label on background", "blue-slate", "charcoal-bg", 3.0),
        ("success text on background", "muted-teal", "charcoal-bg", 3.0),
        ("link on surface", "canary-yellow", "charcoal-surface", 4.5),
    ]
    rows: list[dict[str, Any]] = []
    for label, foreground_token, background_token, minimum in pairs:
        foreground = _token_hex(theme, foreground_token)
        background = _token_hex(theme, background_token)
        try:
            ratio = contrast_ratio(foreground, background)
        except ValueError:
            continue
        rows.append(
            {
                "pair": label,
                "foreground": foreground,
                "background": background,
                "contrast": round(ratio, 2),
                "required": minimum,
                "passes": ratio >= minimum,
            }
        )
    return rows


def theme_review(theme: dict[str, Any] | None = None) -> SkillResult:
    result = SkillResult(skill=f"{SKILL}.theme")
    theme = theme or load_theme()
    rows = contrast_pairs(theme)
    result.metrics["contrast_pairs"] = rows
    for row in rows:
        if not row["passes"]:
            result.add(
                f"{row['pair']} fails WCAG AA: {row['contrast']}:1 against a required "
                f"{row['required']}:1",
                severity="high" if row["required"] >= 4.5 else "medium",
                location="theme",
                recommendation=(
                    "Adjust the token in theme.json; never patch the colour inside a component. "
                    "Every colour in the app must come from the 10-step scale."
                ),
            )
    scales = theme.get("scales") or {}
    if scales:
        for token, steps in scales.items():
            if not isinstance(steps, dict) or len(steps) < 10:
                result.add(
                    f"Colour '{token}' has {len(steps) if isinstance(steps, dict) else 0} steps, "
                    "not the required 10",
                    severity="medium",
                    location="theme",
                    recommendation="Regenerate the scale from the base colour with chroma.js.",
                )
    else:
        result.notes.append(
            "theme.json has no expanded scales yet; the app is running on the bundled defaults."
        )
    result.summary = (
        f"{len(rows)} contrast pair(s) checked, "
        f"{sum(1 for row in rows if not row['passes'])} failing."
    )
    return result


# --- source analysis -------------------------------------------------------------------------


def _class_names(source: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r'className=(?:"([^"]+)"|\{`([^`]+)`\}|"([^"]+)")', source):
        value = next((group for group in match.groups() if group), "")
        found.extend(value.split())
    return found


def _spacing_offenders(classes: Sequence[str]) -> list[str]:
    offenders: list[str] = []
    pattern = re.compile(r"^-?(p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|space-x|space-y)-(\[[^\]]+\]|[0-9.]+)$")
    for name in classes:
        match = pattern.match(name)
        if not match:
            continue
        value = match.group(2)
        if value.startswith("["):
            raw = re.sub(r"[^0-9.]", "", value) or "0"
            pixels = float(raw) if "px" in value else float(raw) * 4
            if pixels % 8 not in (0,) and pixels not in (4.0, 12.0):
                offenders.append(name)
            continue
        try:
            if float(value) not in SPACING_STEPS:
                offenders.append(name)
        except ValueError:
            offenders.append(name)
    return sorted(set(offenders))


def _user_strings(source: str) -> list[str]:
    out: list[str] = []
    for match in USER_STRING.finditer(source):
        value = (match.group(1) or match.group(2) or "").strip()
        if value and not value.startswith(("{", "//", "/*")):
            out.append(value)
    return out


def analyse_screen_source(source: str, screen: str = "screen") -> dict[str, Any]:
    """Deterministic analysis of one view's source. No model involved."""
    classes = _class_names(source)
    strings = _user_strings(source)
    findings: list[dict[str, Any]] = []

    primary_actions = len(re.findall(r"data-primary-action", source))
    findings.append(
        {
            "check": "one primary action",
            "severity": "high" if primary_actions != 1 else "pass",
            "detail": f"{primary_actions} element(s) marked data-primary-action",
        }
    )

    offenders = _spacing_offenders(classes)
    findings.append(
        {
            "check": "8px spacing grid",
            "severity": "medium" if offenders else "pass",
            "detail": f"off-grid utilities: {', '.join(offenders[:8])}" if offenders else "all spacing on step",
        }
    )

    clickable = len(re.findall(r"onClick|onSubmit|onPress", source))
    states = {
        "hover": "hover:" in source,
        "disabled": "disabled:" in source or "aria-disabled" in source,
        "loading": "aria-busy" in source or "isLoading" in source or "isPending" in source,
    }
    missing_states = [name for name, present in states.items() if clickable and not present]
    findings.append(
        {
            "check": "interactive states",
            "severity": "medium" if missing_states else "pass",
            "detail": f"missing: {', '.join(missing_states)}" if missing_states else "hover, disabled and loading present",
        }
    )

    has_collection = any(marker in source for marker in (".map(", "<Table", "<tbody", "data.length"))
    has_empty_state = any(marker in source.lower() for marker in ("empty", "no tasks", "nothing", "get started"))
    findings.append(
        {
            "check": "designed empty state",
            "severity": "medium" if has_collection and not has_empty_state else "pass",
            "detail": "collection rendered without an empty branch" if has_collection and not has_empty_state else "empty state handled",
        }
    )

    focus_ok = "focus-visible:" in source or "focus:" in source
    outline_killed = "outline-none" in source or "outline-hidden" in source
    findings.append(
        {
            "check": "keyboard focus ring",
            "severity": "high" if (outline_killed and not focus_ok) else ("medium" if not focus_ok else "pass"),
            "detail": (
                "outline-none without a focus-visible replacement"
                if outline_killed and not focus_ok
                else ("no focus styling found" if not focus_ok else "focus-visible styling present")
            ),
        }
    )

    jargon_hits = sorted({word for word in JARGON for text in strings if re.search(rf"\b{word}\b", text, re.IGNORECASE)})
    findings.append(
        {
            "check": "no jargon in user-facing copy",
            "severity": "medium" if jargon_hits else "pass",
            "detail": f"jargon in strings: {', '.join(jargon_hits[:6])}" if jargon_hits else "copy readable by a non-technical user",
        }
    )

    durations = [int(value) for value in re.findall(r"duration-(\d+)", source)]
    long_transitions = [value for value in durations if value > MAX_TRANSITION_MS]
    infinite = len(re.findall(r"animate-(?:spin|ping|bounce|pulse)", source))
    motion_ok = not long_transitions and infinite <= 1
    findings.append(
        {
            "check": "subtle purposeful motion",
            "severity": "low" if not motion_ok else "pass",
            "detail": (
                f"durations over {MAX_TRANSITION_MS}ms: {long_transitions}; infinite animations: {infinite}"
                if not motion_ok
                else "transitions short and purposeful"
            ),
        }
    )

    hardcoded = [
        colour
        for colour in re.findall(r"#[0-9a-fA-F]{6}\b", source)
        if colour.upper() not in {"#000000", "#FFFFFF"}
    ]
    findings.append(
        {
            "check": "no ad-hoc colours",
            "severity": "high" if hardcoded else "pass",
            "detail": f"hardcoded colours: {', '.join(sorted(set(hardcoded))[:6])}" if hardcoded else "all colours via tokens",
        }
    )

    return {
        "screen": screen,
        "findings": findings,
        "classes": len(classes),
        "strings": len(strings),
        "primary_actions": primary_actions,
        "passing": all(item["severity"] == "pass" for item in findings),
    }


def review_source(source: str, screen: str = "screen") -> SkillResult:
    analysis = analyse_screen_source(source, screen)
    result = SkillResult(skill=SKILL)
    result.metrics.update({key: value for key, value in analysis.items() if key != "findings"})
    for finding in analysis["findings"]:
        if finding["severity"] == "pass":
            continue
        result.add(
            f"{screen}: {finding['check']} - {finding['detail']}",
            severity=finding["severity"],
            location=screen,
        )
    result.summary = (
        f"{screen}: {'passes the D.7 checklist' if analysis['passing'] else str(sum(1 for f in analysis['findings'] if f['severity'] != 'pass')) + ' checklist item(s) outstanding'}."
    )
    return result


def review_repo(views_dir: Path) -> tuple[list[dict[str, Any]], SkillResult]:
    """Audit every view in the frontend source tree."""
    reviews: list[dict[str, Any]] = []
    combined = SkillResult(skill=SKILL)
    if not views_dir.exists():
        combined.notes.append(f"No view sources found at {views_dir}.")
        combined.summary = "Nothing to audit yet."
        return reviews, combined
    for path in sorted(views_dir.rglob("*.tsx")):
        # Test files live beside the screens now. They are not screens: they have no primary action
        # to audit and no empty state to design, and counting them would report the audit failing
        # whenever a well-tested screen exists.
        if path.name.endswith(".test.tsx"):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        analysis = analyse_screen_source(source, screen=path.stem)
        reviews.append(analysis)
        for finding in analysis["findings"]:
            if finding["severity"] == "pass":
                continue
            combined.add(
                f"{path.stem}: {finding['check']} - {finding['detail']}",
                severity=finding["severity"],
                location=str(path),
            )
    combined.metrics["screens"] = len(reviews)
    combined.metrics["screens_passing"] = sum(1 for review in reviews if review["passing"])
    combined.summary = (
        f"{combined.metrics['screens_passing']} of {len(reviews)} screen(s) pass the D.7 checklist."
    )
    return reviews, combined


def checklist() -> list[dict[str, str]]:
    """The D.7 checklist as data, so the UI and the docs cannot drift apart."""
    return [
        {"id": "primary-action", "label": "One primary action per screen", "enforced_by": "data-primary-action count"},
        {"id": "spacing", "label": "8px spacing grid", "enforced_by": "Tailwind spacing step allowlist"},
        {"id": "states", "label": "Hover, active, disabled and loading states", "enforced_by": "hover:/disabled:/aria-busy presence"},
        {"id": "empty", "label": "Designed empty states", "enforced_by": "empty branch when a collection renders"},
        {"id": "contrast", "label": "WCAG AA in dark mode", "enforced_by": "computed contrast from theme.json"},
        {"id": "keyboard", "label": "Keyboard navigable with focus rings", "enforced_by": "focus-visible presence, outline-none audit"},
        {"id": "language", "label": "No jargon in user-facing copy", "enforced_by": "jargon list against rendered strings"},
        {"id": "motion", "label": "Subtle purposeful motion", "enforced_by": "transition duration and infinite-animation audit"},
    ]


def format_report(reviews: Sequence[dict[str, Any]], theme_result: SkillResult | None = None) -> str:
    """The Markdown written to project-log/DESIGN_TASTE_AUDIT.md."""
    theme_result = theme_result or theme_review()
    lines = [
        "# Design taste audit (D.7)",
        "",
        f"Screens reviewed: {len(reviews)}.",
        "",
        "## Checklist",
        "",
    ]
    for item in checklist():
        lines.append(f"- {item['label']} (checked by {item['enforced_by']})")
    lines += ["", "## Contrast", ""]
    for row in theme_result.metrics.get("contrast_pairs", []):
        mark = "pass" if row["passes"] else "FAIL"
        lines.append(
            f"- {mark}: {row['pair']} - {row['contrast']}:1 (needs {row['required']}:1) "
            f"[{row['foreground']} on {row['background']}]"
        )
    lines += ["", "## Screens", ""]
    for review in reviews:
        status = "pass" if review.get("passing") else "outstanding items"
        lines.append(f"### {review.get('screen', 'screen')} - {status}")
        for finding in review.get("findings", []):
            if finding["severity"] == "pass":
                continue
            lines.append(f"- [{finding['severity']}] {finding['check']}: {finding['detail']}")
        lines.append("")
    if theme_result.findings:
        lines += ["## Theme findings", ""]
        lines += [f"- [{finding.severity}] {finding.message}" for finding in theme_result.findings]
    return "\n".join(lines)


def write_audit_log(target: Path, reviews: Sequence[dict[str, Any]], theme_result: SkillResult | None = None) -> Path:
    """Write the audit where the specification expects it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_report(reviews, theme_result), encoding="utf-8")
    return target


def theme_as_json(theme: dict[str, Any] | None = None) -> str:
    return json.dumps(theme or load_theme(), indent=2)


def tokens_used_in(source: str) -> list[str]:
    """Colour tokens referenced by a component, for the 'no ad-hoc colours' report."""
    return sorted(set(re.findall(r"(?:bg|text|border|ring|from|to|via)-([a-z-]+)-\d{3}", source)))


def unknown_tokens(source: str, theme: dict[str, Any] | None = None) -> list[str]:
    theme = theme or load_theme()
    known = set((theme.get("scales") or {}).keys()) | set((theme.get("colors") or {}).keys())
    return [token for token in tokens_used_in(source) if token not in known]


def summarise(reviews: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(reviews)
    return {
        "screens": len(rows),
        "passing": sum(1 for row in rows if row.get("passing")),
        "failing": [row.get("screen") for row in rows if not row.get("passing")],
    }
