# Tasks and TODO

Honest state of the build. "Done" means written **and executed**; anything written but not yet
run is listed as such, because a file that has never been imported is not finished work.

Last updated: 2026-09-11

## Foundation and core

| Area | State | Evidence |
| --- | --- | --- |
| Repo skeleton, package.json, requirements, .gitignore | Done | npm install 347 packages, exit 0 |
| core/paths, atomic, vault (Fernet + DPAPI), models, events, config, quota, index | Done | Imported and exercised by 49 tests |
| providers/specs.py (20 providers, substitutions, free-forever set) | Done | Import check, generated PROVIDER_VERIFICATION.md |
| providers/base, adapters (15 adapters), router (fallback chain, decline rotation) | Done | test_provider_router.py, 8 tests |
| skills: base, no_ai_slop, humanizer, deslop, harvard_shape, grillme, adhd_filter, design_taste | Done | test_skills.py, 30 tests |
| agents: 12 modules + registry + loader | Written, executed for documenter/programmer/tester on the demo provider | test_pipeline.py |
| orchestration: memory, git_ops, task_bus, projects | Done | Lifecycle test + pipeline tests |
| orchestration: auditor, dispatcher, approvals, context_engine | Done | test_pipeline.py, 11 tests |
| runtime.py: run state, dispatch loop thread, crash recovery of in-flight tasks | Done | Exercised through the API and the live smoke run |
| runtime concurrency: reentrant queue lock, one task per agent per pass | Done | test_pipeline.py file-lock and agent-busy tests; lost-update race fixed |
| mcp: filesystem, godot client, screenshots, ffmpeg | Written, not yet executed against a real Godot install | Import-clean, pyflakes clean, live smoke run |

## Backend surface

| Area | State |
| --- | --- |
| backend/main.py: FastAPI app, static web build, sidecar shutdown | Done - boots on a real socket, scripts/smoke_api.py |
| api/routers: 13 routers (system, settings, projects, tasks, agents, providers, planning, assets, knowledge, logs, git, design, preview) | Done - test_api.py, 31 tests |
| WebSocket /ws/events fed by core/events.bus, with replay + heartbeat | Done - hello + replay covered by test and smoke run |
| notifications/service.py: Telegram + Windows toast, wired to the four triggers | Done - endpoint, log and trigger tests |
| api wiring for approver endpoints (approve / reject / override / bulk) | Done - pipeline tests plus live run |
| Setup Wizard backend (first-run state, config.yaml write, Godot detection) | Done - wizard tests cover write + rejection |
| Demo mode end-to-end run through the API | Done - scripts/smoke_api.py, 24 checks |
| OpenAPI document (drives the frontend types and docs/API.md) | Done - generated and asserted in tests |

## Frontend (after the API)

| Area | State |
| --- | --- |
| Vite + Tailwind + token pipeline (chroma.js scales -> theme.json -> Tailwind) | Not started |
| Layout shell: top bar, sidebar, context panel, Planning rail, Command bar | Not started |
| 9 views (Overview, Kanban, Task detail, Agent detail, Assets, GDD/Story, Knowledge, Logs, Settings) | Not started |
| Live Preview toggle (React Flow graph <-> Godot web export iframe) | Not started |
| Design taste audit run over every view and logged | Script ready (skills/design_taste.py), audit pending |

## Desktop shell and packaging

| Area | State |
| --- | --- |
| Tauri v2 project, NSIS installer config, sidecar spawn/kill, single-instance | Not started |
| Python sidecar packaging (PyInstaller spec) | requirements ready, spec not written |
| Windows CI producing PulseGStudio-Setup.exe | Not started |
| Icon set (icons8 fluency-systems-filled style, authored locally) | Swap script planned, art not made |

## Known gaps carried in the log

- A Windows installer cannot be produced from this Linux sandbox: no Rust toolchain is
  installable here (rustup and the Debian mirrors are unreachable). See UNCERTAIN_CODE.md.
- The Godot MCP client's stdio transport has not been run against the real npm package; the
  direct CLI path is the one the tests can exercise on a machine with Godot installed.
- No live provider keys exist in this sandbox, so real model calls are unverified by
  execution. The adapters are verified against recorded HTTP shapes via respx.
