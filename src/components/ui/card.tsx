import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Surfaces. Two levels only - `Card` for the raised content on a view, `Panel` for the flat
 * containers inside it (a sidebar section, a rail block). More nesting than that and a dark UI
 * turns into grey soup, which is the failure mode this palette avoids.
 */
export function Card({
  children,
  className,
  interactive = false,
}: {
  children: ReactNode;
  className?: string;
  interactive?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-app-border bg-app-surface-raised",
        interactive && "transition-colors duration-fast hover:border-surface-500 hover:bg-surface-700",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  detail,
  action,
  className,
}: {
  title: ReactNode;
  detail?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4 px-4 py-3 border-b border-app-border", className)}>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-mint-100 truncate">{title}</h3>
        {detail ? <p className="text-xs text-muted mt-1">{detail}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("px-4 py-3", className)}>{children}</div>;
}

export function Panel({
  children,
  className,
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: ReactNode;
}) {
  return (
    <div className={cn("rounded-md border border-app-border bg-app-surface p-4", className)}>
      {title ? <div className="text-xs uppercase tracking-wide text-muted mb-3">{title}</div> : null}
      {children}
    </div>
  );
}

export function Stat({
  label,
  value,
  detail,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: "default" | "accent" | "success" | "warning" | "danger";
}) {
  const toneClass = {
    default: "text-mint-100",
    accent: "text-canary-200",
    success: "text-teal-400",
    warning: "text-canary-400",
    danger: "text-danger-soft",
  }[tone];
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className={cn("text-2xl font-semibold mt-1", toneClass)}>{value}</div>
      {detail ? <div className="text-xs text-muted mt-1">{detail}</div> : null}
    </div>
  );
}
