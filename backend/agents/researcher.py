"""Researcher agent: Firecrawl into clean Markdown, YouTube for video sources.

Cost discipline is built in because Firecrawl's free tier is a **one-time** 1,000-credit
grant: the crawl list is capped, primary sources are crawled before blogs, and everything
lands in ``knowledge/`` with its source URL and a trust rating so the Programmer can weigh it.
"""
from __future__ import annotations

import logging
import re

from ..core.models import KnowledgeEntry, Task
from ..orchestration import memory
from .base import AgentContext, AgentRunResult, apply_skill_checks, note_agent_state, run_text_agent

log = logging.getLogger(__name__)

AGENT_ID = "researcher"
MAX_CRAWLS_PER_TASK = 4
MAX_VIDEO_RESULTS = 5

TRUSTED_DOMAINS = (
    "docs.godotengine.org",
    "github.com/godotengine",
    "godotengine.org",
    "docs.godot.org",
    "gdquest.com",
    "kidscancode.org",
)


def _trust_for(url: str) -> str:
    lowered = (url or "").lower()
    if any(domain in lowered for domain in TRUSTED_DOMAINS):
        return "high"
    if any(marker in lowered for marker in ("medium.com", "reddit.com", "stackoverflow.com", "youtube.com")):
        return "medium"
    return "low"


def _urls_from_instruction(text: str, limit: int = MAX_CRAWLS_PER_TASK) -> list[str]:
    found = re.findall(r"https?://[^\s)>\]\"']+", text or "")
    seen: list[str] = []
    for url in found:
        cleaned = url.rstrip(".,;")
        if cleaned not in seen:
            seen.append(cleaned)
    return seen[:limit]


def gather_sources(ctx: AgentContext, extra_urls: list[str] | None = None) -> dict:
    """Crawl the URLs in the instruction (or the supplied list) through the fallback chain."""
    router = ctx.router_or_default()
    instruction = ctx.task.instruction if ctx.task else ""
    urls = list(extra_urls or []) or _urls_from_instruction(instruction)
    documents: list[dict] = []
    for url in urls:
        result = router.scrape(ctx.agent, url, task=ctx.task)
        if result.ok:
            document = result.response
            documents.append(
                {
                    "url": url,
                    "title": getattr(document, "title", "") or url,
                    "markdown": getattr(document, "markdown", "")[:20000],
                    "trust": _trust_for(url),
                    "provider": result.provider_used,
                }
            )
        else:
            log.info("Could not crawl %s: %s", url, result.exhausted)
    return {"documents": documents, "requested": len(urls)}


def search_videos(ctx: AgentContext, query: str, *, limit: int = MAX_VIDEO_RESULTS) -> list[dict]:
    """Video metadata via yt-dlp (keyless) with the YouTube Data API as a fallback."""
    router = ctx.router_or_default()
    result = router.video_search(ctx.agent, query, limit=limit, task=ctx.task)
    if not result.ok:
        return []
    return [
        {
            "url": hit.url,
            "title": hit.title,
            "channel": hit.channel,
            "duration_s": hit.duration_s,
            "source": hit.source,
        }
        for hit in result.response or []
    ]


def run(ctx: AgentContext) -> AgentRunResult:
    gathered = gather_sources(ctx)
    document_block = "\n\n".join(
        f"SOURCE: {item['url']} (trust: {item['trust']}, title: {item['title']})\n{item['markdown'][:6000]}"
        for item in gathered["documents"]
    )
    extra = (
        "CRAWLED SOURCES (already fetched for you; do not ask for others)\n"
        + (document_block or "None - no URLs were reachable. Work from your own knowledge and "
                             "say clearly in `gaps` that nothing was verified against a live source.")
    )
    run_result = run_text_agent(ctx, extra_instruction=extra, require_json=True)
    if run_result.paused:
        note_agent_state(ctx, run_result)
        return run_result

    _index_findings(ctx, run_result, gathered)
    apply_skill_checks(ctx, run_result)
    note_agent_state(ctx, run_result)
    return run_result


def _index_findings(ctx: AgentContext, run_result: AgentRunResult, gathered: dict) -> None:
    """Write knowledge notes to disk and index them for the Knowledge Base view."""
    payload = run_result.payload or {}
    notes = payload.get("notes_markdown") or run_result.output
    if not notes.strip():
        return
    slug = re.sub(r"[^a-z0-9]+", "-", (ctx.task.title if ctx.task else "research").lower()).strip("-")[:48]
    relative = f"knowledge/{ctx.task_id.lower()}_{slug}.md" if ctx.task else f"knowledge/{slug}.md"
    sources = payload.get("sources") or [
        {"url": item["url"], "title": item["title"], "trust": item["trust"]} for item in gathered["documents"]
    ]
    header = "\n".join(
        f"- {source.get('url', '')} ({source.get('trust', 'medium')} trust"
        + (f", Godot {source['godot_version']}" if source.get("godot_version") else "")
        + ")"
        for source in sources
        if source.get("url")
    )
    body = f"# {ctx.task.title if ctx.task else 'Research notes'}\n\n## Sources\n{header or '- none'}\n\n{notes}"
    memory.write_knowledge_markdown(ctx.project_path, relative, body)
    run_result.artifacts.append(relative)

    entry = KnowledgeEntry(
        entry_id=f"kb_{ctx.task_id.lower()}" if ctx.task else f"kb_{slug}",
        title=(ctx.task.title if ctx.task else "Research notes")[:120],
        source_url=(sources[0].get("url", "") if sources else ""),
        source_type="web",
        agent=AGENT_ID,
        summary=_first_paragraph(notes)[:400],
        markdown_path=relative,
        tags=[tag for tag in (payload.get("actionable_for_programmer") or [])[:5] if isinstance(tag, str)],
        trust=(sources[0].get("trust", "medium") if sources else "low"),
    )
    memory.add_knowledge(ctx.project_path, entry)

    videos = payload.get("video_candidates") or []
    if videos:
        video_lines = ["", "## Video sources needing a transcript"]
        for video in videos[:8]:
            if isinstance(video, dict) and video.get("url"):
                video_lines.append(
                    f"- {video.get('title') or video['url']} - {video.get('why', '')} "
                    f"({'needs transcript' if video.get('needs_transcript', True) else 'has subtitles'})"
                )
        memory.write_knowledge_markdown(ctx.project_path, relative, body + "\n".join(video_lines))


def _first_paragraph(text: str) -> str:
    for block in (text or "").split("\n\n"):
        cleaned = block.strip().lstrip("#").strip()
        if len(cleaned) > 40:
            return cleaned
    return (text or "").strip()[:200]


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID
