"""Audio Curator agent: finds CC0/CC-BY audio, prepares it for the engine.

Curation, not generation (the spec renamed this agent deliberately). The licence rules are
enforced in code: a non-commercial or share-alike track is dropped before preparation, and
every CC-BY asset gets its attribution line written to ``assets/audio/CREDITS.md`` in the
same step that downloads it.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path

from ..core.models import Task
from ..mcp import ffmpeg_mcp
from ..orchestration import memory, projects
from .base import AgentContext, AgentRunResult, apply_skill_checks, note_agent_state, run_text_agent

log = logging.getLogger(__name__)

AGENT_ID = "audio_curator"
MAX_PICKS_PER_TASK = 6
ALLOWED_LICENCES = {"CC0", "CC-BY"}


def _credits_path(ctx: AgentContext) -> Path:
    return ctx.project_path / "assets" / "audio" / "CREDITS.md"


def append_credits(ctx: AgentContext, lines: list[str]) -> None:
    path = _credits_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Audio credits\n\n"
    if "- " not in existing:
        existing = existing.rstrip() + "\n\n"
    added = [line for line in lines if line.strip() and line not in existing]
    if added:
        path.write_text(existing.rstrip() + "\n" + "\n".join(added) + "\n", encoding="utf-8")


def search_audio(ctx: AgentContext, query: str, *, kind: str = "sfx", limit: int = 8) -> list[dict]:
    """Search Freesound (CC0 first, then CC-BY), falling back to the local Kenney packs."""
    router = ctx.router_or_default()
    hits: list[dict] = []
    for licence in ("cc0", "cc-by"):
        result = router.audio_search(ctx.agent, query, limit=limit, licence=licence, task=ctx.task)
        if result.ok:
            for hit in result.response or []:
                if hit.licence in ALLOWED_LICENCES:
                    hits.append(
                        {
                            "title": hit.title,
                            "url": hit.url,
                            "preview_url": hit.preview_url,
                            "licence": hit.licence,
                            "author": hit.author,
                            "duration_s": hit.duration_s,
                            "source": hit.source,
                        }
                    )
        if hits:
            break
    return hits[:limit]


def _prepare(ctx: AgentContext, source_path: str, target_relative: str, *, kind: str, loop: bool) -> dict:
    """Run the FFmpeg chain, or copy the file unchanged when FFmpeg is missing."""
    target = ctx.project_path / target_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_mcp.available():
        shutil.copy2(source_path, target)
        return {
            "path": target_relative,
            "steps": ["copied unchanged - FFmpeg is not installed"],
            "note": ffmpeg_mcp.describe()["install_hint"],
        }
    report = ffmpeg_mcp.convert_for_game(source_path, target, kind=kind, loop=loop)
    report["path"] = target_relative
    return report


def run(ctx: AgentContext) -> AgentRunResult:
    run_result = run_text_agent(
        ctx,
        extra_instruction=(
            "Your `picked` entries will be downloaded and processed by FFmpeg "
            "(trim, loop, -14 LUFS, .ogg). Only CC0 and CC-BY licences are accepted; anything "
            "else is rejected in code before download. If a track needs attribution, put the "
            "exact credit line in `credits_lines`."
        ),
        require_json=True,
    )
    if run_result.paused:
        note_agent_state(ctx, run_result)
        return run_result

    payload = run_result.payload or {}
    picks = payload.get("picked") or []
    if not isinstance(picks, list):
        picks = []

    # If the model proposed nothing usable, search directly from the task instruction.
    if not picks:
        query = _query_from_task(ctx)
        candidates = search_audio(ctx, query, kind="sfx" if "sfx" in query.lower() else "bgm")
        picks = [
            {
                "path": f"assets/audio/{'sfx' if 'sfx' in query.lower() else 'bgm'}/{_slug(hit['title'])}.ogg",
                "source": hit["source"],
                "source_url": hit["preview_url"] or hit["url"],
                "author": hit["author"],
                "licence": hit["licence"],
                "loop": "bgm" in query.lower(),
                "query": query,
            }
            for hit in candidates[:MAX_PICKS_PER_TASK]
        ]

    credits: list[str] = list(payload.get("credits_lines") or [])
    written: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pulseg-audio-") as tmp:
        for pick in picks[:MAX_PICKS_PER_TASK]:
            if not isinstance(pick, dict):
                continue
            licence = str(pick.get("licence", "")).upper().replace("CC BY", "CC-BY")
            if licence not in ALLOWED_LICENCES:
                run_result.problems.append(
                    f"Rejected {pick.get('source_url') or pick.get('path')}: licence "
                    f"'{pick.get('licence')}' is not CC0 or CC-BY. Non-commercial and "
                    "share-alike audio cannot ship with the game."
                )
                continue
            source_url = str(pick.get("source_url") or "")
            local_source = str(pick.get("local_path") or "")
            if not local_source and source_url.startswith(("http://", "https://")):
                downloaded = _download(source_url, Path(tmp))
                local_source = str(downloaded) if downloaded else ""
                if not local_source:
                    # A curated hit with no preview URL needs the real Freesound API flow.
                    run_result.problems.append(
                        f"Could not fetch {source_url}. Freesound previews are usually "
                        "available; if this repeats, download the file manually into "
                        "assets/audio/ and it will be picked up."
                    )
                    continue
            if not local_source or not Path(local_source).exists():
                run_result.problems.append(f"No local file for {source_url or pick.get('path')}")
                continue
            target_relative = str(pick.get("path") or f"assets/audio/{_slug(pick.get('title', 'audio'))}.ogg")
            if not target_relative.endswith(".ogg"):
                target_relative += ".ogg"
            kind = "bgm" if "/bgm/" in target_relative or pick.get("loop") else "sfx"
            report = _prepare(ctx, local_source, target_relative, kind=kind, loop=bool(pick.get("loop")))
            written.append(report["path"])
            run_result.notes.append(f"{report['path']}: " + ", ".join(report.get("steps", [])))
            if licence == "CC-BY":
                credits.append(
                    f"- {report['path']} - {pick.get('author', 'unknown')} - CC-BY - {source_url}"
                )

    if written:
        append_credits(ctx, credits)
        run_result.artifacts.extend(written)
        run_result.artifacts.append("assets/audio/CREDITS.md")
        memory.append_progress(
            ctx.project_path,
            f"curated {len(written)} audio file(s): {', '.join(written[:4])}",
            agent=AGENT_ID,
            task_id=ctx.task_id,
            status="NOTE",
        )

    for request in (payload.get("asset_requests") or [])[:4]:
        if isinstance(request, dict):
            projects.create_asset_request(
                {"path": str(ctx.project_path), "project_id": ctx.project_id},
                name=str(request.get("name", "audio"))[:80],
                kind=str(request.get("kind", "sfx")),
                description=str(request.get("description", ""))[:600],
                requested_by=AGENT_ID,
                needed_by_task=ctx.task_id,
            )

    if not written:
        run_result.ok = False
        run_result.error = (
            "No audio was curated. Check Freesound is reachable and the query matches "
            "something; the local Kenney fallback also needs a configured cache folder."
        )
    apply_skill_checks(ctx, run_result)
    note_agent_state(ctx, run_result)
    return run_result


def _download(url: str, tmp: Path) -> Path | None:
    try:
        import httpx

        response = httpx.get(url, timeout=60, follow_redirects=True)
        if response.status_code != 200 or not response.content:
            return None
        suffix = Path(url.split("?")[0]).suffix or ".mp3"
        target = tmp / f"download_{abs(hash(url)) % 100000}{suffix}"
        target.write_bytes(response.content)
        return target
    except Exception as exc:
        log.info("Audio download failed for %s: %s", url, exc)
        return None


def _query_from_task(ctx: AgentContext) -> str:
    text = (ctx.task.instruction if ctx.task else "") or "ui click"
    if ctx.task and ctx.task.title:
        text = ctx.task.title + " " + text
    return re.sub(r"\s+", " ", text)[:120]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "audio").lower()).strip("_")[:40]


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID
