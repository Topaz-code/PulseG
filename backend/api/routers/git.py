"""Git history: the project's real audit trail.

Every commit here was produced by a human approval, so the history view doubles as the record of
who decided what. The router is read-only apart from revert, which itself creates a commit
rather than rewriting history - a studio that rewrites history cannot answer "what did I approve
on Tuesday".
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ...orchestration import memory
from ...runtime import runtime
from ..deps import guarded, require_project
from ..schemas import GitDiffRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/git", tags=["git"])


@router.get("/log")
@guarded("read git log")
def git_log(limit: int = Query(default=60, le=500)) -> dict[str, Any]:
    context = require_project()
    repo = context.git()
    if not repo.initialised:
        return {
            "initialised": False,
            "commits": [],
            "note": "This project is not a git repository yet. Approving a task initialises it.",
        }
    return {
        "initialised": True,
        "branch": repo.current_branch(),
        "commits": repo.log(limit=limit),
        "status": repo.status(),
    }


@router.get("/show")
@guarded("show commit")
def git_show(sha: str = Query(min_length=4, max_length=60), path: str = Query(default="")) -> dict[str, Any]:
    context = require_project()
    text = context.git().show(sha, path)
    if not text.strip():
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_commit", "message": f"No commit {sha} in this project."},
        )
    return {"sha": sha, "path": path, "content": text}


@router.post("/diff")
@guarded("diff commits")
def git_diff(payload: GitDiffRequest) -> dict[str, Any]:
    context = require_project()
    return {"diff": context.git().diff(payload.first, payload.second, payload.path)}


@router.get("/file-history")
@guarded("read file history")
def file_history(path: str = Query(min_length=1), limit: int = Query(default=20, le=200)) -> dict[str, Any]:
    context = require_project()
    return {"path": path, "commits": context.git().file_history(path, limit=limit)}


@router.get("/status")
@guarded("read git status")
def git_status() -> dict[str, Any]:
    context = require_project()
    repo = context.git()
    return {
        "initialised": repo.initialised,
        "identity": repo.identity(),
        "status": repo.status() if repo.initialised else {},
        "branch": repo.current_branch() if repo.initialised else "",
    }


@router.get("/task-commits")
@guarded("map tasks to commits")
def task_commits() -> dict[str, Any]:
    """Which commit belongs to which task - the link the drawer shows on an approved task."""
    context = require_project()
    rows: list[dict[str, Any]] = []
    for task in context.bus.completed():
        if task.committed:
            rows.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "agent": task.assigned_to,
                    "sha": task.committed,
                    "decided_at": task.decided_at,
                    "override": bool(task.human_decision and task.human_decision.value == "OVERRIDDEN"),
                }
            )
    return {"commits": rows, "count": len(rows)}


@router.post("/revert")
@guarded("revert commit")
def revert(sha: str = Query(min_length=4, max_length=60)) -> dict[str, Any]:
    """Revert a commit by creating a new one. History is never rewritten."""
    context = require_project()
    result = context.git().revert_commit(sha)
    if result.ok:
        memory.append_progress(
            context.path,
            f"commit {sha[:8]} reverted by the human",
            agent="human",
            status="REVERTED",
        )
        runtime.wake()
    return {
        "ok": result.ok,
        "sha": result.sha,
        "message": result.message,
        "detail": result.detail,
    }


@router.get("/branches")
@guarded("list branches")
def branches() -> dict[str, Any]:
    """One branch per phase, plus whatever the user has made themselves."""
    context = require_project()
    repo = context.git()
    if not repo.initialised:
        return {"branches": [], "current": ""}
    result = repo._run("branch", "--format=%(refname:short)", check=False)
    names = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    return {"branches": names, "current": repo.current_branch()}
