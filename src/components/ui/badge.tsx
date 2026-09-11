import type { ReactNode } from "react";
import { cn, humanise } from "@/lib/utils";
import type { TaskStatus } from "@/lib/types";

/**
 * Status pills, mapped from the task bus's own states.
 *
 * The colours are semantic tokens, never raw hexes, and the labels are written the way the
 * Kanban lanes say them - a user should never have to translate `NEEDS_HUMAN_REVIEW` in their
 * head to find out whether something is waiting for them.
 */
const STATUS_STYLE: Record<TaskStatus, string> = {
  PENDING: "bg-surface-700 text-mint-200",
  IN_PROGRESS: "bg-slate-600 text-mint-100",
  SUBMITTED: "bg-slate-500 text-charcoal-900",
  AUDITING: "bg-teal-600 text-mint-100",
  NEEDS_HUMAN_REVIEW: "bg-accent text-accent-foreground",
  APPROVED: "bg-teal-500 text-charcoal-900",
  REJECTED: "bg-canary-600 text-charcoal-900",
  FAILED: "bg-danger-solid text-danger-foreground",
  NEEDS_INTERVENTION: "bg-danger-700 text-danger-100",
};

const STATUS_LABEL: Record<TaskStatus, string> = {
  PENDING: "Waiting",
  IN_PROGRESS: "In Progress",
  SUBMITTED: "Submitted",
  AUDITING: "In Audit",
  NEEDS_HUMAN_REVIEW: "Needs Your Review",
  APPROVED: "Approved",
  REJECTED: "Sent Back",
  FAILED: "Failed",
  NEEDS_INTERVENTION: "Needs Intervention",
};

export function Badge({
  children,
  className,
  tone = "neutral",
}: {
  children: ReactNode;
  className?: string;
  tone?: "neutral" | "accent" | "success" | "warning" | "danger" | "muted";
}) {
  const tones = {
    neutral: "bg-surface-700 text-mint-200",
    accent: "bg-accent text-accent-foreground",
    success: "bg-teal-600 text-mint-100",
    warning: "bg-canary-600 text-charcoal-900",
    danger: "bg-danger-solid text-danger-foreground",
    muted: "bg-charcoal-700 text-muted",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-2 py-1 text-xs font-medium leading-none",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StatusPill({ status, className }: { status: TaskStatus; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-2 py-1 text-xs font-medium leading-none whitespace-nowrap",
        STATUS_STYLE[status] ?? "bg-surface-700 text-mint-200",
        className,
      )}
    >
      {STATUS_LABEL[status] ?? humanise(status)}
    </span>
  );
}

/** An agent chip: the agent's own colour from the theme, with the running state ringed. */
export function AgentChip({
  agentId,
  colour,
  label,
  status,
  className,
}: {
  agentId: string;
  colour?: string;
  label?: string;
  status?: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-sm px-2 py-1 text-xs font-medium leading-none bg-surface-700 text-mint-100",
        className,
      )}
      title={`${label ?? humanise(agentId)}${status ? ` - ${status}` : ""}`}
    >
      <span
        aria-hidden
        className="h-2 w-2 rounded-full"
        style={{ backgroundColor: colour ?? "var(--slate-500)" }}
      />
      {label ?? humanise(agentId)}
      {status === "working" ? (
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-teal-400 animate-pulse" />
      ) : null}
    </span>
  );
}
