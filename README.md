# PulseG Studio

A desktop studio of twelve AI agents that design, build, test and ship 2D Godot 4 games - with you
approving every task before it is saved.

You paste a game idea. The Planning Agent interrogates the idea until every character, mechanic and
art decision is pinned down. The build team then works through a task board, and nothing is
committed until you have read it and pressed Approve.

- **Twelve agents, one human.** A Planning Agent plus eleven specialists: Prompter, Researcher,
  Transcriptor, Documenter, Image Generator, Audio Curator, Programmer, Tester, Story Writer,
  Auditor, Task Processor.
- **Every task stops for you.** Auditor verdict, screenshots and output on one screen, with
  Approve, Reject with a note, or Override the Auditor.
- **Your files are the truth.** A project is a folder: Markdown design and story, JSON task queue,
  a real Godot project, a real git repository. The database is only an index that can be rebuilt
  from the folder at any time.
- **It never abandons a task.** Primary model, then two fallbacks, then a paused "Needs
  Intervention" with the error history and a notification - never a dead end.
- **Free to run.** It is built around free provider tiers (Groq, Google AI Studio, Mistral,
  OpenRouter, Pollinations and others) with your own keys, and it says plainly which ones need a
  card and which have daily limits.

## Install

A Windows installer is built by CI: `PulseGStudio-Setup.exe`, which puts PulseG Studio in the
Start Menu and the taskbar like any other program. See [docs/PACKAGING.md](docs/PACKAGING.md) for
building it yourself.

You also need [Godot 4.x](https://godotengine.org/download) installed - the Setup Wizard finds it,
and nothing is bundled or downloaded behind your back.

## First run

The Setup Wizard asks four things: where your projects should live, where Godot is, your name and
email for commit history, and a model key (the wizard links to the free signup pages and can test a
key before you save it). Everything after that happens in the app: describe your game, answer the
Planning Agent, then work through the board.

## How a task moves

```
PENDING -> IN_PROGRESS -> SUBMITTED -> AUDITING -> NEEDS_HUMAN_REVIEW -> APPROVED (commit)
                                                                    \-> REJECTED (requeue with note)
FAILED -> NEEDS_INTERVENTION (paused, notified, resumable)
```

The Auditor checks every submission against four skills before you see it - no_ai_slop, humanizer,
deslop and a stylometric check built from the published research (Burrows' Delta over function-word
frequencies). Its verdict arrives with your review; it never approves anything on its own.

## Repository layout

```
src/                  React + TypeScript dashboard (nine views, generated theme)
src-tauri/            Tauri v2 desktop shell: window, sidecar lifecycle, installers
backend/
  agents/             the twelve agents and the roster that defines them
  orchestration/      task bus, dispatcher, auditor, approvals, git, projects
  skills/             grillme, design_taste, no_ai_slop, humanizer, deslop, harvard_shape, adhd_filter
  providers/          the provider table and the fallback router
  mcp/                filesystem, Godot, screenshots, ffmpeg
  api/                FastAPI routers and the WebSocket event stream
scripts/              theme generation, verification, provider report, packaging
project-log/          CHANGELOG, PROCESS, TASKS_TODO, PROVIDER_VERIFICATION, UNCERTAIN_CODE, ...
docs/                 API reference and packaging guide
ci/workflows/         CI definitions (run scripts/install_ci_workflows.py to install them)
```

## Working on it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
npm install
npm run tauri:dev          # the whole app, or:
npm run backend & npm run dev   # API on :8787, dashboard on :1420
```

Checks, all of which run in CI:

```bash
python -m pyflakes backend scripts
python -m pytest backend/tests -q
python scripts/build_api_docs.py --check
python scripts/design_audit.py --check
python scripts/verify_pipeline.py       # the studio end to end, in demo mode
npm run typecheck && npm run build
npm test                                # the nine views, rendered against a fake API
```

`PULSEG_DEMO=1` runs the entire pipeline - dispatch, audit, approval, git commit - with no keys and
no network, which is how the end-to-end checks work on a machine that has never seen a model API.

## Honest status

Written and verified by execution: the backend, the agent pipeline, the human gate, the nine-view
dashboard, the design-taste audit, and the desktop shell's source and packaging.

Not yet verified: the installer has never been built or launched (this environment has no Windows
runner or Rust toolchain - see `project-log/UNCERTAIN_CODE.md`), no provider key exists here so the
live model calls have never run, and the Godot integration has not run against a real Godot
install. Every one of those is written down there rather than quietly assumed.
