import type { ReactNode } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as ProgressPrimitive from "@radix-ui/react-progress";
import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { cn } from "@/lib/utils";
import { Button } from "./button";

/** A quiet spinner. One animation, short, and reduced-motion aware through index.css. */
export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <span role="status" aria-live="polite" className={cn("inline-flex items-center gap-2", className)}>
      <span
        aria-hidden
        className="h-4 w-4 animate-spin rounded-full border-2 border-surface-600 border-t-accent"
      />
      {label ? <span className="text-xs text-muted">{label}</span> : null}
    </span>
  );
}

/**
 * Empty states are designed, not defaulted: every one says what would appear here and what to
 * do to make it appear. The design audit fails a view that renders a collection without one.
 */
export function EmptyState({
  title,
  detail,
  action,
  icon,
  className,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center gap-3 rounded-lg border border-dashed border-app-border px-6 py-12",
        className,
      )}
    >
      {icon ? <div className="text-muted">{icon}</div> : null}
      <div>
        <p className="text-sm font-medium text-mint-100">{title}</p>
        <p className="text-xs text-muted mt-1 max-w-md">{detail}</p>
      </div>
      {action}
    </div>
  );
}

export function Progress({
  value,
  max = 100,
  tone = "accent",
  label,
  className,
}: {
  value: number;
  max?: number;
  tone?: "accent" | "success" | "danger" | "muted";
  label?: string;
  className?: string;
}) {
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  const toneClass = {
    accent: "bg-accent",
    success: "bg-teal-400",
    danger: "bg-danger-solid",
    muted: "bg-surface-500",
  }[tone];
  return (
    <div className={className}>
      <ProgressPrimitive.Root
        value={percent}
        className="h-2 w-full overflow-hidden rounded-full bg-charcoal-800"
        aria-label={label ?? "progress"}
      >
        <ProgressPrimitive.Indicator
          className={cn("h-full rounded-full transition-transform duration-base", toneClass)}
          style={{ width: `${percent}%` }}
        />
      </ProgressPrimitive.Root>
      {label ? <div className="text-xs text-muted mt-1">{label}</div> : null}
    </div>
  );
}

export function ScrollArea({
  children,
  className,
  viewportClassName,
}: {
  children: ReactNode;
  className?: string;
  viewportClassName?: string;
}) {
  return (
    <ScrollAreaPrimitive.Root className={cn("relative overflow-hidden", className)}>
      <ScrollAreaPrimitive.Viewport className={cn("h-full w-full", viewportClassName)}>
        {children}
      </ScrollAreaPrimitive.Viewport>
      <ScrollAreaPrimitive.Scrollbar
        orientation="vertical"
        className="flex w-2 touch-none select-none p-0.5 transition-opacity duration-fast"
      >
        <ScrollAreaPrimitive.Thumb className="flex-1 rounded-full bg-surface-600 hover:bg-surface-500" />
      </ScrollAreaPrimitive.Scrollbar>
    </ScrollAreaPrimitive.Root>
  );
}

export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={300}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            sideOffset={6}
            className="z-50 max-w-xs rounded-md border border-app-border bg-charcoal-800 px-3 py-2 text-xs text-mint-100 shadow-lg animate-fade-in"
          >
            {label}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

export function Dialog({
  open,
  onOpenChange,
  title,
  detail,
  children,
  footer,
  width = "lg",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  detail?: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: "sm" | "md" | "lg" | "xl";
}) {
  const widths = { sm: "max-w-md", md: "max-w-xl", lg: "max-w-3xl", xl: "max-w-5xl" };
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-charcoal-900/80 animate-fade-in" />
        <DialogPrimitive.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2",
            "rounded-lg border border-app-border bg-app-surface shadow-2xl animate-slide-in",
            widths[width],
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-app-border px-6 py-4">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold text-mint-100">
                {title}
              </DialogPrimitive.Title>
              {detail ? (
                <DialogPrimitive.Description className="text-xs text-muted mt-1">
                  {detail}
                </DialogPrimitive.Description>
              ) : null}
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close">
                <span aria-hidden className="text-lg leading-none">
                  x
                </span>
              </Button>
            </DialogPrimitive.Close>
          </div>
          <ScrollArea className="max-h-[70vh]">
            <div className="px-6 py-4">{children}</div>
          </ScrollArea>
          {footer ? (
            <div className="flex items-center justify-end gap-3 border-t border-app-border px-6 py-4">
              {footer}
            </div>
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function Toaster({
  toast,
  onDismiss,
}: {
  toast: { title: string; body?: string; tone: "info" | "success" | "danger" } | null;
  onDismiss: () => void;
}) {
  if (!toast) return null;
  const tones = {
    info: "border-app-border bg-charcoal-800",
    success: "border-teal-600 bg-charcoal-800",
    danger: "border-danger-solid bg-charcoal-800",
  };
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "fixed bottom-24 right-6 z-50 w-80 rounded-md border px-4 py-3 shadow-xl animate-slide-in",
        tones[toast.tone],
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-mint-100">{toast.title}</p>
          {toast.body ? <p className="text-xs text-muted mt-1">{toast.body}</p> : null}
        </div>
        <Button variant="ghost" size="sm" onClick={onDismiss} aria-label="Dismiss">
          Dismiss
        </Button>
      </div>
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-md border border-danger-700 bg-charcoal-800 px-4 py-3 text-sm text-danger-soft">
      {children}
    </div>
  );
}
