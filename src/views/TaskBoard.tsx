import { useMemo, useState } from "react";
import { cn, humanise, relativeTime } from "@/lib/utils";
import { useBoard, useReviewQueue, useRunControl } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import type { TaskStatus } from "@/lib/types";
import { AgentChip, StatusPill } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { EmptyState, Spinner } from "@/components/ui/feedback";
import { IconAlert, IconPlay, IconSearch } from "@/components/Icons";

/**
 * The Kanban board: seven lanes, one per state a task can be in.
 *
 * The lanes are not decoration - they are the task bus's own state machine, including the two
 * lanes that make this product trustworthy: "Declined, retrying" (the Auditor sent it back and the
 * agent is working on it again) and "Needs Intervention" (every model in the chain failed and the
 * task is paused, never quietly dropped).
 *
 * Clicking any card opens the review drawer with the whole story: the instruction, the output, the
 * verdict, the screenshots, and the decision.
 */
const LANES: { status: TaskStatus; label: string; hint: string }[] = [
  { status: "PENDING", label: "Waiting", hint: "Ready, not started" },
  { status: "IN_PROGRESS", label: "In Progress", hint: "An agent is on it" },
  { status: "AUDITING", label: "In Audit", hint: "Being checked" },
  { status: "NEEDS_HUMAN_REVIEW", label: "Needs Your Review", hint: "Your call" },
  { status: "APPROVED", label: "Approved", hint: "Saved to history" },
  { status: "REJECTED", label: "Declined, Retrying", hint: "Sent back to the agent" },
  { status: "NEEDS_INTERVENTION", label: "Needs Intervention", hint: "Paused, not lost" },
];

/**
 * Lane order when the board is wide enough to show all seven. Lanes that need a person - review
 * and intervention - come first, so the thing that is blocked is never scrolled off the screen.
 */
const LANE_ORDER: TaskStatus[] = [
  "NEEDS_HUMAN_REVIEW",
  "NEEDS_INTERVENTION",
  "IN_PROGRESS",
  "PENDING",
  "AUDITING",
  "REJECTED",
  "APPROVED",
];

