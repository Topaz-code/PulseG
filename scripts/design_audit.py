"""Run the D.7 design-taste checklist over the frontend and write the audit log.

    python scripts/design_audit.py            # audits src/views, writes project-log
    python scripts/design_audit.py --check    # exits non-zero if any screen fails

This is a build step, not a review promise. If a view regresses - a second primary action, a
hardcoded colour, a missing empty state - the audit says so on the next run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.skills import design_taste  # noqa: E402

DEFAULT_TARGET = REPO_ROOT / "project-log" / "DESIGN_TASTE_AUDIT.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", default=str(REPO_ROOT / "src" / "views"), help="directory of .tsx view sources")
    parser.add_argument("--out", default=str(DEFAULT_TARGET), help="markdown audit log to write")
    parser.add_argument("--check", action="store_true", help="exit non-zero when a screen fails the checklist")
    args = parser.parse_args()

    views_dir = Path(args.views)
    reviews, combined = design_taste.review_repo(views_dir)
    theme_result = design_taste.theme_review()
    target = design_taste.write_audit_log(Path(args.out), reviews, theme_result)
    print(f"{combined.summary}")
    print(f"theme: {theme_result.summary}")
    print(f"wrote {target}")

    failing = [review["screen"] for review in reviews if not review["passing"]]
    if failing:
        print("failing screens: " + ", ".join(failing))
    if args.check and (failing or theme_result.findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
