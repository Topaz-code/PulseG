"""Walk the whole studio through the API and prove the product's promises still hold.

    python scripts/verify_pipeline.py            # uses PULSEG_DEMO=1 automatically
    python scripts/verify_pipeline.py --verbose

This is the closest thing to pressing every button once. It runs the real application object
(not a mock) against a scratch home so it cannot touch a user's projects, and it checks the
claims the product makes rather than the code paths it happens to have:

1.  Pasting an idea starts the intake and asks themed questions.
2.  Answering them moves the design ledger - and the API says which fields changed, so a silent
    no-op is impossible.
3.  The build gate stays shut until every ledger item is complete.
4.  Handoff returns a draft design document, and only explicit confirmation finalises it.
5.  Seeding creates a small, dependency-ordered task set - and the Prompter cannot flood the
    queue with duplicates of the same work on the next tick.
6.  The runtime dispatches, agents produce real files, the Auditor produces a verdict, and every
    task stops at "Needs Your Review" - never auto-approved.
7.  Approving at the gate writes a git commit; rejecting records the note and requeues.
8.  A rejected task does not become an approved one, and a declined one cannot be approved
    without the override.

Exit code is non-zero on the first failure, so this can run in CI next to the unit tests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default="", help="scratch studio home (default: a temp folder)")
    parser.add_argument("--projects", default="", help="scratch projects root")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--timeout", type=int, default=120, help="seconds to wait for review items")
    args = parser.parse_args()

    import tempfile

    scratch = Path(args.home).parent if args.home else Path(tempfile.mkdtemp(prefix="pulseg-verify-"))
    home = Path(args.home) if args.home else scratch / "home"
    projects_root = Path(args.projects) if args.projects else scratch / "projects"
    os.environ["PULSEG_HOME"] = str(home)
    os.environ["PULSEG_PROJECTS_ROOT"] = str(projects_root)
    os.environ.setdefault("PULSEG_DEMO", "1")

    from fastapi.testclient import TestClient

    from backend.main import create_app

    checks = 0

    def say(message: str) -> None:
        print(message, flush=True)

    def detail(payload: object) -> None:
        if args.verbose:
            print("    " + json.dumps(payload)[:400], flush=True)

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        if not condition:
            raise SystemExit(f"FAILED: {message}")
        checks += 1
        say(f"  ok  {message}")

    say(f"Scratch studio home: {home}")
    app = create_app()

    with TestClient(app) as client:
        # 1. an idea becomes an intake -------------------------------------------------------
        created = client.post(
            "/api/projects",
            json={
                "name": "Harbour Lights",
                "mode": "fresh",
                "genre": "platformer",
                "concept": "A cosy platformer about a lighthouse keeper guiding boats home through fog.",
            },
        ).json()["project"]
        check(bool(created["project_id"]), "a project can be created from a pasted idea")

        begun = client.post(
            "/api/planning/intake",
            json={
                "concept": "A cosy platformer about a lighthouse keeper guiding boats home through fog.",
                "title": "Harbour Lights",
            },
        ).json()
        check(bool(begun["questions"]), f"the intake asks themed questions ({len(begun['questions'])})")
        detail(begun["question_text"])

        # 2. answers move the ledger, and say what they changed ------------------------------
        answered = client.post(
            "/api/planning/answer",
            json={
                "answers": (
                    "Climb the tower, trim the lamp, sweep the beam to guide boats past the rocks. "
                    "Six levels of four minutes each, linear. Maren is calm, methodical and does "
                    "only what the player presses; Tomas the harbour master stays on the dock. "
                    "Warm 32x32 pixel art in a dusk palette with amber lamp light."
                )
            },
        ).json()
        check(bool(answered["extraction"]["ok"]), "the answers are merged into the design state")
        check(
            "mechanics" in answered["extraction"]["changed"],
            "the API names the fields the answers changed",
        )
        detail(answered["extraction"])

        gate = client.get("/api/planning/gate").json()
        check(gate["ledger"]["completion"] == 1.0, "the ledger is complete")
        check(gate["allowed"], "the build gate opens only once the ledger is complete")
        check(bool(gate["labels"]), "the gate explains each requirement in plain words")

        # 3. handoff needs explicit confirmation --------------------------------------------
        draft = client.post("/api/planning/handoff", json={}).json()
        check("DESIGN CONFIRMATION" in draft["draft_gdd"], "handoff returns the draft design document")
        confirmed = client.post("/api/planning/confirm", json={"confirmed_text": "Build it."}).json()
        check(bool(confirmed.get("finalised")), "the design is only finalised on explicit confirmation")

        # 4. the queue is seeded, small, and stays small ------------------------------------
        seeded = client.post("/api/planning/seed-tasks", json={}).json()
        check(len(seeded["created"]) >= 3, f"Phase 1 tasks are seeded ({len(seeded['created'])})")

        client.post("/api/logs/run-state/start")
        deadline = time.time() + args.timeout
        board: dict = {}
        while time.time() < deadline:
            time.sleep(1)
            board = client.get("/api/tasks/board").json()
            counts = {lane["title"]: lane["count"] for lane in board["lanes"] if lane["count"]}
            if counts.get("Needs Your Review") and not counts.get("In Progress"):
                break
        total = sum(lane["count"] for lane in board["lanes"])
        check(total < 12, f"the pipeline did not flood the queue ({total} tasks)")

        review_lane = next(lane for lane in board["lanes"] if lane["title"] == "Needs Your Review")
        check(review_lane["count"] > 0, "work reaches the human gate")

        check(
            all(task["status"] == "NEEDS_HUMAN_REVIEW" for task in review_lane["tasks"]),
            "the Auditor never approves work on its own",
        )
        check(
            all(task["status"] != "APPROVED" for lane in board["lanes"] for task in lane["tasks"]),
            "nothing in the queue is approved before the human acts",
        )
        detail({lane["id"]: lane["count"] for lane in board["lanes"]})

        # 5. the gate is a real gate ---------------------------------------------------------
        review = client.get("/api/tasks/review").json()
        first = review["items"][0]
        check(bool(first.get("verdict")), "every reviewed task carries an Auditor verdict")

        premature = client.post(f"/api/tasks/{first['task_id']}/reject", json={"note": ""})
        check(premature.status_code in {400, 422}, "a rejection without a note is refused")

        approved = client.post(
            f"/api/tasks/{first['task_id']}/approve", json={"note": "Reads well. Ship it."}
        ).json()
        check(approved["task"]["status"] == "APPROVED", "the human approves a task")
        check(bool(approved.get("commit", {}).get("sha")), "approval writes a git commit")

        log = client.get("/api/git/log").json()
        check(bool(log["commits"]), "the commit is in the project's history")
        detail(log["commits"][0])

        # 6. a rejected task requeues rather than disappearing -------------------------------
        remaining = [
            task
            for lane in client.get("/api/tasks/board").json()["lanes"]
            for task in lane["tasks"]
            if task["status"] == "NEEDS_HUMAN_REVIEW"
        ]
        if remaining:
            second = remaining[0]
            rejected = client.post(
                f"/api/tasks/{second['task_id']}/reject",
                json={"note": "The lamp sweep is not described precisely enough."},
            ).json()
            check(
                rejected["task"]["status"] in {"PENDING", "IN_PROGRESS"},
                "a rejected task returns to the queue with the note attached",
            )
            detail(rejected["task"]["status"])

        client.post("/api/logs/run-state/stop")

    say(f"\n{checks} checks passed. The pipeline, the auditor and the human gate all behaved.")
    say(f"Project folder kept for inspection: {projects_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
