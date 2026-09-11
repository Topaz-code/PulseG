"""Tester agent: runs the game, captures evidence, and reports honestly.

The Tester is the only agent with vision, and it is the only agent allowed to say "this does
not actually work". It does three things:

1. **Static** - validate every ``.gd`` script through Godot's parser.
2. **Runtime** - run the project headless for a bounded time and read the console for errors.
   Errors are never swallowed: "no error dialogs" is one of the checks the specification
   requires, and Godot prints them to stdout even headless.
3. **Visual** - capture screenshots (in-engine when headless, OS-level when windowed) and
   look at them, reporting what is actually on screen rather than what the code claims.

Everything it finds goes into ``reports/`` as Markdown, and the screenshots are attached to
the task so the human review drawer has real images to look at.
"""
from __future__ import annotations

import logging
import re

from ..core.models import Task
from ..mcp import screenshot_mcp
from ..mcp.godot_mcp_client import GodotMCPClient
from ..mcp.filesystem_mcp import write_project_file
from ..orchestration import memory
from .base import AgentContext, AgentRunResult, apply_skill_checks, note_agent_state, run_text_agent

log = logging.getLogger(__name__)

AGENT_ID = "tester"
DEFAULT_CHECKS = (
    "player visible on screen",
    "physics behave sensibly (no teleporting, no falling through the floor)",
    "collisions fire when expected",
    "animations advance rather than freezing",
    "UI elements render legibly",
    "no error dialogs",
    "framerate stable",
)
ERROR_PATTERNS = (
    re.compile(r"SCRIPT ERROR", re.IGNORECASE),
    re.compile(r"ERROR:.*\.gd", re.IGNORECASE),
    re.compile(r"Parse Error", re.IGNORECASE),
    re.compile(r"Failed to load", re.IGNORECASE),
    re.compile(r"Invalid call", re.IGNORECASE),
    re.compile(r"Nonexistent function", re.IGNORECASE),
)


def _godot_files(ctx: AgentContext) -> list[str]:
    root = ctx.project_path / "godot_project"
    if not root.exists():
        return []
    return [str(path.relative_to(root)) for path in sorted(root.rglob("*.gd"))][:120]


def _console_errors(text: str) -> list[str]:
    findings: list[str] = []
    for line in (text or "").splitlines():
        if any(pattern.search(line) for pattern in ERROR_PATTERNS):
            cleaned = line.strip()
            if cleaned and cleaned not in findings:
                findings.append(cleaned[:300])
    return findings[:20]


def static_checks(ctx: AgentContext) -> dict:
    client = GodotMCPClient(executable=ctx.godot_executable, project_path=ctx.project_path / "godot_project")
    try:
        files = _godot_files(ctx)
        if not files:
            return {"ok": False, "message": "No GDScript files found in godot_project/.", "files": 0}
        result = client.validate_scripts(files)
        return {
            "ok": result.ok,
            "message": result.message,
            "transport": result.transport,
            "files": len(files),
            "stderr_tail": result.stderr[-1500:],
        }
    finally:
        client.close()


def runtime_check(ctx: AgentContext, *, seconds: float = 10.0) -> dict:
    client = GodotMCPClient(executable=ctx.godot_executable, project_path=ctx.project_path / "godot_project")
    try:
        result = client.run_headless(seconds=seconds)
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        errors = _console_errors(combined)
        return {
            "ok": not errors,
            "message": result.message,
            "errors": errors,
            "ran_for_s": seconds,
            "stdout_tail": (result.stdout or "")[-2000:],
            "note": (
                "Headless runs cannot show a window, so UI checks that need a visible window "
                "are covered by the screenshot pass."
            ),
        }
    finally:
        client.close()


def windowed_run(ctx: AgentContext, *, seconds: float = 12.0) -> dict:
    """Launch the game visibly, let it play, and capture the window.

    Used when the human has asked to watch the run (``ctx.extra['live']``) or when there is a
    real display attached. On a headless CI box this degrades to the in-engine path.
    """
    client = GodotMCPClient(executable=ctx.godot_executable, project_path=ctx.project_path / "godot_project")
    process = None
    try:
        process = client.run_windowed()
        import time

        time.sleep(max(2.0, min(seconds, 30.0)))
        captures = screenshot_mcp.capture(
            ctx.project_path,
            ctx.task_id or "manual",
            mode="os",
            godot_executable=ctx.godot_executable,
            count=2,
        )
        for capture in captures:
            if not capture.ok:
                log.info("OS capture failed: %s", capture.message)
        return {
            "ok": any(item.ok for item in captures),
            "captures": [item.as_dict() for item in captures],
            "message": "; ".join(item.message for item in captures) or "no capture attempted",
        }
    finally:
        if process is not None:
            try:
                process.terminate()
            except Exception:  # pragma: no cover - best effort
                pass
        client.close()


def capture_evidence(ctx: AgentContext, *, mode: str = "auto", count: int = 4) -> list[dict]:
    captures = screenshot_mcp.capture(
        ctx.project_path,
        ctx.task_id or "manual",
        mode=mode,
        godot_executable=ctx.godot_executable,
        count=count,
        window_title="PulseG",
    )
    return [item.as_dict() for item in captures]


