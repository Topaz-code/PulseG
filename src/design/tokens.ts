import theme from "./theme.json";

/**
 * The generated token set, typed. Every colour in the app resolves through this file or through
 * the Tailwind classes built from the same JSON - there is no third place where a colour can be
 * invented, which is what makes the design audit meaningful.
 */
export type ThemeToken = {
  colors: Record<string, string>;
  brand: Record<string, { hex: string; step: number }>;
  scales: Record<string, Record<string, string>>;
  semantic: Record<string, string>;
  agent_colours: Record<string, string>;
  typography: { sans: string; mono: string; sizes: Record<string, string> };
  spacing: { unit: number; steps: number[] };
  radii: Record<string, string>;
  motion: Record<string, string>;
};

export const tokens = theme as unknown as ThemeToken;

/** Colour chip for an agent id, falling back to the muted slate so an unknown agent still shows. */
export function agentColour(agentId: string): string {
  return tokens.agent_colours[agentId] ?? tokens.scales.slate?.["500"] ?? tokens.colors.slate;
}

/**
 * Every semantic token as a CSS custom property on :root.
 *
 * The Tailwind classes cover normal styling; the variables exist for the few things Tailwind
 * cannot express - canvas drawing, the React Flow graph, and inline SVG fills - so those code
 * paths read the palette instead of hardcoding hexes.
 */
export function installCssVariables(target: HTMLElement = document.documentElement): void {
  const semantic = tokens.semantic ?? {};
  for (const [name, value] of Object.entries(semantic)) {
    target.style.setProperty(`--${name}`, value);
  }
  for (const [name, steps] of Object.entries(tokens.scales ?? {})) {
    for (const [step, value] of Object.entries(steps)) {
      target.style.setProperty(`--${name}-${step}`, value);
    }
  }
  for (const [name, value] of Object.entries(tokens.colors ?? {})) {
    target.style.setProperty(`--brand-${name}`, value);
  }
}

/** Hex with an alpha channel, for translucent overlays that must not introduce a new colour. */
export function withAlpha(hex: string, alpha: number): string {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
