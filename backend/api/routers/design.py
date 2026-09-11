"""Design taste: the D.7 checklist, and the theme tokens the frontend renders from.

The audit runs here rather than in a CI step alone, so the Logs/Settings screen can show the
current state and the Logs view can tell you *why* a screen is failing. The same code writes
``project-log/DESIGN_TASTE_AUDIT.md``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from ...core.paths import theme_file
from ...skills import design_taste
from ..deps import guarded

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/design", tags=["design"])

REPO_ROOT = Path(__file__).resolve().parents[3]


@router.get("/checklist")
def checklist() -> dict[str, Any]:
    """The eight D.7 rules, as data, so the UI and the docs cannot drift apart."""
    return {"items": design_taste.checklist()}


@router.get("/theme")
def theme() -> dict[str, Any]:
    """Colour tokens. The frontend reads this once and builds its Tailwind theme from it."""
    payload = design_taste.load_theme()
    return {
        "theme": payload,
        "path": str(theme_file()),
        "contrast": design_taste.contrast_pairs(payload),
        "note": (
            "Every colour in the app comes from this file. A hardcoded colour in a component is "
            "a design-taste failure, and the audit reports it."
        ),
    }


@router.put("/theme")
@guarded("update theme")
def update_theme(payload: dict[str, Any]) -> dict[str, Any]:
    from ...core.atomic import atomic_write_json

    atomic_write_json(theme_file(), payload)
    return {"theme": payload, "contrast": design_taste.contrast_pairs(payload)}


@router.get("/audit")
@guarded("run design audit")
def audit(write: bool = Query(default=False, description="Also update project-log/DESIGN_TASTE_AUDIT.md")) -> dict[str, Any]:
    views_dir = REPO_ROOT / "src" / "views"
    reviews, combined = design_taste.review_repo(views_dir)
    theme_result = design_taste.theme_review()
    written = ""
    if write:
        target = REPO_ROOT / "project-log" / "DESIGN_TASTE_AUDIT.md"
        design_taste.write_audit_log(target, reviews, theme_result)
        written = str(target)
    return {
        "screens": reviews,
        "summary": design_taste.summarise(reviews),
        "result": combined.as_dict(),
        "theme": theme_result.as_dict(),
        "views_dir": str(views_dir),
        "written": written,
    }


@router.get("/report")
def report() -> dict[str, Any]:
    """The Markdown report, rendered in the app rather than only on disk."""
    views_dir = REPO_ROOT / "src" / "views"
    reviews, combined = design_taste.review_repo(views_dir)
    return {
        "markdown": design_taste.format_report(reviews, design_taste.theme_review()),
        "summary": design_taste.summarise(reviews),
    }
