"""Godot integration: MCP server when available, direct CLI otherwise.

The specification wires the Programmer and Tester to ``Coding-Solo/godot-mcp`` over stdio.
That server is a Node child process the user has to install, so this module has **two
transports** and picks automatically:

1. **MCP** (stdio JSON-RPC). Used when the server is reachable; gives scene/script editing
   tools, project metadata and the debug-output pipe.
2. **Direct CLI** (``godot --headless``). Always available once the user sets the Godot path
   in the Setup Wizard. Used for ``--check-only`` script validation, headless project runs
   and ``--export-debug Web``.

What the MCP server actually exposes was verified against ``@coding-solo/godot-mcp`` 0.1.0
(14 tools: launch_editor, run_project, get_debug_output, stop_project, get_godot_version,
list_projects, get_project_info, create_scene, add_node, load_sprite, export_mesh_library,
save_scene, get_uid, update_project_uids). Note what is **not** there: script validation and
the Web export. Both live on the CLI path, which is why the CLI is not a downgrade - it is
the only route to the acceptance-critical "export the game to the browser" step. Anything
that claims ``godot.validate`` over MCP is a bug, not a feature.

Having the CLI path is what makes the app work on day one for a user who has Godot but has
not installed any MCP server - and the dashboard always shows which transport is in use, so
nobody is misled about what is doing the work.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

DEFAULT_MCP_COMMAND = ("npx", "-y", "@coding-solo/godot-mcp")
EXPORT_TIMEOUT_S = 300
CHECK_TIMEOUT_S = 120


@dataclass
class GodotResult:
    ok: bool
    transport: str
    message: str = ""
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "transport": self.transport,
            "message": self.message,
            "duration_ms": self.duration_ms,
            "stdout_tail": self.stdout[-800:],
            "stderr_tail": self.stderr[-800:],
            **({"data": self.data} if self.data else {}),
        }


class GodotError(RuntimeError):
    pass


class GodotMCPClient:
    """Session-scoped client for one Godot project."""

    def __init__(
        self,
        executable: str = "",
        project_path: Path | None = None,
        *,
        mcp_command: Sequence[str] | None = None,
        prefer_mcp: bool = True,
    ) -> None:
        self.executable = executable
        self.project_path = project_path
        self.mcp_command = list(mcp_command or DEFAULT_MCP_COMMAND)
        self.prefer_mcp = prefer_mcp
        self._mcp: MCPStdioSession | None = None
        self._mcp_checked = False

    # --- transports -------------------------------------------------------------

    @property
    def cli_available(self) -> bool:
        return bool(self.executable and Path(self.executable).exists()) or bool(
            self.executable and shutil.which(self.executable)
        )

    def mcp_available(self) -> bool:
        """Probe the MCP server once per client. Never raises.

        The MCP server drives an installed Godot editor, so when no Godot executable is
        configured there is nothing to probe for: skip the process spawn and let the caller
        fall through to the "set your Godot path" message.
        """
        if not self.prefer_mcp or not self.cli_available:
            return False
        if self._mcp_checked:
            return self._mcp is not None
        self._mcp_checked = True
        if shutil.which(self.mcp_command[0]) is None:
            log.info(
                "Godot MCP server not reachable (%s not on PATH). Falling back to the Godot CLI.",
                self.mcp_command[0],
            )
            return False
        session = MCPStdioSession(self.mcp_command)
        if session.start():
            self._mcp = session
            return True
        return False

    def transport(self) -> str:
        if self.cli_available and self.mcp_available():
            return "mcp"
        if self.cli_available:
            return "cli"
        return "unavailable"

    def close(self) -> None:
        if self._mcp:
            self._mcp.stop()
            self._mcp = None

    # --- operations -------------------------------------------------------------

    def validate_scripts(self, files: Iterable[str] = ()) -> GodotResult:
        """Validate GDScript. Prefers ``--check-only`` per file via the CLI.

        Existence and basic parse errors are caught here - before the Tester spends a
        screenshot cycle on a project that cannot even load.
        """
        targets = [path for path in files if path.endswith(".gd")]
        started = time.perf_counter()
        if not targets and not self.cli_available:
            # Nothing to check and no Godot installed: this is not a failure, it is a no-op.
            return GodotResult(
                ok=True,
                transport="unavailable",
                message="No scripts to check.",
                duration_ms=0,
            )

        if not self.cli_available:
            return GodotResult(
                ok=False,
                transport="unavailable",
                message=(
                    "Neither the Godot MCP server nor a Godot executable is available. "
                    "Set the Godot path in Settings, or install the MCP server with "
                    "'npm i -g @coding-solo/godot-mcp'."
                ),
            )

        problems: list[str] = []
        for relative in targets:
            full = (self.project_path or Path(".")) / relative
            if not full.exists():
                problems.append(f"{relative} does not exist")
                continue
            result = self.run_cli(["--headless", "--check-only", "--script", str(full)], timeout=CHECK_TIMEOUT_S)
            if not result.ok and "SCRIPT ERROR" in (result.stderr + result.stdout):
                problems.append(f"{relative}: {(result.stderr or result.stdout).strip()[:300]}")
        duration = int((time.perf_counter() - started) * 1000)
        if problems:
            return GodotResult(
                ok=False,
                transport="cli",
                message="; ".join(problems[:4]),
                duration_ms=duration,
            )
        return GodotResult(
            ok=True,
            transport="cli",
            message=f"Checked {len(targets)} script(s); no parse errors."
            if targets
            else "No scripts to check.",
            duration_ms=duration,
        )

    def run_headless(
        self, *, seconds: float = 8.0, extra_args: Sequence[str] = (), capture_script: str = ""
    ) -> GodotResult:
        """Run the project headless for a bounded time - the automated build/test cycle."""
        args = ["--headless", "--path", str(self.project_path)]
        if capture_script:
            args += ["--script", capture_script]
        args += list(extra_args)
        started = time.perf_counter()
        result = self.run_cli(args, timeout=seconds + 30, kill_after=seconds)
        return GodotResult(
            ok=result.ok,
            transport="cli",
            message="Headless run finished." if result.ok else "Headless run reported errors.",
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def run_windowed(self, *, seconds: float = 0.0, extra_args: Sequence[str] = ()) -> subprocess.Popen:
        """Launch visibly so the Tester can use OS-level screen capture.

        Returns the process so the caller can wait for the window to appear, capture, then
        terminate it. Windowed mode is only used when the user explicitly wants to watch.
        """
        if not self.cli_available:
            raise GodotError("No Godot executable configured.")
        args = [self.executable, "--path", str(self.project_path), *extra_args]
        log.info("Launching Godot windowed: %s", " ".join(args))
        return subprocess.Popen(args, cwd=str(self.project_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def export_web(self, out_dir: Path, *, preset_name: str = "Web") -> GodotResult:
        """Produce the HTML5/WASM build used by the Live Preview iframe (spec D.4).

        Export templates must be installed by the user; when they are missing Godot's own
        message is surfaced verbatim instead of a generic failure, because that message tells
        the user exactly what to install.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "index.html"
        args = [
            "--headless",
            "--path",
            str(self.project_path),
            "--export-debug",
            preset_name,
            str(target),
        ]
        result = self.run_cli(args, timeout=EXPORT_TIMEOUT_S)
        combined = (result.stdout + result.stderr).lower()
        if "no export template" in combined or "export templates for this version" in combined:
            return GodotResult(
                ok=False,
                transport="cli",
                message=(
                    "Godot export templates are not installed for this version. In Godot, "
                    "open Editor > Manage Export Templates and install them, then retry. "
                    "The Live Preview needs a Web export."
                ),
                stdout=result.stdout,
                stderr=result.stderr,
            )
        if "unknown export preset" in combined or "no preset" in combined:
            message = (
                f'No export preset named "{preset_name}" exists in export_presets.cfg. '
                "Press 'Create export preset' for Web in the app to add one."
            )
            self.ensure_web_preset(preset_name)
            return GodotResult(ok=False, transport="cli", message=message, stdout=result.stdout, stderr=result.stderr)
        ok = result.ok and target.exists()
        return GodotResult(
            ok=ok,
            transport="cli",
            message=(
                f"Web build written to {target}" if ok else "Web export failed. See the log tail."
            ),
            stdout=result.stdout,
            stderr=result.stderr,
            data={"index": str(target)} if ok else {},
        )

    def ensure_web_preset(self, preset_name: str = "Web") -> Path:
        """Write a minimal ``export_presets.cfg`` for the Web target if none exists.

        Without this the first export always fails, and the user has to open the editor and
        configure an export preset by hand - a poor first experience for a non-technical user.
        """
        preset_file = (self.project_path or Path(".")) / "export_presets.cfg"
        if preset_file.exists() and f'name="{preset_name}"' in preset_file.read_text(encoding="utf-8", errors="replace"):
            return preset_file
        body = f'''[preset.0]

name="{preset_name}"
platform="Web"
runnable=true
advanced_options=false
dedicated_server=false
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter=""
export_path=""
encryption_include_filters=""
encryption_exclude_filters=""
encrypt_pck=false
encrypt_directory=false

[preset.0.options]

custom_template/debug=""
custom_template/release=""
variant/extensions_support=false
variant/thread_support=false
vram_texture_compression/for_desktop=true
vram_texture_compression/for_mobile=false
html/export_icon=true
html/custom_html_shell=""
html/head_include=""
html/canvas_resize_policy=2
html/focus_canvas_on_start=true
html/experimental_virtual_keyboard=false
progressive_web_app/enabled=false
'''
        preset_file.write_text(body, encoding="utf-8")
        return preset_file

    def run_cli(
        self, args: Sequence[str], *, timeout: int = 120, kill_after: float | None = None
    ) -> subprocess.CompletedProcess:
        if not self.cli_available:
            raise GodotError(
                f"Godot executable not found ({self.executable!r}). Set the path in Settings."
            )
        command = [self.executable, *args]
        try:
            process = subprocess.Popen(  # noqa: S603 - user-configured executable
                command,
                cwd=str(self.project_path or Path(".")),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise GodotError(f"Could not start Godot: {exc}") from exc
        try:
            stdout, stderr = process.communicate(timeout=kill_after or timeout)
            ok = process.returncode == 0
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            # A game that runs until the timer fires is a success, not a failure.
            ok = kill_after is not None
        return subprocess.CompletedProcess(command, 0 if ok else 1, stdout, stderr)

    def version(self) -> str:
        if not self.cli_available:
            return ""
        try:
            result = subprocess.run(  # noqa: S603
                [self.executable, "--version"], capture_output=True, text=True, timeout=20, check=False
            )
            return (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr) else ""
        except Exception:
            return ""


class MCPStdioSession:
    """Minimal JSON-RPC over stdio for MCP servers. One request per line, matching the spec."""

    def __init__(self, command: Sequence[str]) -> None:
        self.command = list(command)
        self.process: subprocess.Popen | None = None
        self._next_id = 1

    def start(self) -> bool:
        try:
            self.process = subprocess.Popen(  # noqa: S603
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env={**os.environ, "GODOT_MCP_QUIET": "1"},
            )
        except OSError as exc:
            log.info("Could not start MCP server %s: %s", self.command, exc)
            return False
        # Give the server a moment; if it died immediately, report unavailable.
        time.sleep(0.6)
        if self.process.poll() is not None:
            return False
        try:
            self._send({"jsonrpc": "2.0", "id": self._next_id, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "PulseG Studio", "version": "0.9.0"}}})
            reply = self._read(timeout=6.0)
            if not reply:
                return False
            # The spec requires the client to acknowledge initialize before any tool call.
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return True
        except Exception:
            return False

    def _send(self, payload: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise GodotError("MCP session is not running.")
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def _read(self, timeout: float = 30.0) -> dict[str, Any] | None:
        if not self.process or not self.process.stdout:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.process.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 60.0) -> dict[str, Any]:
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}})
        reply = self._read(timeout) or {}
        if "error" in reply:
            raise GodotError(f"MCP error from {method}: {reply['error']}")
        result = reply.get("result") or {}
        return result if isinstance(result, dict) else {"value": result}

    def stop(self) -> None:
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:  # pragma: no cover
                    pass
            self.process = None


def detect_godot_candidates() -> list[str]:
    """Common install locations, used by the Setup Wizard's 'Find Godot' button."""
    found: list[str] = []
    which = shutil.which("godot") or shutil.which("godot4") or shutil.which("Godot")
    if which:
        found.append(which)
    home = Path.home()
    patterns = [
        "Godot*.exe",
        "Godot_v*.exe",
        "Program Files/Godot*/Godot*.exe",
        "AppData/Local/Programs/Godot*/Godot*.exe",
        "Desktop/Godot*.exe",
        "Downloads/Godot*.exe",
        "Applications/Godot.app/Contents/MacOS/Godot",
        "Applications/Godot*.app/Contents/MacOS/Godot",
    ]
    for pattern in patterns:
        for path in home.glob(pattern):
            text = str(path)
            if text not in found:
                found.append(text)
    return found[:12]
