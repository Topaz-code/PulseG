"""Screenshot capture, local-machine aware (spec C.6).

Two capture paths, chosen by how Godot is actually running for the task:

* **in-engine** - a small GDScript helper is written into the project and run with
  ``--script``; the frame comes from
  ``get_viewport().get_texture().get_image().save_png()``. Reliable when nothing is on
  screen (headless, CI, a locked workstation) and it captures exactly the game viewport,
  not the desktop.
* **os-level** - ``mss`` grabs the Godot window specifically (located by window title), for
  when the user is watching the game run in windowed mode.

The Tester agent never chooses by guesswork: :func:`capture` inspects the mode it was given
and falls back to the other path if the preferred one fails, recording which was used so the
Auditor's verdict can say what it actually looked at.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

log = logging.getLogger(__name__)

CAPTURE_SCRIPT_NAME = "pulseg_capture.gd"
CAPTURE_SCRIPT = '''extends SceneTree
## Written by PulseG Studio at capture time. Runs headless, waits for the scene to settle,
## then saves the main viewport to a PNG and quits. Safe to delete.

const OUT_PATH := "{out_path}"
const WAIT_FRAMES := {wait_frames}


func _initialize() -> void:
\t_run()


func _run() -> void:
\tvar frames := 0
\twhile (frames < WAIT_FRAMES and get_root().get_child_count() > 0):
\t\tawait process_frame
\t\tframes += 1
\tvar image := get_root().get_texture().get_image()
\tif image == null:
\t\tpush_error("PulseG capture: no viewport image available")
\t\tquit(1)
\t\treturn
\tvar error := image.save_png(OUT_PATH)
\tif error != OK:
\t\tpush_error("PulseG capture: save_png failed with error %d" % error)
\t\tquit(2)
\t\treturn
\tprint("PulseG capture: wrote ", OUT_PATH, " ", image.get_width(), "x", image.get_height())
\tquit(0)
'''


@dataclass
class CaptureResult:
    ok: bool
    mode: str
    path: str = ""
    message: str = ""
    width: int = 0
    height: int = 0
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "path": self.path,
            "message": self.message,
            "width": self.width,
            "height": self.height,
            "duration_ms": self.duration_ms,
        }


def shot_path(project_path: Path, task_id: str, index: int = 1) -> Path:
    """``screenshots/task_042_20260911T140312_001.png`` - sortable and traceable to the task."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    target_dir = project_path / "screenshots"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{task_id.lower()}_{stamp}_{index:03d}.png"


def capture(
    project_path: Path,
    task_id: str,
    *,
    mode: str = "auto",
    godot_executable: str = "",
    wait_frames: int = 45,
    window_title: str = "Godot",
    timeout_s: int = 90,
    count: int = 1,
    subdir: str = "screenshots",
) -> list[CaptureResult]:
    """Capture one or more frames. ``mode`` is ``headless``, ``windowed`` or ``auto``.

    ``auto`` prefers in-engine capture for headless runs and OS capture for windowed runs,
    which mirrors the specification's rule exactly.
    """
    results: list[CaptureResult] = []
    for index in range(1, max(1, count) + 1):
        target = shot_path(project_path, task_id, index)
        if subdir != "screenshots":
            target = project_path / subdir / target.name
            target.parent.mkdir(parents=True, exist_ok=True)
        result = _capture_one(
            project_path,
            target,
            mode=mode,
            godot_executable=godot_executable,
            wait_frames=wait_frames,
            window_title=window_title,
            timeout_s=timeout_s,
        )
        results.append(result)
        if not result.ok and index == 1:
            break
    return results


