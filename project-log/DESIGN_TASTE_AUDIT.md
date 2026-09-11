# Design taste audit (D.7)

Status: **not yet run - the views do not exist.** This file is written by
`scripts/design_audit.py`, which walks `src/views/*.tsx`, applies the eight D.7 checks to each
screen's real source, computes every palette contrast pair from `theme.json`, and overwrites
this document with the result.

It is generated on purpose: a checklist in a document gets skipped, a checklist that fails the
build does not. The audit runs as part of `npm run build` once the views land, and
`python scripts/design_audit.py --check` exits non-zero when any screen is outstanding.

## The checklist being enforced

1. One primary action per screen - exactly one element marked `data-primary-action`.
2. 8px spacing grid - padding, margin and gap utilities restricted to the project's step scale.
3. Interactive states - hover, disabled and loading treatment on anything clickable.
4. Designed empty states - a collection render without an empty branch is a finding.
5. WCAG AA contrast in dark mode - computed from the theme tokens, body text 4.5:1, large text
   and graphical objects 3:1.
6. Keyboard navigable with visible focus - `focus-visible` rings, and no `outline-none` without
   a replacement.
7. No jargon in user-facing copy - "artifact", "webhook", "payload" and friends are flagged
   with the string quoted, because a non-technical user has to be able to run this.
8. Subtle purposeful motion - transitions short, and no decorative infinite animation.

## Current theme contrast (computed)

The palette is checked by the same script that checks the screens, so these numbers are
reproducible rather than remembered:

| Pair | Contrast | Required | Result |
| --- | --- | --- | --- |
| Primary button label (`charcoal-bg` on `canary-yellow`) | see audit output | 4.5:1 | pending first run |
| Body text on app background (`frosted-mint` on `charcoal-bg`) | see audit output | 4.5:1 | pending first run |

`backend/tests/test_skills.py::test_palette_contrast_meets_wcag_aa` already asserts the two
critical pairs pass, so a palette regression fails the test suite even before the views exist.
