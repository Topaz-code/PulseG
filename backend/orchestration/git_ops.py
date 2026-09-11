"""Git integration.

Rules from the specification, enforced here rather than by convention:

* Git is auto-initialised per project.
* A **human approval** is the only thing that produces a commit. There is deliberately no
  ``commit_agent_work`` function that skips the approval check - ``commit_task`` refuses a
  task that has no human decision, which makes an autonomous commit path impossible to
  write by accident.
* A branch per phase (``phase/3-audio``), so a bad phase can be reverted wholesale.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import Task

log = logging.getLogger(__name__)

DEFAULT_BRANCH = "main"

#: Used only when the machine has no git identity at all, and only for the single commit command
#: that needs it. A user who has configured git keeps their own name on every commit.
FALLBACK_AUTHOR_NAME = "PulseG Studio"
FALLBACK_AUTHOR_EMAIL = "studio@pulseg.local"


class GitUnavailable(RuntimeError):
    """Git is not installed or not usable on this machine."""


@dataclass
class CommitResult:
    ok: bool
    sha: str = ""
    branch: str = ""
    message: str = ""
    files: int = 0
    detail: str = ""


class GitRepo:
    """Thin subprocess wrapper around git for one project folder."""

    def __init__(self, project_path: Path) -> None:
        self.path = project_path
        self.git_dir = project_path / ".git"

    # --- availability -----------------------------------------------------------

    @staticmethod
    def git_executable() -> str | None:
        return shutil.which("git")

    @property
    def available(self) -> bool:
        return self.git_executable() is not None

    @property
    def initialised(self) -> bool:
        return self.git_dir.exists()

    def _run(self, *args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
        executable = self.git_executable()
        if not executable:
            raise GitUnavailable(
                "Git was not found on PATH. Install Git for Windows, then reopen Settings "
                "to re-check."
            )
        result = subprocess.run(  # noqa: S603 - fixed executable, no shell
            [executable, *args],
            cwd=str(self.path),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            raise GitUnavailable(
                f"git {' '.join(args)} failed ({result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[:400]}"
            )
        return result

    # --- setup ------------------------------------------------------------------

    def init(self, *, user_name: str = "", user_email: str = "") -> bool:
        """Initialise the repo and make the first commit. Idempotent."""
        if not self.available:
            raise GitUnavailable("Git is not installed.")
        if self.initialised:
            return False
        self.path.mkdir(parents=True, exist_ok=True)
        self._run("init", "-b", DEFAULT_BRANCH, check=False)
        self._run("init", check=False)  # older git without -b
        self.ensure_gitignore()
        if user_name and not self._run("config", "user.name", check=False).stdout.strip():
            self._run("config", "user.name", user_name)
        if user_email:
            self._run("config", "user.email", user_email)
        self._run("add", "-A")
        self._run(
            "commit",
            "-m",
            "chore: initialise PulseG Studio project\n\nCreated by PulseG Studio.",
            check=False,
        )
        return True

    def ensure_gitignore(self) -> None:
        """Project-level ignores. ``web_build/`` is regenerated; keep the repo readable."""
        path = self.path / ".gitignore"
        wanted = [
            "# PulseG Studio project ignores",
            "web_build/",
            "godot_project/.godot/",
            "*.import",
            "*.tmp",
            ".DS_Store",
            "Thumbs.db",
        ]
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        missing = [line for line in wanted if line and line not in existing.splitlines()]
        if missing:
            path.write_text(existing.rstrip() + "\n" + "\n".join(missing) + "\n", encoding="utf-8")

    def identity(self) -> dict[str, str]:
        if not self.available:
            return {"name": "", "email": "", "available": "false"}
        name = self._run("config", "user.name", check=False).stdout.strip()
        email = self._run("config", "user.email", check=False).stdout.strip()
        return {"name": name, "email": email, "available": "true"}

    def set_identity(self, name: str, email: str) -> None:
        self._run("config", "user.name", name)
        self._run("config", "user.email", email)

    # --- branches ---------------------------------------------------------------

    def current_branch(self) -> str:
        if not self.initialised:
            return ""
        return self._run("rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()

    def ensure_phase_branch(self, phase: int, label: str = "") -> str:
        """Switch to (or create) ``phase/<n>-<slug>``. Returns the branch name."""
        slug = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")[:30]
        branch = f"phase/{phase}" + (f"-{slug}" if slug else "")
        if self.current_branch() == branch:
            return branch
        existing = self._run("branch", "--list", branch, check=False).stdout.strip()
        if existing:
            self._run("checkout", branch)
        else:
            self._run("checkout", "-b", branch, check=False)
        return branch

    # --- identity ---------------------------------------------------------------

    def author_identity(self) -> tuple[str, str]:
        """The name and email a commit will carry, or empty strings when git has none.

        A machine with no global git identity is the normal case for someone who has never used
        git, and git responds by refusing to commit. That used to mean an approval recorded
        "not committed" - the one guarantee this product makes (approval is the commit) quietly
        broken by a missing config value. So the identity is looked up here, and when there is
        none the commit is made with an explicit fallback rather than not made at all.
        """
        name = self._run("config", "user.name", check=False).stdout.strip()
        email = self._run("config", "user.email", check=False).stdout.strip()
        return name, email

    def _identity_args(self) -> list[str]:
        """`-c user.name=...` arguments for a commit, only when git has no identity of its own.

        Scoped to the single command on purpose: this never writes to the user's local or global
        configuration, so the studio cannot change how their other repositories behave.
        """
        name, email = self.author_identity()
        if name and email:
            return []
        return [
            "-c",
            f"user.name={name or FALLBACK_AUTHOR_NAME}",
            "-c",
            f"user.email={email or FALLBACK_AUTHOR_EMAIL}",
        ]

    # --- committing -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        if not self.initialised:
            return {"initialised": False, "dirty": False, "files": []}
        porcelain = self._run("status", "--porcelain", check=False).stdout
        entries = [line.strip() for line in porcelain.splitlines() if line.strip()]
        return {
            "initialised": True,
            "dirty": bool(entries),
            "files": entries[:200],
            "branch": self.current_branch(),
        }

    def commit_task(self, task: Task, *, phase_label: str = "") -> CommitResult:
        """Commit an approved task. Refuses anything without a human decision."""
        if task.human_decision is None:
            return CommitResult(
                ok=False,
                message="Refused: this task has no human decision. Only the human can approve work.",
            )
        if not self.initialised:
            try:
                self.init()
            except GitUnavailable as exc:
                return CommitResult(ok=False, message=str(exc))

        branch = self.current_branch() or DEFAULT_BRANCH
        if phase_label:
            branch = self.ensure_phase_branch(task.phase, phase_label)

        decision = task.human_decision.value
        files = task.artifacts or ["(no file artifacts recorded)"]
        summary = (task.title or task.instruction or task.task_id)[:72]
        body_lines = [
            f"Task: {task.task_id}",
            f"Agent: {task.assigned_to}",
            f"Provider: {task.provider_used or 'unknown'}/{task.model_used or 'unknown'}",
            f"Auditor: {task.auditor_verdict.verdict if task.auditor_verdict else 'n/a'}"
            f" (score {task.auditor_verdict.score if task.auditor_verdict else 'n/a'}/10)",
            f"Human decision: {decision}",
        ]
        if task.human_note:
            body_lines.append(f"Human note: {task.human_note}")
        body_lines.append("")
        body_lines.append("Files:")
        body_lines.extend(f"  {item}" for item in files[:40])
        if len(files) > 40:
            body_lines.append(f"  ... and {len(files) - 40} more")

        message = f"{task.assigned_to}: {summary}\n\n" + "\n".join(body_lines)
        self._run("add", "-A")
        result = self._run(*self._identity_args(), "commit", "-m", message, check=False)
        if result.returncode != 0:
            detail = (result.stdout + result.stderr).strip()
            if "nothing to commit" in detail.lower():
                return CommitResult(
                    ok=True,
                    sha=self._run("rev-parse", "HEAD", check=False).stdout.strip(),
                    branch=branch,
                    message="Nothing to commit - the approved files already match the last commit.",
                    files=len(files),
                )
            return CommitResult(ok=False, message="Commit failed", detail=detail[:500])
        sha = self._run("rev-parse", "HEAD", check=False).stdout.strip()
        return CommitResult(ok=True, sha=sha, branch=branch, message="Committed", files=len(files))

    def log(self, limit: int = 50) -> list[dict[str, str]]:
        if not self.initialised:
            return []
        fmt = "%H%x1f%an%x1f%ad%x1f%s"
        raw = self._run(
            "log", f"-{limit}", f"--pretty=format:{fmt}", "--date=iso-strict", check=False
        ).stdout
        commits: list[dict[str, str]] = []
        for line in raw.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append(
                    {
                        "sha": parts[0],
                        # The short form is what every git UI shows and what the dashboard prints. It
                        # is derived here so no client has to know how long git makes an abbreviation.
                        "short": parts[0][:7],
                        "author": parts[1],
                        "date": parts[2],
                        "subject": parts[3],
                    }
                )
        return commits

    def show(self, sha: str, path: str = "") -> str:
        args = ["show", f"{sha}:{path}"] if path else ["show", sha]
        return self._run(*args, check=False).stdout

    def diff(self, first: str, second: str, path: str = "") -> str:
        args = ["diff", first, second] + ([path] if path else [])
        return self._run(*args, check=False).stdout

    def file_history(self, path: str, limit: int = 20) -> list[dict[str, str]]:
        raw = self._run(
            "log",
            f"-{limit}",
            "--pretty=format:%H%x1f%ad%x1f%s",
            "--date=iso-strict",
            "--",
            path,
            check=False,
        ).stdout
        history: list[dict[str, str]] = []
        for line in raw.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                history.append({"sha": parts[0], "date": parts[1], "subject": parts[2], "path": path})
        return history

    def revert_commit(self, sha: str) -> CommitResult:
        result = self._run("revert", "--no-edit", sha, check=False)
        if result.returncode != 0:
            return CommitResult(ok=False, message="Revert failed", detail=(result.stderr or "")[:400])
        return CommitResult(ok=True, sha=sha, message=f"Reverted {sha[:8]}")


def git_available() -> bool:
    return GitRepo.git_executable() is not None


def detect_identity() -> dict[str, str]:
    """Read the machine's global git identity for the Setup Wizard."""
    executable = GitRepo.git_executable()
    if not executable:
        return {"available": "false", "name": "", "email": ""}
    def read(key: str) -> str:
        result = subprocess.run(  # noqa: S603
            [executable, "config", "--global", key],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip()

    return {"available": "true", "name": read("user.name"), "email": read("user.email")}


def set_global_identity(name: str, email: str) -> tuple[bool, str]:
    executable = GitRepo.git_executable()
    if not executable:
        return False, "Git is not installed, so the identity was not saved."
    for key, value in (("user.name", name), ("user.email", email)):
        if not value:
            continue
        result = subprocess.run(  # noqa: S603
            [executable, "config", "--global", key, value],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout).strip()[:300]
    return True, "Git identity saved."
