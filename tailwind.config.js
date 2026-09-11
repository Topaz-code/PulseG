import { readFileSync } from "node:fs";

// Read rather than import so the config loads identically in Node, in Tailwind's own loader and
// in the packaged build - import attributes for JSON are still unevenly supported.
const theme = JSON.parse(
  readFileSync(new URL("./src/design/theme.json", import.meta.url), "utf8"),
);

/**
 * Every colour, size and radius in the app comes from src/design/theme.json, which is generated
 * from the six brand colours by `npm run tokens`. Nothing here is hand-picked, and no component
 * may use a hex value: the design audit in backend/skills/design_taste.py fails the build when
 * one does, because that is how a product drifts into looking like five products.
 *
 * Class names are semantic (`bg-app-surface`, `text-muted`) with the raw ramps still available
 * (`bg-canary-200`, `text-teal-400`) for the few places that need a specific step.
 */

/** Ramps: charcoal-100 … danger-900, each with the ten steps the generator produced. */
const ramps = Object.fromEntries(
  Object.entries(theme.scales).map(([name, steps]) => [
    name,
    Object.fromEntries(Object.entries(steps).map(([step, hex]) => [step, hex])),
  ]),
);

/** Semantic tokens resolve to the same hexes, so a screen reads as intent, not as colour. */
const semantic = {
  "app-background": theme.semantic["app-background"],
  "app-surface": theme.semantic["app-surface"],
  "app-surface-raised": theme.semantic["app-surface-raised"],
  "app-border": theme.semantic["app-border"],
  // The surface ramp stays reachable as `bg-surface-700` while the bare `bg-surface` keeps the
  // semantic value, so a component can ask either "a raised panel" or "step 7 of the ramp".
  surface: {
    DEFAULT: theme.semantic["app-surface"],
    ...Object.fromEntries(Object.entries(theme.scales.surface)),
  },
  border: theme.semantic["app-border"],
  background: theme.semantic["app-background"],
  foreground: theme.semantic["text-primary"],
  muted: {
    DEFAULT: theme.semantic["text-muted"],
    foreground: theme.semantic["text-muted"],
  },
  accent: {
    DEFAULT: theme.semantic.accent,
    foreground: theme.semantic["accent-contrast"],
    50: theme.scales.canary[100],
  },
  primary: {
    DEFAULT: theme.semantic.accent,
    foreground: theme.semantic["accent-contrast"],
  },
  success: {
    DEFAULT: theme.semantic.success,
    foreground: theme.semantic["app-background"],
  },
  warning: {
    DEFAULT: theme.semantic.warning,
    foreground: theme.semantic["app-background"],
  },
  danger: {
    DEFAULT: theme.semantic["danger-solid"],
    soft: theme.semantic.danger,
    foreground: theme.semantic["danger-contrast"],
  },
  ring: theme.semantic["focus-ring"],
  "text-secondary": theme.semantic["text-secondary"],
  "text-inverse": theme.semantic["text-inverse"],
};

/** Per-agent chip colours, exposed as `bg-agent-programmer` and friends. */
const agentColours = Object.fromEntries(
  Object.entries(theme.agent_colours).map(([agent, hex]) => [agent.replace(/_/g, "-"), hex]),
);

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { ...ramps, ...semantic, agent: agentColours },
      fontFamily: {
        sans: theme.typography.sans.split(",").map((part) => part.trim().replace(/^'|'$/g, "")),
        mono: theme.typography.mono.split(",").map((part) => part.trim().replace(/^'|'$/g, "")),
      },
      fontSize: Object.fromEntries(
        Object.entries(theme.typography.sizes).map(([name, size]) => [name, size]),
      ),
      // The 8px grid, from the same file the audit reads, so the two cannot disagree.
      spacing: Object.fromEntries(theme.spacing.steps.map((step) => [step, `${step * 4}px`])),
      borderRadius: {
        sm: theme.radii.sm,
        DEFAULT: theme.radii.md,
        md: theme.radii.md,
        lg: theme.radii.lg,
        xl: theme.radii.xl,
      },
      transitionDuration: {
        fast: theme.motion.fast,
        DEFAULT: theme.motion.base,
        slow: theme.motion.slow,
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-in": {
          from: { transform: "translateY(4px)", opacity: "0" },
          to: { transform: "translateY(0)", opacity: "1" },
        },
      },
      animation: {
        "fade-in": `fade-in ${theme.motion.base} ease-out`,
        "slide-in": `slide-in ${theme.motion.base} ease-out`,
      },
    },
  },
  plugins: [],
};
