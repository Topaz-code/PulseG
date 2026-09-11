import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn, relativeTime } from "@/lib/utils";
import { useActiveProject, useNotifications, useProjects, useRunControl, useActivateProject } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/feedback";
import { PhaseDots } from "@/components/PhaseDots";
import {
  IconBell,
  IconChevronDown,
  IconFolder,
  IconPause,
  IconPlay,
  IconSettings,
  IconSparkle,
  IconStep,
} from "@/components/Icons";

/**
 * The top bar: which project, which phase, whether the studio is working, and the controls.
 *
 * It stays on every screen, because those four questions are the ones a person asks constantly
 * while the team runs. The run state is a single sentence, not a status code - "Working" with a
 * spinner while agents are busy, "Needs you" with a count when the review queue is not empty.
 */
export function TopBar() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const { data: active } = useActiveProject();
  const { data: projects } = useProjects();
  const { data: notifications } = useNotifications();
  const { start, pause, stop, tick } = useRunControl();
  const activate = useActivateProject();
  const openDialog = useStudio((state) => state.openDialog);
  const showToast = useStudio((state) => state.showToast);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const project = active?.project;
  const runState = active?.run_state ?? active?.board?.run_state;
  const reviewCount = active?.board?.review_count ?? 0;
  const status = runState?.status ?? "idle";

  const markRead = useMutation({
    mutationFn: () => api.post("/api/system/notifications/read", {}),
    onSuccess: () => client.invalidateQueries({ queryKey: ["notifications"] }),
  });

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const statusLabel = () => {
    if (!project) return { text: "No project open", tone: "muted" as const };
    if (reviewCount > 0) return { text: `${reviewCount} waiting for you`, tone: "accent" as const };
    if (status === "running") return { text: "Team is working", tone: "success" as const };
    if (status === "paused") return { text: "Paused", tone: "warning" as const };
    return { text: "Idle", tone: "muted" as const };
  };
  const state = statusLabel();

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-app-border bg-app-surface px-4">
      <div className="flex items-center gap-2 min-w-0">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-accent-foreground">
          <IconSparkle size={16} />
        </span>
        <span className="text-sm font-semibold text-mint-100 whitespace-nowrap">PulseG Studio</span>
      </div>

      <div className="relative" ref={menuRef}>
        <button
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-expanded={menuOpen}
          aria-haspopup="listbox"
          disabled={!projects?.projects?.length}
          className={cn(
            "flex items-center gap-2 rounded-md border border-app-border bg-charcoal-800 px-3 py-1.5 text-sm text-mint-100",
            "transition-colors duration-fast hover:border-surface-500 hover:bg-surface-800",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
            "disabled:opacity-50 disabled:cursor-not-allowed",
          )}
        >
          <IconFolder size={16} />
          <span className="max-w-[180px] truncate">{project?.name ?? "Open a project"}</span>
          <IconChevronDown size={14} />
        </button>

        {menuOpen ? (
          <div
            role="listbox"
            className="absolute left-0 top-11 z-30 w-72 rounded-md border border-app-border bg-charcoal-800 p-1 shadow-xl animate-slide-in"
          >
            {(projects?.projects ?? []).map((row) => (
              <button
                key={row.project_id}
                type="button"
                role="option"
                aria-selected={row.project_id === project?.project_id}
                onClick={() => {
                  setMenuOpen(false);
                  activate.mutate(row.project_id, {
                    onSuccess: () => showToast({ title: `Opened ${row.name}`, tone: "info" }),
                  });
                }}
                className={cn(
                  "flex w-full items-center justify-between gap-3 rounded-sm px-3 py-2 text-left text-sm",
                  "transition-colors duration-fast hover:bg-surface-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                  row.project_id === project?.project_id ? "text-canary-200" : "text-mint-200",
                )}
              >
                <span className="truncate">{row.name}</span>
                <span className="text-xs text-muted">{relativeTime(row.last_opened)}</span>
              </button>
            ))}
            <div className="my-1 h-px bg-app-border" />
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              onClick={() => {
                setMenuOpen(false);
                openDialog("newProject");
              }}
            >
              Start a new project
            </Button>
          </div>
        ) : null}
      </div>

      <div className="hidden md:block">
        {/* The phase comes from the overview; the number of approved phases is worked out by
            comparing the phase number with the count of tasks approved in earlier ones. */}
        <PhaseDots current={active?.overview?.phase?.phase ?? project?.phase ?? 0} approved={0} />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <span
          className={cn(
            "hidden sm:flex items-center gap-2 rounded-md px-2 py-1 text-xs",
            state.tone === "accent" && "bg-accent text-accent-foreground",
            state.tone === "success" && "bg-charcoal-800 text-teal-400",
            state.tone === "warning" && "bg-charcoal-800 text-canary-400",
            state.tone === "muted" && "bg-charcoal-800 text-muted",
          )}
        >
          {status === "running" ? <Spinner /> : null}
          {state.text}
        </span>

        {project ? (
          <div className="flex items-center gap-1">
            {status === "running" ? (
              <Button variant="ghost" size="icon" onClick={() => pause.mutate()} aria-label="Pause the team">
                <IconPause />
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => start.mutate()}
                aria-label="Start the team"
                loading={start.isPending}
              >
                <IconPlay />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => tick.mutate()}
              aria-label="Run one step"
              loading={tick.isPending}
            >
              <IconStep />
            </Button>
            {status !== "idle" && status !== "stopping" ? (
              <Button variant="ghost" size="sm" onClick={() => stop.mutate()} aria-label="Stop">
                Stop
              </Button>
            ) : null}
          </div>
        ) : null}

        <button
          type="button"
          onClick={() => {
            markRead.mutate();
            navigate("/logs");
          }}
          aria-label={`Notifications${notifications?.unread ? `, ${notifications.unread} unread` : ""}`}
          className={cn(
            "relative rounded-md p-2 text-mint-200 transition-colors duration-fast",
            "hover:bg-surface-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
          )}
        >
          <IconBell />
          {notifications?.unread ? (
            <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-semibold text-accent-foreground">
              {notifications.unread > 9 ? "9+" : notifications.unread}
            </span>
          ) : null}
        </button>

        {project ? (
          <Badge tone="muted" className="hidden lg:inline-flex">
            {active?.overview?.git?.branch ?? "main"}
          </Badge>
        ) : null}

        <Button
          variant="ghost"
          size="icon"
          onClick={() => navigate("/settings")}
          aria-label="Settings"
        >
          <IconSettings />
        </Button>
      </div>
    </header>
  );
}
