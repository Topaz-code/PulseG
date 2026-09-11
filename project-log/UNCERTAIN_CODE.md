# Uncertain code and unverified behaviour

Anything suspected rather than proven goes here. Each entry says what is uncertain, why it
matters, and what would settle it. Silence about a doubt is the failure mode this file exists
to prevent.

Last updated: 2026-09-11

## 1. The installer cannot be built in this sandbox

**What is uncertain.** Whether `npm run tauri build` produces a working `PulseGStudio-Setup.exe`
on a Windows machine.

**Why.** This sandbox is Debian 12 with no Rust toolchain. `rustup` (`sh.rustup.rs`,
`static.rust-lang.org`) and the Debian mirrors return no response (curl exit 000), so cargo
cannot be installed here, and the Tauri build cannot be run. `webkit2gtk-4.1` and `pkg-config`
are also absent, so even a Linux bundle cannot be produced.

**What is in place instead.** The shell is written in full - `src-tauri/src/main.rs` (window,
sidecar start/stop, single instance, `open_path`, `backend_status`), `tauri.conf.json` (NSIS + MSI,
external binary, icon set), `capabilities/default.json` - plus three build pieces: the icon
generator (`scripts/make_icons.py`, which has been run), the sidecar freeze
(`scripts/build_backend.py`, written and pyflakes-clean), and the Windows CI workflow, which
starts the frozen binary and waits for `/api/health` before it packages anything. So the installer
is a build away for anyone with the toolchain, but it has not been built or launched here.

Unverified as a direct result: the frozen sidecar's first start on Windows, the shell's ability to
kill it on every exit path, and the Windows toast path (`winotify`, which only installs on win32).
The React app is validated by `tsc` and Vite, and the Python by pytest.

**What would settle it.** Running the CI workflow on a Windows runner, or `npm run tauri build`
on a Windows machine.

**Honest consequence.** Acceptance criterion "the installer builds and launches on Windows" is
**not yet demonstrated**. It is not claimed as done anywhere.

## 2. Provider adapters are verified against recorded shapes, not live keys

**What is uncertain.** Whether each adapter's request/response handling matches the live API
today, in particular for providers whose payloads were reconstructed from documentation.

**Why.** No provider keys exist in this environment, and the free tiers require accounts.

**What is in place instead.** `test_provider_router.py` drives Groq, Mistral, OpenRouter and
the demo provider through `respx` mocks that follow the documented shapes, including 429 with
`retry-after`, 401, 5xx and OpenAI-compatible JSON. The Setup Wizard's "Test Connection" is a
real ping and is the intended way for the user to confirm their own keys.

**What would settle it.** One live call per provider with a real key; the
`/api/providers/test` endpoint reports Valid, Invalid or Rate-limited for each.

## 3. The Godot MCP stdio transport is unexercised

**What is uncertain.** Whether `npx -y @coding-solo/godot-mcp` starts, completes the
initialise handshake and answers `tools/call` as expected.

**Why.** Godot is not installed in this sandbox and the npm package cannot be assumed to run
headless here.

**What is in place instead.** `mcp/godot_mcp_client.py` implements the JSON-RPC session and
falls back to the direct Godot CLI (`--headless --check-only --script`,
`--headless --path`, `--export-debug`) whenever MCP is unavailable. The CLI path is the one the
Tester depends on, and it is the path the Setup Wizard's Godot picker enables.

**What would settle it.** A machine with Godot 4.x installed: run `validate_scripts` and
`export_web` both ways and compare.

## 4. Windows notification delivery is untested on Windows

**What is uncertain.** Whether `winotify` toasts appear as expected on Windows 10/11 and
whether the app id registers correctly for the toast.

**Why.** Linux sandbox.

**What is in place instead.** The notification service prefers Tauri's native notification API
when running inside the desktop shell and falls back to `winotify`, and every notification is
also written to the in-app notification list so nothing is lost if the toast fails.

**What would settle it.** Run the packaged app on Windows and trigger a review request.

## 5. Screenshot capture on Windows

**What is uncertain.** The `EnumWindows`-based window locate path for OS-level screenshots has
not run on Windows; only the mss capture and the X11 fallback logic have been reviewed.

**Why.** Linux sandbox, no Godot window.

**What is in place instead.** Engine-side capture (write the viewport to a PNG headless) is the
primary path and is platform-independent; OS capture falls back to the primary monitor and says
so in the capture result, so a screenshot is never silently a picture of the desktop.

**What would settle it.** One windowed Tester run on Windows with a real Godot window.

## 6. Stylometry thresholds are heuristics

**What is uncertain.** How well `THRESHOLDS_BY_KIND` separates human from machine text on real
project documents. The paper publishes distances, not cutoffs.

**Why.** A single absolute cutoff would be invented. The method is comparative, so the code
says so and reports the numbers it used.

**What would settle it.** The per-project corpus comparison accumulates samples automatically
as the human types in the Planning rail; after a project has a few of each, its verdict is
evidence-based rather than threshold-based. Worth revisiting once real projects exist.

## 7. Frontend has not been audited yet

**What is uncertain.** Whether every screen passes the D.7 checklist.

**Why.** The views do not exist yet; the audit tool does (`skills/design_taste.py`).

**What would settle it.** `scripts/design_audit.py` over `src/views/`, written to
`DESIGN_TASTE_AUDIT.md`. The audit is a build step, not a review promise.