def _capture_one(
    project_path: Path,
    target: Path,
    *,
    mode: str,
    godot_executable: str,
    wait_frames: int,
    window_title: str,
    timeout_s: int,
) -> CaptureResult:
    started = time.perf_counter()
    prefer_os = mode == "windowed"
    if mode == "auto":
        prefer_os = _godot_window_present(window_title)

    order = ["os", "engine"] if prefer_os else ["engine", "os"]
    messages: list[str] = []
    for attempt in order:
        if attempt == "engine":
            result = _capture_in_engine(project_path, target, godot_executable, wait_frames, timeout_s)
        else:
            result = _capture_os_window(target, window_title)
        if result.ok:
            result.duration_ms = int((time.perf_counter() - started) * 1000)
            if messages:
                result.message += " (fallback after: " + "; ".join(messages) + ")"
            return result
        messages.append(f"{attempt}: {result.message}")

    return CaptureResult(
        ok=False,
        mode="failed",
        message=(
            "Both capture paths failed. In-engine: the project may not have a main scene or "
            "Godot may not be configured. OS-level: the Godot window was not found. "
            + " | ".join(messages)
        ),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


def _capture_in_engine(
    project_path: Path, target: Path, godot_executable: str, wait_frames: int, timeout_s: int
) -> CaptureResult:
    """Run the generated capture script through Godot headless."""
    from .godot_mcp_client import GodotMCPClient

    client = GodotMCPClient(godot_executable, project_path)
    if not client.cli_available:
        return CaptureResult(
            ok=False,
            mode="engine",
            message="No Godot executable configured, so in-engine capture is unavailable.",
        )
    script_path = project_path / CAPTURE_SCRIPT_NAME
    script_path.write_text(
        CAPTURE_SCRIPT.format(out_path=str(target).replace("\\", "/"), wait_frames=wait_frames),
        encoding="utf-8",
    )
    try:
        result = client.run_cli(
            ["--headless", "--path", str(project_path), "--script", str(script_path), "--quit-after", str(wait_frames * 3)],
            timeout=timeout_s,
            kill_after=None,
        )
    finally:
        # The helper is scaffolding, not project content: remove it so it never appears in
        # a git diff or confuses the Godot editor's script list.
        try:
            script_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass

    if not target.exists():
        return CaptureResult(
            ok=False,
            mode="engine",
            message=(
                "Godot ran but wrote no PNG. The project may fail to start, or the main "
                f"scene is missing. Output: {((result.stderr or result.stdout) or '')[-300:]}"
            ),
        )
    size = _png_size(target)
    return CaptureResult(
        ok=True,
        mode="engine",
        path=str(target),
        message="Captured the game viewport in-engine.",
        width=size[0],
        height=size[1],
    )


def _capture_os_window(target: Path, window_title: str) -> CaptureResult:
    """Grab the Godot window specifically, not the whole desktop."""
    try:
        import mss
        from PIL import Image
    except ImportError:
        return CaptureResult(
            ok=False, mode="os", message="mss/Pillow are not installed, so OS capture is unavailable."
        )

    try:
        import platform

        system = platform.system()
        region = None
        if system == "Windows":
            region = _find_window_rect_windows(window_title)
        elif system == "Darwin":
            region = _find_window_rect_macos(window_title)
        else:
            region = _find_window_rect_x11(window_title)

        with mss.mss() as grabber:
            if region:
                monitor = {
                    "left": region[0],
                    "top": region[1],
                    "width": max(1, region[2] - region[0]),
                    "height": max(1, region[3] - region[1]),
                }
            else:
                # Fall back to the primary monitor, but say so clearly.
                monitor = grabber.monitors[1]
            shot = grabber.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        image.save(target)
    except Exception as exc:
        return CaptureResult(ok=False, mode="os", message=f"OS capture failed: {exc}")

    size = _png_size(target)
    found = "the Godot window" if region else "the whole primary screen (Godot window not located)"
    return CaptureResult(
        ok=True,
        mode="os",
        path=str(target),
        message=f"Captured {found}.",
        width=size[0],
        height=size[1],
    )


def _godot_window_present(window_title: str) -> bool:
    """True when a Godot window with this title is on screen (drives the auto choice)."""
    import platform

    system = platform.system()
    if system == "Windows":
        return _find_window_rect_windows(window_title) is not None
    if system == "Darwin":
        return _find_window_rect_macos(window_title) is not None
    return _find_window_rect_x11(window_title) is not None


def _find_window_rect_windows(title_hint: str) -> tuple[int, int, int, int] | None:
    """Locate a window by title using the Win32 API (no extra dependency)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        matches: list[tuple[int, int, int, int]] = []

        def callback(hwnd, _lparam):  # pragma: no cover - Windows only
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if title_hint.lower() in buffer.value.lower() and user32.IsWindowVisible(hwnd):
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    matches.append((rect.left, rect.top, rect.right, rect.bottom))
            return True

        user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback), 0)
        return matches[0] if matches else None
    except Exception as exc:  # pragma: no cover
        log.debug("Windows window lookup failed: %s", exc)
        return None


def _find_window_rect_macos(title_hint: str) -> tuple[int, int, int, int] | None:
    try:  # pragma: no cover - macOS only
        import subprocess

        script = (
            'tell application "System Events" to get {position, size} of first window of '
            f'(first process whose name contains "{title_hint}")'
        )
        output = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=10, check=False
        ).stdout
        numbers = [int(float(part)) for part in output.replace("{", "").replace("}", "").split(",") if part.strip()]
        if len(numbers) >= 4:
            x, y, width, height = numbers[:4]
            return (x, y, x + width, y + height)
    except Exception:
        pass
    return None


def _find_window_rect_x11(title_hint: str) -> tuple[int, int, int, int] | None:
    import shutil
    import subprocess

    if not shutil.which("xdotool"):
        return None
    try:  # pragma: no cover - Linux only
        window_id = subprocess.run(
            ["xdotool", "search", "--name", title_hint],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.splitlines()
        if not window_id:
            return None
        geometry = subprocess.run(
            ["xdotool", "getwindowgeometry", "--shell", window_id[0]],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout
        values = dict(
            line.split("=") for line in geometry.splitlines() if "=" in line
        )
        x, y = int(values.get("X", 0)), int(values.get("Y", 0))
        width, height = int(values.get("WIDTH", 0)), int(values.get("HEIGHT", 0))
        if width and height:
            return (x, y, x + width, y + height)
    except Exception:
        pass
    return None


def _png_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.width, image.height
    except Exception:
        return (0, 0)


def attach_screenshots(project_path: Path, task_id: str, paths: Sequence[str]) -> list[str]:
    """Return project-relative paths, filtering out anything outside the project."""
    root = project_path.resolve()
    out: list[str] = []
    for raw in paths:
        try:
            relative = str(Path(raw).resolve().relative_to(root))
        except ValueError:
            continue
        out.append(relative)
    return out


def describe_modes() -> dict[str, str]:
    """Short explanation for the UI, so the choice is never a mystery."""
    return {
        "engine": (
            "In-engine capture: Godot renders one frame headless and saves the viewport. "
            "Most reliable when nothing is on screen."
        ),
        "os": (
            "Screen capture: grabs the Godot window from the desktop. Used when you are "
            "watching the game run."
        ),
        "auto": "Automatic: uses screen capture when a Godot window is visible, otherwise in-engine.",
    }
