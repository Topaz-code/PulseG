"""Image Generator agent: sprites, tiles, UI and backgrounds.

Three behaviours that make generated art usable in a real project:

* **Reuse first.** The existing asset library is injected into the prompt, and a payload that
  proposes generating something that already exists is bounced back as a reuse decision.
* **Style lock.** One style string per project, prepended verbatim to every prompt, plus
  image-to-image from user reference art when it exists (spec E.3).
* **Request, don't fake.** Anything the generators cannot produce well (a hand-drawn portrait
  in an inconsistent style, say) becomes a visible asset request card rather than a bad asset.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..core.models import Task
from ..orchestration import memory, projects
from .base import AgentContext, AgentRunResult, apply_skill_checks, note_agent_state, run_text_agent

log = logging.getLogger(__name__)

AGENT_ID = "image_generator"
MAX_IMAGES_PER_TASK = 8


def style_lock(ctx: AgentContext) -> str:
    """Derive one style string for the project and cache it next to the assets."""
    style_file = ctx.project_path / "assets" / "STYLE.md"
    if style_file.exists():
        text = style_file.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text.splitlines()[0][:400]
    gdd = memory.gdd_summary(ctx.project_path, max_lines=40)
    art = ""
    match = re.search(r"art direction[:\s]*(.+)", gdd, re.IGNORECASE)
    if match:
        art = match.group(1).strip()[:200]
    style = (
        art
        or "clean 2D pixel art, 32x32 base tile, limited palette, hard edges, consistent light "
        "from the top-left, no text in image"
    )
    style_file.parent.mkdir(parents=True, exist_ok=True)
    style_file.write_text(style + "\n", encoding="utf-8")
    return style


def _reference_images(ctx: AgentContext) -> list[str]:
    references = ctx.project_path / "assets" / "references"
    if not references.exists():
        return []
    return [str(path) for path in sorted(references.glob("*")) if path.suffix.lower() in (".png", ".jpg", ".jpeg")][:4]


def _asset_block(ctx: AgentContext) -> str:
    library = projects.asset_library({"path": str(ctx.project_path), "project_id": ctx.project_id})
    if not library:
        return "ASSET LIBRARY: empty - nothing to reuse yet."
    lines = ["ASSET LIBRARY (check this before generating; reuse beats regeneration):"]
    for entry in library[:40]:
        lines.append(f"- {entry.get('path')} ({entry.get('kind')}, {entry.get('size_bytes', 0)} bytes)")
    return "\n".join(lines)


def run(ctx: AgentContext) -> AgentRunResult:
    style = style_lock(ctx)
    library_block = _asset_block(ctx)
    references = _reference_images(ctx)
    reference_note = (
        "REFERENCE ART available for image-to-image: " + ", ".join(Path(path).name for path in references)
        if references
        else "REFERENCE ART: none supplied. Use the style string above and keep it identical "
        "across every asset in this task."
    )
    run_result = run_text_agent(
        ctx,
        extra_instruction=(
            f"STYLE LOCK (prepend this verbatim to every generation prompt):\n{style}\n\n"
            f"{library_block}\n\n{reference_note}"
        ),
        require_json=True,
    )
    if run_result.paused:
        note_agent_state(ctx, run_result)
        return run_result

    payload = run_result.payload or {}
    generated = _generate_images(ctx, payload, style, references[0] if references else "")
    run_result.artifacts.extend(generated["written"])
    run_result.problems.extend(generated["problems"])

    # Asset requests: anything the agent says it cannot produce becomes a visible card.
    for request in (payload.get("asset_requests") or [])[:6]:
        if not isinstance(request, dict):
            continue
        projects.create_asset_request(
            {"path": str(ctx.project_path), "project_id": ctx.project_id},
            name=str(request.get("name", "asset"))[:80],
            kind=str(request.get("kind", "sprite")),
            description=str(request.get("description", ""))[:600],
            requested_by=AGENT_ID,
            needed_by_task=ctx.task_id,
        )
        run_result.problems.append(
            f"Asset request raised: {request.get('name')} ({request.get('kind')}) - visible in "
            "the dashboard for you to fulfil or have another agent try."
        )

    apply_skill_checks(ctx, run_result)
    note_agent_state(ctx, run_result)
    return run_result


def _generate_images(
    ctx: AgentContext, payload: dict, style: str, reference: str
) -> dict[str, list[str]]:
    router = ctx.router_or_default()
    written: list[str] = []
    problems: list[str] = []
    generations = payload.get("generations") or []
    if not isinstance(generations, list):
        problems.append("`generations` was not a list, so nothing was generated.")
        return {"written": written, "problems": problems}

    for item in generations[:MAX_IMAGES_PER_TASK]:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("path") or "").strip()
        if not relative:
            problems.append("A generation had no output path and was skipped.")
            continue
        if not relative.startswith("assets/"):
            relative = "assets/sprites/" + relative.lstrip("/")
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            problems.append(f"{relative}: generation had no prompt, skipped.")
            continue
        # Style lock is enforced in code, not just requested in the prompt.
        if style.split(",")[0].lower() not in prompt.lower():
            prompt = f"{style}. {prompt}"
        target = ctx.project_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        img2img = item.get("img2img") or {}
        reference_path = reference
        if isinstance(img2img, dict) and img2img.get("reference_path"):
            candidate = ctx.project_path / str(img2img["reference_path"])
            if candidate.exists():
                reference_path = str(candidate)

        result = router.image(
            ctx.agent,
            prompt,
            str(target),
            width=int(item.get("width") or 512),
            height=int(item.get("height") or 512),
            negative_prompt=str(item.get("negative_prompt") or "text, watermark, blurry, extra limbs"),
            reference_image=reference_path or "",
            denoise=float(img2img.get("denoise", 0.55)) if isinstance(img2img, dict) else 0.55,
            task=ctx.task,
        )
        if result.ok:
            written.append(relative)
            if getattr(result.response, "watermarked", False):
                problems.append(
                    f"{relative} was generated on Pollinations' anonymous tier and may be "
                    "watermarked. Add a Pollinations key or a Stable Horde account key to "
                    "remove it."
                )
        else:
            problems.append(
                f"{relative}: every image provider failed ({result.exhausted}). "
                "The asset was not created."
            )
    if not generations:
        problems.append(
            "The agent proposed no generations. If the asset already existed, say so in "
            "`reuse`; otherwise this task produced nothing."
        )
    return {"written": written, "problems": problems}


def should_run(task: Task) -> bool:
    return task.assigned_to == AGENT_ID