export function TaskBoard() {
  const { data: board, isLoading, error } = useBoard();
  const { data: review } = useReviewQueue();
  const { start } = useRunControl();
  const openTask = useStudio((state) => state.openTask);
  const [query, setQuery] = useState("");
  const [laneFilter, setLaneFilter] = useState<TaskStatus | "">("");

  // The board arrives as lanes (the same shape the columns render), so the flat list the search
  // and the counts work on is built from them rather than fetched separately.
  const tasks = useMemo(() => (board?.lanes ?? []).flatMap((lane) => lane.tasks), [board]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return tasks.filter((task) => {
      if (laneFilter && task.status !== laneFilter) return false;
      if (!needle) return true;
      return (
        task.task_id.toLowerCase().includes(needle) ||
        task.instruction.toLowerCase().includes(needle) ||
        task.assigned_to.toLowerCase().includes(needle)
      );
    });
  }, [tasks, query, laneFilter]);

  const byLane = useMemo(() => {
    const groups: Record<string, typeof tasks> = {};
    for (const lane of LANES) groups[lane.status] = [];
    for (const task of filtered) {
      if (!groups[task.status]) groups[task.status] = [];
      groups[task.status].push(task);
    }
    for (const status of Object.keys(groups)) {
      groups[status].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
    }
    return groups;
  }, [filtered, tasks]);

  const reviewCount = review?.count ?? 0;
  const pausedCount = board?.counts?.NEEDS_INTERVENTION ?? 0;

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-app-border px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold text-mint-100">Task Board</h1>
          <p className="text-xs text-muted">
            {tasks.length} tasks
            {reviewCount > 0 ? ` - ${reviewCount} waiting for you` : ""}
            {pausedCount > 0 ? ` - ${pausedCount} paused` : ""}
          </p>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div className="relative">
            <IconSearch size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find a task"
              aria-label="Find a task"
              className="w-56 pl-10"
            />
          </div>
          <select
            value={laneFilter}
            onChange={(event) => setLaneFilter(event.target.value as TaskStatus | "")}
            aria-label="Show one lane"
            className="rounded-md border border-app-border bg-charcoal-800 px-3 py-2 text-sm text-mint-100 transition-colors duration-fast hover:border-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <option value="">All lanes</option>
            {LANES.map((lane) => (
              <option key={lane.status} value={lane.status}>
                {lane.label}
              </option>
            ))}
          </select>
          <Button variant="primary" onClick={() => start.mutate()} loading={start.isPending} data-primary-action>
            <IconPlay size={16} />
            Start the team
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Reading the board" />
        </div>
      ) : error ? (
        <div className="p-6">
          <EmptyState
            title="The board could not be read"
            detail={error instanceof Error ? error.message : "Open a project and try again."}
          />
        </div>
      ) : tasks.length === 0 ? (
        <div className="p-6">
          <EmptyState
            title="No tasks yet"
            detail="Tasks appear here when you send an instruction from the bar below, or when you start the build team."
            action={
              <Button variant="secondary" onClick={() => start.mutate()} loading={start.isPending}>
                Start the build team
              </Button>
            }
          />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden px-6 py-4">
          <div className="flex h-full gap-4" style={{ minWidth: LANES.length * 272 }}>
            {LANE_ORDER.map((laneStatus) => {
              const lane = LANES.find((candidate) => candidate.status === laneStatus)!;
              const items = byLane[lane.status] ?? [];
              const isReview = lane.status === "NEEDS_HUMAN_REVIEW";
              const isPaused = lane.status === "NEEDS_INTERVENTION";
              return (
                <section
                  key={lane.status}
                  aria-label={lane.label}
                  className={cn(
                    "flex h-full w-[256px] shrink-0 flex-col rounded-lg border bg-app-surface/60",
                    isReview ? "border-accent/60" : "",
                    isPaused && items.length > 0 ? "border-danger-700" : "",
                  )}
                >
                  <header className="flex items-center justify-between gap-2 border-b border-app-border px-3 py-2">
                    <div className="min-w-0">
                      <h2 className="truncate text-sm font-medium text-mint-100">{lane.label}</h2>
                      <p className="text-[11px] text-muted truncate">{lane.hint}</p>
                    </div>
                    <span
                      className={cn(
                        "flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-semibold",
                        isReview && items.length > 0
                          ? "bg-accent text-accent-foreground"
                          : isPaused && items.length > 0
                            ? "bg-danger-solid text-danger-foreground"
                            : "bg-surface-700 text-mint-200",
                      )}
                    >
                      {items.length}
                    </span>
                  </header>

                  <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
                    {items.length === 0 ? (
                      <p className="px-2 py-6 text-center text-xs text-muted">
                        {lane.status === "PENDING"
                          ? "Nothing queued."
                          : lane.status === "NEEDS_HUMAN_REVIEW"
                            ? "Nothing needs you right now."
                            : lane.status === "NEEDS_INTERVENTION"
                              ? "Nothing is stuck."
                              : "Empty."}
                      </p>
                    ) : (
                      items.map((task) => (
                        <button
                          key={task.task_id}
                          type="button"
                          onClick={() => openTask(task.task_id)}
                          className={cn(
                            "w-full rounded-md border border-app-border bg-charcoal-800 p-3 text-left",
                            "transition-colors duration-fast hover:border-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                          )}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <span className="line-clamp-3 text-sm text-mint-100">{task.title || task.instruction}</span>
                            {task.status === "NEEDS_INTERVENTION" ? (
                              <IconAlert size={14} className="mt-1 shrink-0 text-danger-soft" />
                            ) : null}
                          </div>
                          <div className="mt-2 flex items-center justify-between gap-2">
                            <AgentChip agentId={task.assigned_to} label={humanise(task.assigned_to)} />
                            <span className="text-[11px] text-muted">{relativeTime(task.updated_at)}</span>
                          </div>
                          <div className="mt-2 flex items-center gap-2 text-[11px] text-muted">
                            <span>{task.task_id}</span>
                            <span aria-hidden>|</span>
                            <span>Phase {task.phase}</span>
                            {task.retry_count > 0 ? (
                              <>
                                <span aria-hidden>|</span>
                                <span>{task.retry_count} retries</span>
                              </>
                            ) : null}
                          </div>
                          {task.status !== lane.status ? <StatusPill status={task.status} /> : null}
                        </button>
                      ))
                    )}
                  </div>
                </section>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
