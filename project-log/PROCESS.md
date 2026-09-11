# PROCESS.md — How PulseG Studio is built and how to work on it

Living document. Updated as the build proceeds. Last updated: 2026-09-11 (Phase 1).

## 1. What this project is

PulseG Studio is a **Windows desktop application** (Tauri shell + React dashboard + Python
FastAPI backend sidecar) that orchestrates 12 AI agents to design, build, test and ship 2D
Godot 4.x games. The human is the final approver of every task. It is a **reusable studio
platform**, not a one-off game generator: projects are isolated folders, global config lives
in `~/.pulsegstudio/`.

## 2. Build order (as specified)

```
Foundation -> Backend/Agent Core -> Frontend/Dashboard -> Integration -> Polish/QA
```

Each phase ends with: tests run, `/project-log` updated, `CHANGELOG.yaml` entry added.

## 3. Repository layout

```
/PulseG
  ├── src/                  React + TS + Vite + Tailwind + shadcn/ui frontend
  ├── src-tauri/            Rust Tauri v2 shell, sidecar wiring, NSIS/WiX installer config
  ├── backend/              Python 3.11 FastAPI backend (runs as a Tauri sidecar)
  │   ├── api/              HTTP + WebSocket routers (one module per dashboard concern)
  │   ├── agents/           one module per agent in the roster
  │   ├── core/             config, encrypted vault, SQLite index, event bus, quota
  │   ├── mcp/              MCP clients (Godot, gamedev, filesystem) + FFmpeg + screenshots
  │   ├── notifications/    Telegram + Windows toast
  │   ├── orchestration/    task bus, dispatcher, auditor pipeline, approvals, memory, git
  │   ├── providers/        provider adapters + fallback router
  │   ├── skills/           reusable agent skills (grillme, harvard_shape, ...)
  │   └── tests/            pytest suite
  ├── scripts/              theme generation, icon fetch, packaging helpers
  ├── project-log/          living documentation (this folder)
  └── docs/                 user-facing guides (install, first project, providers)
```

## 4. Non-negotiable architectural rules

1. **Files are the source of truth.** `task_queue.json`, `memory/*.md`, `gdd.md`, project
   folders. SQLite (`~/.pulsegstudio/index.db`) is a *cache* for dashboard queries. On
   disagreement, files win; the index is rebuilt from files (`POST /api/system/reindex`).
2. **No task is ever permanently abandoned.** Failure chain: primary -> fallback1 ->
   fallback2 -> `NEEDS_INTERVENTION` (a *pause*, not death). Full error history and
   `retry_count` are preserved, and the task resumes automatically when the human fixes a
   key, edits the fallback chain, or clicks Retry.
3. **The human approves every task.** The Auditor produces a verdict for *every* task and
   the task still lands in "Needs Your Review". There is no autonomous commit path.
   Git commits happen only on human approval.
4. **Zero polling.** All live state moves over WebSocket. The frontend never polls a list
   endpoint on a timer; it re-fetches on server-pushed events.
5. **Secrets stay local.** Provider keys live in `~/.pulsegstudio/keys.enc`
   (Fernet-encrypted, 0600). They are only ever sent to the provider that owns them. They
   are never logged, never returned by the API (masked `last4` only), and never committed.
6. **Config over code.** Adding an agent or a provider must be a config change
   (`agents.yaml`, `providers.yaml`), not a code change. Agent/provider registries are
   data-driven; code modules declare capabilities and defaults only.
7. **No emojis in the product UI.** Icons come from a single icon set (icons8
   Fluency Systems Filled style). Text labels are plain English, beginner-readable.
   (Emoji characters are also avoided in source, logs and docs produced by this project so
   the UI has one consistent visual language. Where the original specification used an
   emoji as a status marker, this build uses an icon + plain label, e.g.
   "Needs Intervention" with a warning icon.)

## 5. Environment reality of the build machine (honest notes)

The build sandbox has a **restricted network**: PyPI, npm and `api.github.com` are reachable;
Debian mirrors, `sh.rustup.rs`, `godotengine.org`, `img.icons8.com` and general web hosts are
**not** reachable from the shell. Consequences, which are tracked in
`UNCERTAIN_CODE.md` and `RESOLUTIONS.md`:

* Rust/Tauri cannot be compiled *here*; the Tauri crate, sidecar wiring, NSIS config and a
  Windows CI workflow are committed and verified by static review + schema validation, and
  the installer is produced by the GitHub Actions workflow on a Windows runner.
* Icons are authored in the Fluency-Systems-Filled idiom (solid, rounded, single-color,
  48x48 grid) rather than downloaded, because `img.icons8.com` is unreachable from this
  sandbox. `scripts/fetch_icons8.py` swaps in the official files on a machine that can
  reach icons8.com, using the same slug manifest.
* Live provider calls cannot be made without user keys; instead every provider has a
  deterministic `Test Connection` path and the test-suite exercises adapters against
  recorded/mocked transports.

## 6. Testing strategy

* `backend/tests/` — pytest. Unit tests for vault, task bus, memory compression, provider
  router fallback, auditor skill rubrics, dispatcher dependency graph, approval flow, and an
  API smoke test that boots the app with `TestClient`.
* Frontend — `tsc --noEmit` type gate + `vite build`; design-taste pass recorded in
  `DESIGN_TASTE_AUDIT.md`.
* End-to-end demo mode — `PULSEG_DEMO=1` boots a pre-seeded project (demo provider, no keys)
  so the full pipeline can be watched and screenshotted without spending quota. This is the
  path used for the screenshots in `docs/`.

## 7. Documentation rules

Update these files as part of the same change that alters behaviour:

| File | When |
|---|---|
| `CHANGELOG.yaml` | every meaningful change |
| `TASKS_TODO.md` | when work items are added/completed |
| `PROVIDER_VERIFICATION.md` | whenever a provider is verified or changed |
| `UNCERTAIN_CODE.md` | whenever something cannot be verified here |
| `RESOLUTIONS.md` | when an uncertainty/question is resolved |
| `DESIGN_TASTE_AUDIT.md` | after each UI review pass |
| `SECURITY_REVIEW_NEEDED.md` | whenever key handling or process spawning changes |
| `PROCESS.md` | when the process itself changes |
