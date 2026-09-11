"""Freeze the FastAPI sidecar into one executable for the desktop app.

    python scripts/build_backend.py

Run on the machine that is building the installer (Windows for a Windows release). The output is
``src-tauri/binaries/pulseg-backend-<target-triple>.exe`` on Windows and the plain name elsewhere,
which is exactly where ``tauri build`` looks for an external binary.

Why freeze it at all: the app promises a non-technical user one installer and no prerequisites.
A Python runtime that the user has to install - and keep at 3.11 - would break that promise the
first time Windows updates. One executable also means the shell can start the sidecar with a
single command and kill it with a single handle, which is what keeps "close the window, stop the
work" true.

Godot is deliberately *not* bundled. It is a large third-party download, it updates on its own
schedule, and the user may already have it; the Setup Wizard finds the one they have.
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "src-tauri" / "binaries"
ENTRYPOINT = REPO_ROOT / "backend" / "__main__.py"

#: Tauri's external-binary naming: `<name>-<target-triple>[.exe]`.
TRIPLES = {
    ("Windows", "AMD64"): "x86_64-pc-windows-msvc",
    ("Windows", "ARM64"): "aarch64-pc-windows-msvc",
    ("Darwin", "arm64"): "aarch64-apple-darwin",
    ("Darwin", "x86_64"): "x86_64-apple-darwin",
    ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
}


def target_triple() -> str:
    machine = platform.machine()
    key = (platform.system(), machine)
    if key in TRIPLES:
        return TRIPLES[key]
    # Windows reports AMD64, some Python builds report x86_64; try the alias before giving up.
    alias = {"x86_64": "AMD64", "aarch64": "ARM64", "arm64": "ARM64"}
    key = (platform.system(), alias.get(machine, machine))
    if key in TRIPLES:
        return TRIPLES[key]
    raise SystemExit(
        f"Unknown platform {platform.system()}/{machine}. Add it to TRIPLES in scripts/build_backend.py."
    )


def include_extra() -> list[str]:
    """Data the frozen backend still needs at runtime."""
    extras = [
        # FastAPI, Pydantic and uvicorn load some modules by name at runtime, so PyInstaller's
        # static analysis misses them.
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.loops.asyncio",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.http.h11_impl",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.protocols.websockets.websockets_impl",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=uvicorn.lifespan.off",
        "--collect-submodules=backend",
        "--collect-submodules=encodings",
    ]
    theme = REPO_ROOT / "backend" / "core" / "theme.json"
    if theme.exists():
        extras.append(f"--add-data={theme}{';' if platform.system() == 'Windows' else ':'}backend/core")
    return extras


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="pulseg-backend", help="base name of the executable")
    parser.add_argument("--onedir", action="store_true", help="build a folder instead of one file")
    args = parser.parse_args()

    try:
        # Imported for its side effect: PyInstaller only becomes importable once installed, and
        # that is the check we want, not a version comparison.
        import PyInstaller  # noqa: F401
        _ = PyInstaller.__version__
    except ImportError:
        raise SystemExit(
            "PyInstaller is not installed in this environment. Run:\n"
            "    python -m pip install -r backend/requirements.txt"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    triple = target_triple()
    suffix = ".exe" if platform.system() == "Windows" else ""
    work = REPO_ROOT / "backend" / "build"

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        args.name,
        "--console",  # the sidecar logs to stdout; the shell captures it for diagnostics
        "--onedir" if args.onedir else "--onefile",
        "--distpath",
        str(work / "dist"),
        "--workpath",
        str(work / "work"),
        "--specpath",
        str(work),
    ]
    command += include_extra()
    command.append(str(ENTRYPOINT))

    print("Building the sidecar:")
    print("  " + " ".join(command))
    result = subprocess.run(command, cwd=REPO_ROOT)
    if result.returncode != 0:
        return result.returncode

    built = work / "dist" / f"{args.name}{suffix}"
    if not built.exists():
        print(f"PyInstaller {PyInstaller.__version__} finished but {built} is missing.", file=sys.stderr)
        return 1

    target = OUTPUT_DIR / f"{args.name}-{triple}{suffix}"
    shutil.copy2(built, target)
    print(f"\nWrote {target}")
    print("The desktop build picks this up automatically: it is the executable the shell starts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
