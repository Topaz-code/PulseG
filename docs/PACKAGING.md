# Building the installer

PulseG Studio ships as one Windows installer. This document is what a person building a release
needs, and it is also the checklist the release itself is held to.

## What the user gets

One `PulseGStudio-Setup.exe` that installs:

| Piece | Why it is where it is |
| --- | --- |
| `pulseg-studio.exe` | The desktop shell: window, sidecar supervision, "open folder" |
| `pulseg-backend.exe` | The Python studio, frozen with PyInstaller - no Python required on the user's machine |
| Web assets | The dashboard, embedded in the shell binary by Tauri |
| Start Menu entry | "PulseG Studio", created by the NSIS installer |
| Taskbar entry | A normal Windows window, so it pins and groups like any other program |

The user installs Godot themselves (the Setup Wizard finds it), and their projects are ordinary
folders under the projects root they chose - not inside the install directory, so uninstalling the
app never touches a game.

## Building it

On a Windows machine with Rust, Node 22, Python 3.11 and NSIS available (the
`ci/workflows/windows-installer.yml` workflow does all of this on a clean runner; run
`python scripts/install_ci_workflows.py` once to put it in `.github/workflows/`):

```bat
pip install -r backend\requirements.txt
npm ci
python scripts\make_icons.py        :: writes src-tauri\icons
python scripts\build_backend.py     :: freezes the sidecar into src-tauri\binaries
npx tauri build --bundles nsis,msi
```

Artifacts land in `src-tauri\target\release\bundle\`:

- `nsis\PulseGStudio_0.9.0_x64-setup.exe` - the installer to hand to a user
- `msi\PulseGStudio_0.9.0_x64_en-US.msi` - the same app for managed deployment

## Why the backend is frozen rather than bundled as source

The promise is "one installer, no prerequisites". A Python runtime the user has to install - and
keep at the right version - would break that at the first Windows update. Freezing also gives the
shell a single process handle to start and stop, which is what keeps "close the window, the studio
stops working" true.

PyInstaller needs a few hints, all recorded in `scripts/build_backend.py`: uvicorn imports its loop
and protocol implementations by name at runtime, and `backend/core/theme.json` is data the code
reads rather than imports.

## Verifying before shipping

The Windows workflow runs the frozen sidecar and waits for `GET /api/health` *before* it packages
anything. A packaged app whose backend cannot start is worse than no app: it looks installed and
does nothing.

The checks workflow covers the rest on every push: the Python suite, `pyflakes`, the API document
drift check, the TypeScript build, and the D.7 design-taste audit with `--check` so a screen that
regresses fails the build rather than the review.

## What cannot be built in every environment

The development sandbox this project was written in has no Rust toolchain and no Windows runner,
so the `.exe` itself is produced by CI rather than locally. That is recorded in
`project-log/UNCERTAIN_CODE.md` along with what remains unverified because of it - chiefly the
first launch of the frozen sidecar on Windows and the toast notification path, which uses
`winotify` on a real Windows machine.

## Signing

The installer is unsigned. Windows shows a SmartScreen warning on first run. Signing needs a
certificate the maintainer owns; when one exists, `tauri build` picks it up from
`bundle.windows.certificateThumbprint` / `digestAlgorithm` in `src-tauri/tauri.conf.json`. This is
documented rather than silently ignored because an unsigned installer is a real user-facing
friction point, not a detail.
