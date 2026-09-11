# Security review needed

This file tracks decisions that need a security review before the studio is handed to someone
who runs it for years. Each entry states the risk, what is already done about it, and what is
still required. Nothing here is a claim that the app is secure; it is a list of the places a
reviewer should look first.

Last updated: 2026-09-11

## Secrets

**Where secrets live.** `~/.pulsegstudio/keys.enc`, encrypted with Fernet. The key is derived
per machine: on Windows the DPAPI-protected key file is preferred, otherwise a key file with
restricted permissions is used. `PULSEG_VAULT_KEY` and `PULSEG_VAULT_PASSPHRASE` allow an
operator to supply the key from the environment instead.

**Done.** Keys are never written into `config.yaml`, `agents.yaml` or any project file. The UI
only ever shows the last four characters (`vault.describe()`), and the API returns
`masked_key` rather than the value. Project files are the source of truth for everything
*except* secrets, and no agent prompt is ever given a key: agent output is scanned for the
key patterns before it is written (see the prompt rules in `agents/roster.py`).

**Needs review.**
- Confirm the DPAPI implementation in `core/dpapi.py` on a real Windows machine.
- Decide whether the vault should auto-lock after an idle period, and whether a passphrase
  prompt on launch is worth the friction for a single-user desktop app.
- Verify that crash dumps, log files and the SQLite index never contain secret material. The
  index stores task and project metadata; it must never store prompts containing keys.

## Filesystem access

**Done.** The filesystem MCP layer resolves every path inside the project folder and refuses
traversal, absolute outsiders, `.git` and `.godot` (`resolve_in_project`). Writes have a text
suffix allowlist and a 4 MB cap. Writes outside a task's declared outputs are flagged as scope
creep in the task record rather than silently allowed.

**Needs review.**
- The `_looks_like_engine_file` allowlist should be reviewed against the full set of files a
  Godot 4 project legitimately needs.
- Symlink handling inside a project folder has not been tested on Windows.

## Generated code execution

**Risk.** The Programmer writes GDScript, and the Tester runs it. That is arbitrary code
execution by design, but it must be scoped to the project.

**Done.** Godot is invoked with `--path <project>` and a bounded runtime; script validation is
`--check-only`. No generated code is executed by the Python sidecar itself, and no shell string
is ever built from model output.

**Needs review.**
- Confirm that a malicious `export_presets.cfg` or `project.godot` cannot make Godot write
  outside the project folder during an export.
- Consider running the Tester's windowed runs with a restricted working directory on Windows.

## Network access

**Done.** Provider calls go only to the URLs in `providers/specs.py`, which are auditable in
one file. Demo mode (`PULSEG_DEMO=1`) appends an offline provider to every chain, so a
demonstration or a test run cannot reach the network even with real keys present. Research
crawls are limited to the URLs a task names.

**Needs review.**
- The Researcher's Firecrawl calls send the URL under research to a third party. The UI should
  say so on the Research screen (currently covered in the provider card, not at the point of
  use).
- Telegram notifications send task titles to Telegram's servers. Titles can contain project
  names; the notification text must never include instruction bodies. Verify in
  `notifications/service.py` once written.

## Update and supply chain

**Needs review.**
- Pin the Node and Python dependency versions used for the packaged build, and record the
  hashes of the Godot binary the wizard accepts.
- Decide how the app behaves when `npx` fetches a newer `@coding-solo/godot-mcp` than the one
  tested: the current design falls back to the CLI, which is the safer default.

## Threat model not yet written

A short threat model for a single-user desktop studio is still owed: what a curious child, a
hostile project file from the internet, and a compromised dependency could each do. It belongs
in this file before release, not after.
