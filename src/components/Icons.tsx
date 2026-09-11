import type { SVGProps } from "react";

/**
 * The icon set, drawn locally.
 *
 * The brief asks for icons8's "fluency systems" family. img.icons8.com is not reachable from the
 * build environment (recorded in project-log/UNCERTAIN_CODE.md), so the same visual language is
 * hand-drawn here: 24x24 grid, 1.6px rounded strokes, no fills, currentColor, and one concept per
 * glyph. That keeps the app free of a network dependency at build time and off a third party's
 * licence terms - and every icon inherits the surrounding text colour, which is what makes the
 * per-agent and per-status palettes work.
 *
 * Adding an icon means adding one `Path` here. Nothing else in the app draws its own SVG.
 */
type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 20, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconDashboard = (props: IconProps) => (
  <Icon {...props}>
    <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
    <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" />
    <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" />
    <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" />
  </Icon>
);

export const IconBoard = (props: IconProps) => (
  <Icon {...props}>
    <rect x="3" y="4" width="5" height="16" rx="1.5" />
    <rect x="9.5" y="4" width="5" height="10" rx="1.5" />
    <rect x="16" y="4" width="5" height="13" rx="1.5" />
  </Icon>
);

export const IconBook = (props: IconProps) => (
  <Icon {...props}>
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5z" />
    <path d="M8 8h7M8 11.5h5" />
  </Icon>
);

export const IconImages = (props: IconProps) => (
  <Icon {...props}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <circle cx="8.5" cy="9.5" r="1.5" />
    <path d="M4 17l4.5-4.5 3.5 3.5 3-3L20 17" />
  </Icon>
);

export const IconSearch = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M15.5 15.5 21 21" />
  </Icon>
);

export const IconTerminal = (props: IconProps) => (
  <Icon {...props}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M7 9l3 3-3 3M13 15h4" />
  </Icon>
);

export const IconBranch = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="6.5" cy="5.5" r="2.5" />
    <circle cx="6.5" cy="18.5" r="2.5" />
    <circle cx="17.5" cy="9" r="2.5" />
    <path d="M6.5 8v8M9 5.5h5.5A3 3 0 0 1 17.5 9" />
  </Icon>
);

export const IconSettings = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3v2.5M12 18.5V21M4.9 7.5l2.2 1.3M16.9 15.2l2.2 1.3M4.9 16.5l2.2-1.3M16.9 8.8l2.2-1.3" />
  </Icon>
);

export const IconBell = (props: IconProps) => (
  <Icon {...props}>
    <path d="M6.5 10a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 2.5 6.5H4c1-1 2.5-2.5 2.5-6.5z" />
    <path d="M10 19.5a2 2 0 0 0 4 0" />
  </Icon>
);

export const IconPlay = (props: IconProps) => (
  <Icon {...props}>
    <path d="M7 4.5 19 12 7 19.5z" />
  </Icon>
);

export const IconPause = (props: IconProps) => (
  <Icon {...props}>
    <path d="M9 5v14M15 5v14" />
  </Icon>
);

export const IconStep = (props: IconProps) => (
  <Icon {...props}>
    <path d="M6 5.5 15 12 6 18.5z" />
    <path d="M18 5v14" />
  </Icon>
);

export const IconPlus = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);

export const IconCheck = (props: IconProps) => (
  <Icon {...props}>
    <path d="M4.5 12.5 9.5 17.5 19.5 6.5" />
  </Icon>
);

export const IconClose = (props: IconProps) => (
  <Icon {...props}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Icon>
);

export const IconChevronDown = (props: IconProps) => (
  <Icon {...props}>
    <path d="M6 9.5 12 15.5 18 9.5" />
  </Icon>
);

export const IconChevronRight = (props: IconProps) => (
  <Icon {...props}>
    <path d="M9.5 6 15.5 12 9.5 18" />
  </Icon>
);

export const IconFolder = (props: IconProps) => (
  <Icon {...props}>
    <path d="M3 7.5A2 2 0 0 1 5 5.5h3.8l1.7 2H19a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </Icon>
);

export const IconRefresh = (props: IconProps) => (
  <Icon {...props}>
    <path d="M20 11a8 8 0 1 0-2.3 5.7" />
    <path d="M20 5v6h-6" />
  </Icon>
);

export const IconKey = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="8" cy="12" r="3.5" />
    <path d="M11.5 12H21M18 12v3M15 12v2.5" />
  </Icon>
);

export const IconRobot = (props: IconProps) => (
  <Icon {...props}>
    <rect x="4" y="8" width="16" height="11" rx="2.5" />
    <path d="M12 4.5V8M9 13h.01M15 13h.01M9.5 16.5h5" />
  </Icon>
);

export const IconSparkle = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 3.5 13.8 9l5.7 1.8-5.7 1.9L12 18l-1.8-5.3L4.5 10.8 10.2 9z" />
  </Icon>
);

export const IconExternal = (props: IconProps) => (
  <Icon {...props}>
    <path d="M14 4h6v6M20 4l-8.5 8.5" />
    <path d="M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10" />
  </Icon>
);

export const IconCopy = (props: IconProps) => (
  <Icon {...props}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M6 15H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v1" />
  </Icon>
);

export const IconSend = (props: IconProps) => (
  <Icon {...props}>
    <path d="M4 12l16-7-6.5 16-2.5-6z" />
    <path d="M11 15l9-10" />
  </Icon>
);

export const IconClip = (props: IconProps) => (
  <Icon {...props}>
    <path d="M8 12.5l6.5-6.5a3 3 0 0 1 4.2 4.2L11 18a5 5 0 0 1-7-7l7-7" />
  </Icon>
);

export const IconAlert = (props: IconProps) => (
  <Icon {...props}>
    <path d="M12 4.5 21 19.5H3z" />
    <path d="M12 10v4M12 16.8h.01" />
  </Icon>
);

export const IconInfo = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5M12 7.8h.01" />
  </Icon>
);

export const IconCamera = (props: IconProps) => (
  <Icon {...props}>
    <rect x="3" y="7" width="18" height="12" rx="2" />
    <circle cx="12" cy="13" r="3.2" />
    <path d="M9 7l1.5-2.5h3L15 7" />
  </Icon>
);

export const IconGraph = (props: IconProps) => (
  <Icon {...props}>
    <circle cx="6" cy="6.5" r="2.5" />
    <circle cx="18" cy="6.5" r="2.5" />
    <circle cx="12" cy="18" r="2.5" />
    <path d="M7.8 8.3 10.5 16M16.2 8.3 13.5 16M8.5 6.5h7" />
  </Icon>
);

export const IconGamepad = (props: IconProps) => (
  <Icon {...props}>
    <path d="M7 8.5h10a4 4 0 0 1 3.9 3.1l.7 3.4a2.4 2.4 0 0 1-4.2 2.1l-1.1-1.6H7.7l-1.1 1.6a2.4 2.4 0 0 1-4.2-2.1l.7-3.4A4 4 0 0 1 7 8.5z" />
    <path d="M8.5 11v3M7 12.5h3M15.5 12h.01M17.5 14h.01" />
  </Icon>
);
