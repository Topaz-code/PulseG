"""Regenerate project-log/PROVIDER_VERIFICATION.md from the provider table.

Run this whenever ``backend/providers/specs.py`` changes:

    python scripts/build_provider_report.py

Deriving the document from code is the only way it stays true. If the two disagree, one of
them is a lie, and it will be the document.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.providers.specs import (  # noqa: E402
    FREE_FOREVER,
    ONE_TIME_CREDIT,
    SUBSTITUTIONS,
    VERIFIED_ON,
    verification_report,
)

TARGET = REPO_ROOT / "project-log" / "PROVIDER_VERIFICATION.md"


def build() -> str:
    rows = verification_report()
    out: list[str] = []
    out.append("# Provider verification")
    out.append("")
    out.append(
        f"Verified on **{VERIFIED_ON}** against first-party documentation. This file is generated "
        "from `backend/providers/specs.py` by `scripts/build_provider_report.py`, so the app's "
        "provider cards and this record cannot disagree."
    )
    out.append("")
    out.append("## How to read the status column")
    out.append("")
    out.append("| Status | Meaning |")
    out.append("| --- | --- |")
    out.append("| verified | Free tier confirmed on the provider's own pages. Numbers below are from those pages. |")
    out.append("| conditional | Usable, but with a catch that changes how the agent behaves (a one-time credit, |")
    out.append("|  | a rotated preview model, or a quota the user must enable). |")
    out.append("| substituted | The specification named this provider as free; verification did not support that, |")
    out.append("|  | so a sanctioned substitute carries the chain slot. Documented in full below. |")
    out.append("| unverified | Listed so it can be configured, but terms were not confirmed. |")
    out.append("")
    out.append(f"**Free forever** ({len(FREE_FOREVER)}): " + ", ".join(sorted(FREE_FOREVER)))
    out.append("")
    out.append("**One-time credit** (ration these): " + ", ".join(sorted(ONE_TIME_CREDIT)))
    out.append("")
    out.append("## Substitutions")
    out.append("")
    for provider_id, substitution in SUBSTITUTIONS.items():
        out.append(f"### {provider_id} -> {substitution['substitute']}")
        out.append("")
        out.append(f"- Models used instead: `{substitution['models']}`")
        out.append(f"- Why: {substitution['reason']}")
        out.append(f"- Recorded: {substitution['verified_on']}")
        out.append("- Not a silent drop: the substitution is shown in Settings on the provider card, in the")
        out.append("  agent's chain editor, and here.")
        out.append("")
    out.append("## The table")
    out.append("")
    out.append("| Provider | Status | Needs key | Free tier | Limits | Sign up |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for row in rows:
        limits = "; ".join(f"{key}: {value}" for key, value in (row["limits"] or {}).items()) or "-"
        out.append(
            f"| {row['name']} (`{row['id']}`) | {row['verified']} | "
            f"{'yes' if row['requires_key'] else 'no'} | {row['free_tier']} | {limits} | {row['signup_url']} |"
        )
    out.append("")
    out.append("## Detail and caveats")
    out.append("")
    for row in rows:
        out.append(f"### {row['name']} (`{row['id']}`)")
        out.append("")
        out.append(f"- Status: **{row['verified']}** (checked {row['verified_on']})")
        out.append(f"- Free tier: {row['free_tier']}")
        if row["limits"]:
            out.append("- Limits: " + "; ".join(f"{key}: {value}" for key, value in row["limits"].items()))
        out.append(f"- Docs: {row['docs_url']}")
        if row["notes"]:
            out.append(f"- Notes: {row['notes']}")
        if row["substitution"]:
            out.append(
                f"- Substituted by: {row['substitution']['substitute']} ({row['substitution']['reason']})"
            )
        out.append("")
    out.append("## What was not verified")
    out.append("")
    out.append("- **Exact Gemini free-tier request numbers.** Google does not publish them as a stable figure;")
    out.append("  AI Studio shows them per project. The app therefore never displays a number it cannot")
    out.append("  confirm and instead links to AI Studio.")
    out.append("- **NVIDIA NIM credit size.** The grant exists; its size changes with promotions, so the UI says")
    out.append('  "credit-based" rather than inventing a figure.')
    out.append("- **Routeway's ongoing free access.** No first-party page confirmed it, hence the substitution.")
    out.append("- **Z.ai free coding tier.** The devpack FAQ confirms there is none; the cheap plan is paid.")
    out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(build(), encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
