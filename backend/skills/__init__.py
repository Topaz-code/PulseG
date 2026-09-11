"""The seven skills the specification requires, plus their shared contract.

Every skill returns the same :class:`~backend.skills.base.SkillResult`, so the Auditor can
combine arbitrary skills without special cases:

* ``grillme``       - intake interrogation and the handoff gate.
* ``design_taste``  - the D.7 checklist, enforced against the frontend source.
* ``no_ai_slop``    - banned phrases, filler, em-dash pile-ups and the rest of the tells.
* ``humanizer``     - rhythm, contractions, voice collapse, on-the-nose emotion.
* ``deslop``        - structural slop: empty files, TODO stubs, scaffolding left behind.
* ``harvard_shape`` - stylometry and document shape, built on the published method.
* ``adhd_filter``   - the compressed-context memory engine, and task/submission fit.

The Auditor combines no_ai_slop + humanizer + deslop + harvard_shape into one verdict; the
other three run earlier in the pipeline where their findings are actionable.
"""
from __future__ import annotations

from . import (
    adhd_filter,
    design_taste,
    deslop,
    grillme,
    harvard_shape,
    humanizer,
    no_ai_slop,
)
from .base import SkillFinding, SkillResult, estimate_tokens, excerpt_around

__all__ = [
    "SkillFinding",
    "SkillResult",
    "adhd_filter",
    "design_taste",
    "deslop",
    "estimate_tokens",
    "excerpt_around",
    "grillme",
    "harvard_shape",
    "humanizer",
    "no_ai_slop",
]
