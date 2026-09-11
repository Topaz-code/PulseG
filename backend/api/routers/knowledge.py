"""Knowledge base: the Researcher and Transcriptor index.

The Knowledge screen is searchable, so this router exposes both the structured index and the
raw Markdown. Trust ratings are surfaced rather than averaged: "this came from Godot's own docs"
and "this came from a blog" are different kinds of information and the UI is allowed to say so.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ...orchestration import memory
from ...runtime import runtime
from ..deps import guarded, require_project
from ..schemas import KnowledgeSearchRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("")
@guarded("list knowledge")
def list_knowledge(
    source_type: str = Query(default=""),
    trust: str = Query(default=""),
    limit: int = Query(default=200, le=1000),
) -> dict[str, Any]:
    context = require_project()
    entries = memory.read_knowledge(context.path)
    if source_type:
        entries = [entry for entry in entries if entry.source_type == source_type]
    if trust:
        entries = [entry for entry in entries if entry.trust == trust]
    rows = [entry.model_dump(mode="json") for entry in entries[:limit]]
    return {
        "entries": rows,
        "count": len(entries),
        "by_type": _counts(entries, "source_type"),
        "by_trust": _counts(entries, "trust"),
    }


@router.post("/search")
@guarded("search knowledge")
def search(payload: KnowledgeSearchRequest) -> dict[str, Any]:
    """Search titles, summaries and tags. Deliberately simple and offline: no embeddings call."""
    context = require_project()
    needle = payload.query.lower()
    rows: list[dict[str, Any]] = []
    for entry in memory.read_knowledge(context.path):
        haystack = f"{entry.title} {entry.summary} {' '.join(entry.tags)} {entry.source_url}".lower()
        if needle in haystack:
            rows.append({**entry.model_dump(mode="json"), "matched_on": "index"})
    # Fall back to the file contents so a term that only appears in the body is still findable.
    if len(rows) < payload.limit:
        for path in sorted((context.path / "knowledge").rglob("*.md"))[:200]:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in text.lower():
                relative = str(path.relative_to(context.path))
                if any(row.get("markdown_path") == relative for row in rows):
                    continue
                rows.append(
                    {
                        "entry_id": f"file:{relative}",
                        "title": path.stem.replace("_", " "),
                        "markdown_path": relative,
                        "summary": _excerpt(text, needle),
                        "source_url": "",
                        "source_type": "file",
                        "trust": "medium",
                        "agent": "",
                        "tags": [],
                        "matched_on": "file",
                    }
                )
            if len(rows) >= payload.limit:
                break
    return {"query": payload.query, "results": rows[: payload.limit], "count": len(rows)}


@router.get("/entry")
@guarded("read knowledge entry")
def entry(path: str = Query(min_length=1)) -> dict[str, Any]:
    """One note, rendered from its Markdown file on disk."""
    context = require_project()
    from ...mcp.filesystem_mcp import read_project_file

    try:
        text = read_project_file(context.path, path)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "not_readable",
                "message": f"{exc} Only files inside the project folder can be read.",
            },
        ) from exc
    return {"path": path, "markdown": text}


@router.get("/transcripts")
@guarded("list transcripts")
def transcripts() -> dict[str, Any]:
    context = require_project()
    root = context.path / "knowledge" / "transcripts"
    rows: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            rows.append(
                {
                    "path": str(path.relative_to(context.path)),
                    "title": _front_matter(text, "Title") or path.stem,
                    "source": _front_matter(text, "Source"),
                    "method": _front_matter(text, "Method"),
                    "duration": _front_matter(text, "Duration"),
                    "bytes": len(text),
                }
            )
    return {"transcripts": rows, "count": len(rows)}


@router.get("/stats")
@guarded("read knowledge stats")
def stats() -> dict[str, Any]:
    context = require_project()
    entries = memory.read_knowledge(context.path)
    root = context.path / "knowledge"
    files = list(root.rglob("*.md")) if root.exists() else []
    return {
        "entries": len(entries),
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
        "sources": sorted({entry.source_url for entry in entries if entry.source_url})[:40],
        "untrusted": [entry.title for entry in entries if entry.trust == "low"][:20],
        "note": (
            "'low' trust means the page came from somewhere without editorial standards. The "
            "Programmer is told the trust level of everything it is given."
        ),
    }


@router.delete("/entry")
@guarded("forget knowledge entry")
def forget(path: str = Query(min_length=1)) -> dict[str, Any]:
    """Forget a note. The file is removed from the project and from the index - the one place
    deletion is correct, because a wrong fact that keeps coming back is worse than no fact."""
    context = require_project()
    from ...mcp.filesystem_mcp import resolve_in_project

    try:
        target = resolve_in_project(context.path, path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail={"error": "outside_project", "message": str(exc)}) from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail={"error": "missing", "message": f"{path} does not exist."})
    target.unlink()
    remaining = [entry for entry in memory.read_knowledge(context.path) if entry.markdown_path != path]
    from ...core.atomic import atomic_write_json

    atomic_write_json(context.path / "knowledge" / "index.json", [entry.model_dump(mode="json") for entry in remaining])
    runtime.wake()
    return {"removed": True, "remaining": len(remaining)}


def _counts(entries, attribute: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        value = str(getattr(entry, attribute, "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _excerpt(text: str, needle: str, span: int = 200) -> str:
    index = text.lower().find(needle)
    if index == -1:
        return text[:span].strip()
    start = max(0, index - span // 3)
    return ("..." if start else "") + text[start : start + span].replace("\n", " ").strip()


def _front_matter(text: str, label: str) -> str:
    for line in text.splitlines()[:14]:
        if line.lower().startswith(f"- {label.lower()}:"):
            return line.split(":", 1)[1].strip()
        if line.lower().startswith(f"# {label.lower()}:"):
            return line.split(":", 1)[1].strip()
    return ""