def _report_markdown(ctx: AgentContext, static: dict, runtime: dict, vision: dict, captures: list[dict]) -> str:
    task = ctx.task
    title = task.title if task else "Manual test run"
    lines = [
        f"# Test report: {title}",
        "",
        f"- Task: {ctx.task_id or 'n/a'}",
        f"- Agent: {AGENT_ID}",
        f"- Static parse check: {'pass' if static.get('ok') else 'fail'} - {static.get('message', '')}",
        f"- Runtime check: {'pass' if runtime.get('ok') else 'fail'} - {runtime.get('message', '')}",
        f"- Screenshots: {len([item for item in captures if item.get('ok')])} captured",
        "",
        "## Runtime errors",
    ]
    errors = runtime.get("errors") or []
    lines.extend([f"- {error}" for error in errors] or ["- none"])
    lines += ["", "## Screenshot review"]
    lines.append(vision.get("summary", "No visual review was performed."))
    for item in vision.get("findings", [])[:20]:
        if isinstance(item, dict):
            lines.append(
                f"- [{item.get('severity', 'info')}] {item.get('check', 'check')}: "
                f"{item.get('observation', '')}"
            )
        elif isinstance(item, str):
            lines.append(f"- {item}")
    lines += ["", "## Evidence files"]
    lines.extend([f"- {item['path']}" for item in captures if item.get("ok")] or ["- none"])
    lines += ["", "## Verdict suggestion"]
    lines.append(vision.get("verdict_suggestion", "Review the findings above."))
    return "\n".join(lines)


def run(ctx: AgentContext, *, live: bool = False) -> AgentRunResult:
    static = static_checks(ctx)
    runtime = runtime_check(ctx)
    captures = capture_evidence(ctx, mode="auto", count=4)
    if live or ctx.extra.get("live"):
        windowed = windowed_run(ctx)
        captures.extend(windowed.get("captures", []))

    image_paths = [str(ctx.project_path / item["path"]) for item in captures if item.get("ok") and item.get("path")]

    vision_instruction = (
        "You are reviewing screenshots of the game as it actually ran.\n\n"
        f"STATIC CHECK: {static.get('message')}\n"
        f"RUNTIME CHECK: {runtime.get('message')}\n"
        f"RUNTIME ERRORS: {'; '.join(runtime.get('errors') or []) or 'none'}\n\n"
        "For each requested check, say what you actually see in the images. If an image is "
        "black, blurry, or shows a menu instead of gameplay, say that plainly - do not assume "
        "the code is correct because it compiled. Return the JSON object in your system prompt."
    )
    run_result = run_text_agent(
        ctx,
        extra_instruction=vision_instruction,
        images=image_paths or None,
        require_json=True,
    )

    vision_payload: dict = {}
    if run_result.ok and run_result.payload:
        vision_payload = run_result.payload
    else:
        vision_payload = {
            "summary": (
                "No visual review was possible: "
                + (run_result.error or "the vision chain returned nothing usable.")
            ),
            "findings": [],
            "verdict_suggestion": "A human should look at the screenshots before approval.",
        }

    report = _report_markdown(ctx, static, runtime, vision_payload, captures)
    relative = f"reports/test_{ctx.task_id.lower() if ctx.task_id else 'manual'}.md"
    try:
        write_project_file(ctx.project_path, relative, report)
        run_result.artifacts.append(relative)
    except Exception as exc:
        run_result.problems.append(f"Could not write the test report: {exc}")

    run_result.screenshots = screenshot_mcp.attach_screenshots(
        ctx.project_path, ctx.task_id or "manual", [item["path"] for item in captures if item.get("ok") and item.get("path")]
    )
    bus = ctx.extra.get("task_bus")
    if bus is not None and ctx.task_id:
        for shot in run_result.screenshots:
            try:
                bus.add_screenshot(ctx.task_id, shot)
            except Exception as exc:  # pragma: no cover - defensive
                log.info("Could not attach %s to %s: %s", shot, ctx.task_id, exc)

    # Deterministic gating: a script error or a failed capture is a fail, whatever the
    # model's prose says about it.
    hard_failures: list[str] = []
    if not static.get("ok"):
        hard_failures.append(f"static parse check failed: {static.get('message')}")
    if runtime.get("errors"):
        hard_failures.append(f"{len(runtime['errors'])} engine error line(s) in the console")
    if not run_result.screenshots:
        hard_failures.append("no screenshot could be captured")
    if hard_failures:
        run_result.ok = False
        run_result.error = "; ".join(hard_failures)
    if run_result.problems or vision_payload.get("findings"):
        memory.append_progress(
            ctx.project_path,
            f"test run: {'fail' if hard_failures else 'pass'} ({'; '.join(hard_failures) or 'all hard checks passed'})",
            agent=AGENT_ID,
            task_id=ctx.task_id,
            status="FAIL" if hard_failures else "OK",
        )

    run_result.payload = {
        "static": static,
        "runtime": runtime,
        "vision": vision_payload,
        "screenshots": run_result.screenshots,
        "hard_failures": hard_failures,
        "checks": DEFAULT_CHECKS,
    }
    apply_skill_checks(ctx, run_result)
    note_agent_state(ctx, run_result)
    return run_result


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID
