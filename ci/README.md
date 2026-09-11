# CI workflows

These two workflow files are kept here instead of in `.github/workflows/` because GitHub refuses
to let an app token create files under `.github/workflows/` without the `workflows` permission, and
the automation that built this repository was not granted that permission. Moving them into a
normal directory keeps them in version control, reviewed and diffable, rather than stranded on one
machine.

To enable them:

```bash
python scripts/install_ci_workflows.py
git add .github/workflows && git commit -m "Enable CI" && git push
```

with a token that has the `workflows` permission. After that they behave like any other workflow.

| File | What it does |
| --- | --- |
| `checks.yml` | pyflakes, the Python suite, the API-document drift check, the TypeScript build and the D.7 design audit |
| `windows-installer.yml` | Builds PulseGStudio-Setup.exe (NSIS + MSI) on a Windows runner, after starting the frozen sidecar and waiting for `/api/health` |

`windows-installer.yml` is the workflow that satisfies the "installer builds and launches on
Windows" acceptance criterion. It has never run, because this environment has no Windows runner and
no Rust toolchain; see `project-log/UNCERTAIN_CODE.md`.
