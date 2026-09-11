#!/usr/bin/env node
/**
 * Turn the six brand colours into the token set the whole app renders from.
 *
 *     npm run tokens
 *
 * Writes two files from one source:
 *   src/design/theme.json      - imported by tailwind.config.js and the React app
 *   backend/core/theme.json    - the bundled default the backend serves and audits
 *
 * The 10-step scale per colour is the point: a component may only use `bg-canary-500` or
 * `text-mint-100`, never `#FAF33E` and never Tailwind's own `bg-yellow-300`. When every screen
 * is built from one ramp the app looks like one product, and the contrast audit in
 * backend/skills/design_taste.py can check the values that actually ship.
 *
 * Steps are computed in OKLCH so lightness moves evenly to the eye rather than to the maths:
 * 100 is lightest, 900 is darkest, hue and chroma stay put. Each brand colour is pinned at the
 * step whose lightness is closest to it, so the ramp stays monotonic - forcing yellow (a
 * perceptually light colour) into 500 would leave a hole in its own scale.
 */
import { writeFileSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import chroma from "chroma-js";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");

/**
 * Brand colours from the specification's Key Results, plus one flagged addition: `danger`.
 * The six-colour palette has no red, and a destructive action needs a colour a person reads as
 * "careful" without being told. It is a full 10-step ramp like every other colour and it is
 * recorded in project-log/RESOLUTIONS.md as an addition, not smuggled in.
 */
const BASE = {
  charcoal: "#1E1C21",
  surface: "#55505C",
  canary: "#FAF33E",
  teal: "#7FC6A4",
  mint: "#D6F8D6",
  slate: "#5D737E",
  danger: "#F2555A",
};

/** Descriptive names the backend's contrast audit and older notes refer to. */
const ALIASES = {
  "charcoal-bg": "charcoal",
  "charcoal-surface": "surface",
  "canary-yellow": "canary",
  "muted-teal": "teal",
  "frosted-mint": "mint",
  "blue-slate": "slate",
};

// Ten steps per colour, as the D.7 checklist requires. 100 is the lightest tint, 950 the
// darkest shade: the extra step below 900 is what gives the dark UI a true "recessed" tone for
// wells and scrollbar tracks instead of reusing the background colour.
const LIGHTNESS = {
  100: 0.97,
  200: 0.92,
  300: 0.85,
  400: 0.75,
  500: 0.64,
  600: 0.54,
  700: 0.44,
  800: 0.34,
  900: 0.24,
  950: 0.16,
};

/**
 * One colour -> ten steps (100 … 950).
 *
 * `chroma(...).oklch()` gives [l, c, h]; we hold c and h and move l. Chroma is pulled down for
 * the lightest and darkest steps, otherwise a saturated yellow at 0.97 lightness turns white
 * and a saturated red at 0.24 turns to mud.
 */
function ramp(hex) {
  const [baseL, baseC, baseH] = chroma(hex).oklch();
  const hue = Number.isNaN(baseH) ? 0 : baseH;
  const steps = {};
  let anchor = 500;
  let closest = Infinity;
  for (const [step, targetL] of Object.entries(LIGHTNESS)) {
    const distance = Math.abs(targetL - baseL);
    // The anchor is chosen from the nine main steps only. 950 always sits at the dark end of the
    // ramp, so letting it become the anchor would move a light brand colour like the canary to a
    // nearly-black step and break the whole scale.
    if (distance < closest && Number(step) !== 950) {
      closest = distance;
      anchor = Number(step);
    }
    const chromaFactor = Math.max(0.25, 1 - distance * 1.5);
    steps[step] = chroma
      .oklch(targetL, Math.min(baseC * chromaFactor, 0.37), hue)
      .hex()
      .toUpperCase();
  }
  steps[String(anchor)] = chroma(hex).hex().toUpperCase();
  return { steps, anchor };
}

const ramps = Object.fromEntries(Object.entries(BASE).map(([name, hex]) => [name, ramp(hex)]));
const scales = Object.fromEntries(Object.entries(ramps).map(([name, { steps }]) => [name, steps]));
const brand = Object.fromEntries(
  Object.entries(BASE).map(([name, hex]) => [
    name,
    { hex: chroma(hex).hex().toUpperCase(), step: ramps[name].anchor },
  ]),
);

const colors = {
  ...Object.fromEntries(Object.entries(ALIASES).map(([alias, name]) => [alias, brand[name].hex])),
  ...Object.fromEntries(Object.entries(BASE).map(([name, hex]) => [name, chroma(hex).hex().toUpperCase()])),
};

/** Semantic names, so a screen asks for a meaning instead of a colour. */
const SEMANTIC = {
  "app-background": ["charcoal", 900],
  "app-surface": ["charcoal", 800],
  "app-surface-raised": ["charcoal", 700],
  "app-border": ["surface", 700],
  "text-primary": ["mint", 100],
  "text-secondary": ["mint", 300],
  "text-muted": ["slate", 400],
  "text-inverse": ["charcoal", 900],
  accent: ["canary", brand.canary.step],
  "accent-contrast": ["charcoal", 900],
  success: ["teal", brand.teal.step],
  warning: ["canary", 600],
  // Two danger tokens on purpose: the deep red reads correctly as a filled destructive button,
  // but as text on a dark surface it drops to 2.3:1, so text and icons use the lighter step.
  danger: ["danger", 300],
  "danger-solid": ["danger", brand.danger.step],
  "danger-contrast": ["charcoal", 900],
  "focus-ring": ["canary", brand.canary.step],
};
const semantic = Object.fromEntries(
  Object.entries(SEMANTIC).map(([name, [scale, step]]) => [name, scales[scale][String(step)]]),
);

/** Per-agent chips in the dashboard. Defined here so a colour is never invented in a component. */
const AGENT_COLOURS = {
  planning_agent: scales.canary[brand.canary.step],
  prompter: scales.slate[400],
  researcher: scales.teal[300],
  transcriptor: scales.slate[300],
  documenter: scales.mint[400],
  story_writer: scales.teal[200],
  image_generator: scales.canary[300],
  audio_curator: scales.teal[400],
  programmer: scales.teal[brand.teal.step],
  tester: scales.slate[500],
  auditor: scales.canary[600],
  task_processor: scales.slate[600],
};

const theme = {
  version: 1,
  generated_by: "scripts/generate-theme.mjs (chroma.js, OKLCH ramps)",
  updated_at: new Date().toISOString(),
  colors,
  brand,
  scales,
  semantic,
  agent_colours: AGENT_COLOURS,
  typography: {
    sans: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif",
    mono: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
    sizes: {
      xs: "0.75rem",
      sm: "0.875rem",
      base: "1rem",
      lg: "1.125rem",
      xl: "1.25rem",
      "2xl": "1.5rem",
      "3xl": "1.875rem",
    },
  },
  spacing: { unit: 8, steps: [0, 1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64] },
  radii: { sm: "4px", md: "8px", lg: "12px", xl: "16px" },
  motion: { fast: "120ms", base: "180ms", slow: "260ms" },
  contrast_targets: { body: 4.5, large: 3.0 },
};

const json = JSON.stringify(theme, null, 2) + "\n";
for (const target of [
  resolve(ROOT, "src/design/theme.json"),
  resolve(ROOT, "backend/core/theme.json"),
]) {
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, json, "utf8");
}

