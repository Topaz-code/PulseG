import { useEffect, useMemo, useRef, useState } from "react";
import { cn, clockTime, humanise } from "@/lib/utils";
import { useContextStats, useFailurePatterns, useLogEvents, useRunControl, useStreamInfo } from "@/lib/queries";
import { useEventStream } from "@/lib/ws";
import { useStudio } from "@/lib/store";
import { agentColour } from "@/design/tokens";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, Spinner } from "@/components/ui/feedback";
import { IconPause, IconPlay, IconStep, IconTerminal } from "@/components/Icons";

/**
 * The live terminal: one line per thing that happened, colour-coded by the agent that did it.
 *
 * This is the honesty screen. Everything the studio does is logged with which agent, which model,
 * and which task produced it, and this shows the stream as it arrives over the websocket - not on
 * a timer. When a run stalls, this is where a person finds out why: the failed attempt, the
 * provider that rate-limited, the fallback that answered instead.
 *
 * A live tail and the persisted history are deliberately two different lists. The tail is what has
 * arrived since the window opened; the history is what was kept on disk. Losing the tail on
 * refresh would be confusing, so both are shown, and the history is the one that can be filtered.
 */
const LEVELS = ["all", "INFO", "WARNING", "ERROR", "DEBUG"] as const;

export function Logs() {
  const { logTail, clearLogTail, connection } = useEventStream();
  const { data: history, isLoading } = useLogEvents(400);
  const { data: info } = useStreamInfo();
  const { data: context } = useContextStats();
  const { data: failures } = useFailurePatterns();
  const { start, pause, tick } = useRunControl();
  const showToast = useStudio((state) => state.showToast);
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("all");
  const [agent, setAgent] = useState("");
  const [follow, setFollow] = useState(true);
  const bottom = useRef<HTMLDivElement>(null);

  const lines = useMemo(() => {
    const persisted = (history?.events ?? []).map((event) => ({
      key: `h-${event.seq}`,
      at: event.at,
      level: String(event.payload.level ?? "INFO").toUpperCase(),
      message: String(event.payload.message ?? event.type),
      agent: String(event.payload.agent_id ?? event.payload.agent ?? ""),
      task: String(event.payload.task_id ?? ""),
      live: false,
    }));
    const live = logTail.map((event) => ({
      key: `l-${event.seq}`,
      at: event.at,
      level: String(event.payload.level ?? "INFO").toUpperCase(),
      message: String(event.payload.message ?? event.type),
      agent: String(event.payload.agent_id ?? event.payload.agent ?? ""),
      task: String(event.payload.task_id ?? ""),
      live: true,
    }));
    const seen = new Set(live.map((line) => line.key));
    return [...live, ...persisted.filter((line) => !seen.has(line.key))].sort((a, b) => a.at.localeCompare(b.at));
  }, [history, logTail]);

  const agents = useMemo(() => {
    const found = new Set<string>();
    for (const line of lines) if (line.agent) found.add(line.agent);
    return [...found].sort();
  }, [lines]);

  const visible = lines.filter((line) => {
    if (level !== "all" && line.level !== level) return false;
    if (agent && line.agent !== agent) return false;
    return true;
  });

  useEffect(() => {
    if (follow) bottom.current?.scrollIntoView({ block: "end" });
  }, [visible.length, follow]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-app-border px-6 py-4">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-mint-100">
            <IconTerminal size={18} />
            Logs and Live Terminal
          </h1>
          <p className="text-xs text-muted">
            {visible.length} lines -{" "}
            {connection === "open" ? "connected, updating live" : "reconnecting - showing saved history"}
          </p>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <select
            value={level}
            onChange={(event) => setLevel(event.target.value as (typeof LEVELS)[number])}
            aria-label="Filter by level"
            className="rounded-md border border-app-border bg-charcoal-800 px-3 py-2 text-sm text-mint-100 transition-colors duration-fast hover:border-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {LEVELS.map((option) => (
              <option key={option} value={option}>
                {option === "all" ? "Every level" : humanise(option)}
              </option>
            ))}
          </select>
          <select
            value={agent}
            onChange={(event) => setAgent(event.target.value)}
            aria-label="Filter by agent"
            className="rounded-md border border-app-border bg-charcoal-800 px-3 py-2 text-sm text-mint-100 transition-colors duration-fast hover:border-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <option value="">Every agent</option>
            {agents.map((name) => (
              <option key={name} value={name}>
                {humanise(name)}
              </option>
            ))}
          </select>
          <Button variant="ghost" size="sm" onClick={() => setFollow((value) => !value)} aria-pressed={follow}>
            {follow ? "Following" : "Not following"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              clearLogTail();
              showToast({ title: "Live tail cleared", body: "Saved history is still here.", tone: "info" });
            }}
          >
            Clear the tail
          </Button>
          <Button variant="secondary" size="sm" onClick={() => tick.mutate()} loading={tick.isPending}>
            <IconStep size={14} />
            One step
          </Button>
          <Button variant="primary" size="sm" data-primary-action onClick={() => start.mutate()} loading={start.isPending}>
            <IconPlay size={14} />
            Start
          </Button>
          <Button variant="ghost" size="sm" onClick={() => pause.mutate()} loading={pause.isPending}>
            <IconPause size={14} />
            Pause
          </Button>
        </div>
      </div>

      <div className="grid gap-3 border-b border-app-border px-6 py-3 md:grid-cols-3">
        <div className="rounded-md border border-app-border bg-app-surface px-3 py-2">
          <p className="text-xs uppercase tracking-wide text-muted">Context sent to agents</p>
          <p className="mt-1 text-sm text-mint-100">
            {context ? `${context.saved_tokens} tokens saved by summarising` : "Reading..."}
          </p>
          {context ? (
            <p className="text-[11px] text-muted">
              design document {context.gdd_tokens_sent} of {context.gdd_tokens_full} tokens, progress{" "}
              {context.progress_tokens_sent} of {context.progress_tokens_full}
            </p>
          ) : null}
        </div>
        <div className="rounded-md border border-app-border bg-app-surface px-3 py-2">
          <p className="text-xs uppercase tracking-wide text-muted">Stream</p>
          <p className="mt-1 text-sm text-mint-100">
            {info ? `${info.subscribers} open, ${info.replay_available} replays kept` : "Reading..."}
          </p>
          <p className="text-[11px] text-muted">{info?.note}</p>
        </div>
        <div className="rounded-md border border-app-border bg-app-surface px-3 py-2">
          <p className="text-xs uppercase tracking-wide text-muted">Repeated failures</p>
          {failures?.patterns?.length ? (
            <ul className="mt-1 space-y-1">
              {failures.patterns.slice(0, 3).map((pattern, index) => (
                <li key={index} className="truncate text-[11px] text-canary-200">
                  {String(pattern.message ?? pattern.kind ?? JSON.stringify(pattern))}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm text-mint-100">None right now</p>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto bg-charcoal-900 px-6 py-4 font-mono text-xs">
        {isLoading ? (
          <Spinner label="Reading the log" />
        ) : visible.length === 0 ? (
          <EmptyState
            className="mt-8 border-charcoal-700"
            icon={<IconTerminal />}
            title="Nothing logged yet"
            detail="Start the build team and this fills as the agents work. Every line says which agent, which model, and which task it came from."
          />
        ) : (
          <ul className="space-y-1">
            {visible.map((line) => (
              <li key={line.key} className="flex items-start gap-3">
                <span className="shrink-0 text-muted">{clockTime(line.at)}</span>
                <span
                  className={cn(
                    "w-16 shrink-0",
                    line.level === "ERROR"
                      ? "text-danger-soft"
                      : line.level === "WARNING"
                        ? "text-canary-200"
                        : "text-muted",
                  )}
                >
                  {line.level}
                </span>
                {line.agent ? (
                  <span
                    className="w-28 shrink-0 truncate"
                    style={{ color: agentColour(line.agent) }}
                    title={line.agent}
                  >
                    {humanise(line.agent)}
                  </span>
                ) : (
                  <span className="w-28 shrink-0 text-muted">studio</span>
                )}
                <span className="min-w-0 flex-1 whitespace-pre-wrap text-mint-200">{line.message}</span>
                {line.task ? <span className="shrink-0 text-muted">{line.task}</span> : null}
                {line.live ? <Badge tone="muted">live</Badge> : null}
              </li>
            ))}
          </ul>
        )}
        <div ref={bottom} />
      </div>
    </div>
  );
}
