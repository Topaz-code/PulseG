"""Copy the CI workflows into .github/workflows/.

    python scripts/install_ci_workflows.py

The workflows live in ``ci/workflows/`` rather than in ``.github/workflows/`` for one reason:
GitHub refuses to let an app token create files under ``.github/workflows/`` without the
``workflows`` permission, and the automation that wrote this repository was not granted it. Moving
them to a normal directory keeps them in version control - reviewed, diffable, part of the project -
instead of leaving them on one machine.

Running this script puts them where GitHub looks. Do it with a token that has ``workflows``
permission; after that, normal pushes keep them up to date.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "ci" / "workflows"
TARGET = REPO_ROOT / ".github" / "workflows"


def main() -> int:
    if not SOURCE.is_dir():
        print(f"No {SOURCE.relative_to(REPO_ROOT)} directory found.", file=sys.stderr)
        return 1

    workflows = sorted(SOURCE.glob("*.yml"))
    if not workflows:
        print(f"No workflows in {SOURCE.relative_to(REPO_ROOT)}.", file=sys.stderr)
        return 1

    TARGET.mkdir(parents=True, exist_ok=True)
    for workflow in workflows:
        destination = TARGET / workflow.name
        shutil.copy2(workflow, destination)
        print(f"installed {destination.relative_to(REPO_ROOT)}")

    print()
    print("Commit and push with a token that has the 'workflows' permission:")
    print("    git add .github/workflows && git commit -m 'Enable CI' && git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