// Report the contrast of the pairings the app actually renders, so a bad palette edit shows up
// in the terminal instead of in a screenshot.
const luminance = (hex) => {
  const [r, g, b] = chroma(hex)
    .rgb()
    .map((value) => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a, b) => {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return Math.round(((l1 + 0.05) / (l2 + 0.05)) * 100) / 100;
};

const pairs = [
  ["body text on app background", semantic["text-primary"], semantic["app-background"], 4.5],
  ["body text on surface", semantic["text-primary"], semantic["app-surface-raised"], 4.5],
  ["primary button label", semantic["accent-contrast"], semantic.accent, 4.5],
  ["muted label on background", semantic["text-muted"], semantic["app-background"], 3.0],
  ["success text on background", semantic.success, semantic["app-background"], 3.0],
  ["link on surface", semantic.accent, semantic["app-surface-raised"], 4.5],
  ["danger label on surface", semantic.danger, semantic["app-surface-raised"], 3.0],
  [
    "destructive button label",
    semantic["danger-contrast"],
    semantic["danger-solid"],
    4.5,
  ],
];

console.log("Wrote src/design/theme.json and backend/core/theme.json");
let failing = 0;
for (const [label, foreground, background, required] of pairs) {
  const value = contrast(foreground, background);
  const ok = value >= required;
  if (!ok) failing += 1;
  console.log(
    `  ${ok ? "ok  " : "FAIL"} ${label}: ${value}:1 (needs ${required}:1) ${foreground} on ${background}`,
  );
}
for (const [name, { hex, step }] of Object.entries(brand)) {
  console.log(`  brand ${name}: ${hex} at step ${step}`);
}

// Re-read what we wrote: catches a malformed file here rather than in a Tailwind build.
JSON.parse(readFileSync(resolve(ROOT, "src/design/theme.json"), "utf8"));
if (failing) process.exitCode = 1;
