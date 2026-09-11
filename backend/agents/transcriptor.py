"""Transcriptor agent: audio/video into readable text.

Order of operations is chosen to protect the user's wallet:

1. **yt-dlp subtitles** - free, keyless, instant. Used whenever a subtitle track exists.
2. **Deepgram** - only when there is no subtitle track. Deepgram's $200 is a one-time grant,
   so spending it on a video that already had captions would be careless.
3. **Groq Whisper** - the perpetual free fallback when Deepgram credit is exhausted.

FFmpeg does the extraction and the silence-aware splitting, so long videos never get cut
mid-sentence.
"""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from ..core.models import KnowledgeEntry, Task
from ..mcp import ffmpeg_mcp
from ..orchestration import memory
from .base import AgentContext, AgentRunResult, apply_skill_checks, note_agent_state, run_text_agent

log = logging.getLogger(__name__)

AGENT_ID = "transcriptor"


def resolve_video_url(task: Task | None) -> str:
    if task is None:
        return ""
    instruction = task.instruction or ""
    match = re.search(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be|vimeo\.com)[^\s)>\]\"']*", instruction)
    if match:
        return match.group(0)
    for path in task.context_files:
        if path.startswith("http"):
            return path
    return ""


def transcribe_source(ctx: AgentContext, url: str) -> dict:
    """Get the best available transcript for ``url``, cheapest path first."""
    router = ctx.router_or_default()
    attempts: list[dict] = []

    # 1. Free subtitles.
    subtitles = router.subtitles(ctx.agent, url, language="en", task=ctx.task)
    if subtitles.ok and getattr(subtitles.response, "text", "").strip():
        return {
            "method": "yt-dlp-subtitles",
            "text": subtitles.response.text,
            "segments": subtitles.response.segments,
            "duration_s": subtitles.response.duration_s,
            "attempts": attempts,
            "cost_note": "No provider credit used.",
        }
    attempts.append({"method": "yt-dlp-subtitles", "result": "no subtitle track available"})

    # 2. Download audio, then transcribe through the chain (Deepgram -> Groq Whisper).
    audio_path = ""
    with tempfile.TemporaryDirectory(prefix="pulseg-audio-") as tmp:
        try:
            import yt_dlp

            target = Path(tmp) / "audio.%(ext)s"
            options = {
                "format": "bestaudio/best",
                "outtmpl": str(target),
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 60,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
            }
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
            candidates = sorted(Path(tmp).glob("audio.*"))
            audio_path = str(candidates[0]) if candidates else ""
        except Exception as exc:
            attempts.append({"method": "download", "result": f"failed: {exc}"})
            return {"method": "none", "text": "", "attempts": attempts, "error": str(exc)}

        if not audio_path:
            return {"method": "none", "text": "", "attempts": attempts, "error": "no audio downloaded"}

        # Segment long audio at natural pauses so nothing is cut mid-sentence.
        chunks: list[str] = [audio_path]
        if ffmpeg_mcp.available():
            try:
                chunks = [str(path) for path in ffmpeg_mcp.segment_audio(audio_path, Path(tmp) / "parts")]
                attempts.append({"method": "ffmpeg-segment", "result": f"{len(chunks)} chunk(s)"})
            except Exception as exc:
                attempts.append({"method": "ffmpeg-segment", "result": f"failed: {exc}"})

        texts: list[str] = []
        segments: list[dict] = []
        duration = 0.0
        for chunk in chunks:
            result = router.transcribe(ctx.agent, chunk, task=ctx.task, language="en")
            if result.ok:
                transcript = result.response
                texts.append(getattr(transcript, "text", ""))
                segments.extend(getattr(transcript, "segments", []) or [])
                duration += getattr(transcript, "duration_s", 0.0)
                attempts.append(
                    {"method": f"transcribe:{result.provider_used}", "result": "ok", "chunk": Path(chunk).name}
                )
            else:
                attempts.append({"method": "transcribe", "result": str(result.exhausted)[:200], "chunk": Path(chunk).name})
        text = "\n".join(part for part in texts if part.strip())
        return {
            "method": "deepgram-or-whisper",
            "text": text,
            "segments": segments,
            "duration_s": duration,
            "attempts": attempts,
            "cost_note": "Only the audio path was transcribed; subtitles were unavailable.",
        }


def run(ctx: AgentContext) -> AgentRunResult:
    url = resolve_video_url(ctx.task) or ctx.extra.get("video_url", "")
    if not url:
        # Nothing to transcribe: say so precisely instead of burning a model call.
        return AgentRunResult(
            ok=False,
            agent_id=AGENT_ID,
            error=(
                "No video URL found in this task. Put the URL in the instruction (for example "
                "'transcribe https://youtu.be/...') so the Transcriptor knows what to fetch."
            ),
        )

    transcript = transcribe_source(ctx, url)
    if not transcript.get("text"):
        return AgentRunResult(
            ok=False,
            agent_id=AGENT_ID,
            error=(
                f"Could not obtain a transcript for {url}. Attempts: "
                + "; ".join(f"{item['method']}: {item['result']}" for item in transcript.get("attempts", []))
            ),
        )

    run_result = run_text_agent(
        ctx,
        extra_instruction=(
            f"RAW TRANSCRIPT from {url} (method: {transcript['method']}, "
            f"{transcript.get('duration_s', 0):.0f}s).\n"
            "Clean it according to your rules - remove filler, fix technical vocabulary, add "
            "timestamps, keep code verbatim. Do not summarise it into something shorter than "
            "the techniques it contains.\n\n"
            + transcript["text"][:40000]
        ),
        require_json=True,
    )
    if run_result.paused:
        note_agent_state(ctx, run_result)
        return run_result

    payload = run_result.payload or {}
    clean = payload.get("clean_markdown") or transcript["text"]
    slug = re.sub(r"[^a-z0-9]+", "-", (ctx.task.title if ctx.task else "transcript").lower()).strip("-")[:40]
    relative = f"knowledge/transcripts/{ctx.task_id.lower()}_{slug}.md" if ctx.task else f"knowledge/transcripts/{slug}.md"
    body = (
        f"# Transcript: {ctx.task.title if ctx.task else url}\n\n"
        f"- Source: {url}\n- Method: {transcript['method']}\n"
        f"- Duration: {transcript.get('duration_s', 0):.0f}s\n"
        f"- Cost note: {transcript.get('cost_note', '')}\n\n{clean}"
    )
    memory.write_knowledge_markdown(ctx.project_path, relative, body)
    run_result.artifacts.append(relative)
    memory.add_knowledge(
        ctx.project_path,
        KnowledgeEntry(
            entry_id=f"kb_tr_{ctx.task_id.lower()}" if ctx.task else f"kb_tr_{slug}",
            title=(ctx.task.title if ctx.task else "Transcript")[:120],
            source_url=url,
            source_type="youtube" if "youtu" in url else "web",
            agent=AGENT_ID,
            summary=(payload.get("key_points") or [clean[:300]])[0] if isinstance(payload.get("key_points"), list) else clean[:300],
            markdown_path=relative,
            trust="medium",
            tags=[str(code)[:40] for code in (payload.get("code_snippets") or [])[:5]],
        ),
    )
    apply_skill_checks(ctx, run_result)
    note_agent_state(ctx, run_result)
    return run_result


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID
