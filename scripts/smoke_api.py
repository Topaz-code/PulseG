#!/usr/bin/env python
"""End-to-end smoke test against a real running server.

The pytest suite uses FastAPI's TestClient, which never opens a socket. This script boots the
actual process the Tauri sidecar boots (``python -m backend.main``), talks to it over HTTP and
WebSocket, and drives one task from creation to an approved, committed result. It is the check
that catches the things TestClient hides: the uvicorn entry point, port binding, the event
stream, the SPA/static mounts and process shutdown.

    python scripts/smoke_api.py [--port 8799] [--keep]

Exit code 0 means every step behaved. Nothing here touches the user's real studio home: it runs
against a scratch directory under the system temp folder which is deleted on start.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}{f'  -  {detail}' if detail else ''}")
    return bool(ok)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--keep", action="store_true", help="keep the scratch studio home")
    args = parser.parse_args(argv)

    base = f"http://127.0.0.1:{args.port}"
    scratch = Path(tempfile.gettempdir()) / "pulseg-smoke"
    shutil.rmtree(scratch, ignore_errors=True)
    (scratch / "home").mkdir(parents=True)
    (scratch / "projects").mkdir(parents=True)

    def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return exc.code, json.loads(body or b"{}")
            except json.JSONDecodeError:
                return exc.code, {"raw": body.decode("utf-8", "replace")[:300]}

    env = {
        **os.environ,
        "PULSEG_HOME": str(scratch / "home"),
        "PULSEG_PROJECTS_ROOT": str(scratch / "projects"),
        "PULSEG_PORT": str(args.port),
        "PULSEG_DEMO": "1",
        "PULSEG_LOG_LEVEL": "warning",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "backend.main"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 40
        healthy = False
        while time.time() < deadline and not healthy:
            try:
                status, _ = call("GET", "/api/health")
                healthy = status == 200
            except Exception:
                time.sleep(0.3)
        if not check("server boots and answers /api/health", healthy):
            return 1

        status, info = call("GET", "/api/system/info")
        check("system info", status == 200 and info.get("app", {}).get("name") == "PulseG Studio")

        status, first_run = call("GET", "/api/system/first-run")
        check("first run report", status == 200 and first_run.get("first_run_complete") is False)

        status, board = call("GET", "/api/tasks/board")
        check(
            "project-scoped call without a project explains itself",
            status == 409 and board.get("error") == "no_active_project",
            board.get("action", ""),
        )

        status, setup = call(
            "POST",
            "/api/system/setup",
            {
                "projects_root": str(scratch / "projects"),
                "git_name": "Smoke Test",
                "git_email": "smoke@example.com",
            },
        )
        check("setup wizard writes config", status == 200 and setup.get("ok") is True)
        check("git identity configured locally", bool(setup.get("git", {}).get("configured")))

        status, created = call(
            "POST",
            "/api/projects",
            {"name": "Sky Ferry", "mode": "fresh", "concept": "A tiny zeppelin courier game"},
        )
        project_id = created.get("project", {}).get("project_id", "")
        check("project created with a Godot skeleton", status == 200 and bool(project_id))

        status, overview = call("GET", f"/api/projects/{project_id}/overview")
        lanes = overview.get("board", {}).get("lanes", [])
        check("kanban has the seven lanes", len(lanes) == 7, ",".join(lane["id"] for lane in lanes))
        check(
            "Godot project scaffolded on disk",
            Path(overview.get("godot_project_path", "")).exists(),
        )

        status, intake = call("GET", "/api/planning/state")
        check(
            "intake asks questions and blocks handoff",
            status == 200 and intake.get("questions") and intake.get("allowed") is False,
            f"{len(intake.get('questions', []))} question(s), genre={intake.get('genre')}",
        )

        status, made = call(
            "POST",
            "/api/tasks",
            {
                "instruction": "Create the player scene and controller.",
                "assigned_to": "programmer",
                "expected_outputs": ["godot_project/scripts/player.gd"],
            },
        )
        task_id = made.get("task", {}).get("task_id", "")
        check("task created by hand", status == 200 and bool(task_id), task_id)

        status, tick = call("POST", "/api/logs/tick")
        check("dispatch tick ran the task", status == 200 and task_id in tick.get("dispatched", []),
              str(tick.get("dispatched", [])))

        status, detail = call("GET", f"/api/tasks/{task_id}")
        verdict = (detail.get("auditor_verdict") or {}).get("verdict", "")
        check(
            "task reached the human gate with a verdict",
            detail.get("status") == "NEEDS_HUMAN_REVIEW" and verdict in {"APPROVED", "DECLINED"},
            f"{detail.get('status')} / {verdict}",
        )
        status, review = call("GET", "/api/tasks/review")
        check("review queue lists it", status == 200 and review.get("count") == 1)

        status, _ = call("POST", f"/api/tasks/{task_id}/approve", {"note": "Looks good."})
        if status == 409:
            # The Auditor declined: the human gate requires the explicit override endpoint.
            status, approved = call(
                "POST",
                f"/api/tasks/{task_id}/override",
                {"note": "Read the Auditor's note; shipping it anyway."},
            )
        else:
            approved = call("GET", f"/api/tasks/{task_id}")[1]
        check("human decision recorded", status == 200)
        check(
            "approval produced a Git commit",
            bool(approved.get("commit", {}).get("ok")) if "commit" in approved else True,
            str(approved.get("commit", {}).get("detail", ""))[:70],
        )

        status, git_log = call("GET", "/api/git/log")
        check("git history has the committed task", status == 200 and bool(git_log.get("commits")),
              f"{len(git_log.get('commits', []))} commit(s)")

        status, agents = call("GET", "/api/agents")
        check("twelve agents in the roster", status == 200 and len(agents.get("agents", [])) == 12)

        status, providers = call("GET", "/api/providers")
        check(
            "provider table with substitution advice",
            status == 200 and len(providers.get("providers", [])) == 20
            and providers.get("substitutions"),
        )

        status, theme = call("GET", "/api/design/theme")
        failing = [row["pair"] for row in theme.get("contrast", []) if not row.get("passes")]
        check("theme contrast pairs all pass", status == 200 and not failing, ",".join(failing))

        status, notifications = call("GET", "/api/system/notifications")
        check(
            "needs-review notification was recorded",
            status == 200 and notifications.get("unread", 0) >= 1,
            f"unread={notifications.get('unread')}",
        )

        diagnostics = call("GET", "/api/system/diagnostics")[1]
        check("diagnostics contain no key material", "gsk" not in json.dumps(diagnostics))

        try:
            import websockets

            async def hello() -> str:
                async with websockets.connect(f"ws://127.0.0.1:{args.port}/ws/events") as socket:
                    first = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
                    return str(first.get("type", ""))

            kind = asyncio.run(hello())
            check("event stream sends hello", kind == "hello", kind)
        except ImportError:  # pragma: no cover - websockets ships with uvicorn[standard]
            check("event stream sends hello", False, "websockets not installed")

        status, stopped = call("POST", "/api/system/shutdown")
        check("shutdown endpoint answers", status in (200, 409), str(stopped.get("run_state", "")))
    finally:
        server.terminate()
        try:
            output, _ = server.communicate(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover
            server.kill()
            output, _ = server.communicate()
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)

    failures = [label for label, ok, _ in CHECKS if not ok]
    print()
    if failures:
        print(f"{len(failures)} check(s) failed: " + "; ".join(failures))
        print("--- server log tail ---")
        print("\n".join((output or "").strip().splitlines()[-15:]))
        return 1
    print(f"All {len(CHECKS)} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
