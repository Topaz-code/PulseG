import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { cn, humanise } from "@/lib/utils";
import {
  useAgent,
  useAgentContextPreview,
  useAgentHistory,
  useAgentUsage,
  useAgents,
  useProviders,
  useReloadAgents,
  useUpdateChain,
  useUpdateAgent,
} from "@/lib/queries";
import { useStudio } from "@/lib/store";
import type { ChainEntry } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, Switch } from "@/components/ui/field";
import { EmptyState, Spinner } from "@/components/ui/feedback";
import { IconAlert, IconChevronRight, IconRefresh, IconRobot, IconSparkle } from "@/components/Icons";

/**
 * One agent, in full: what it is for, which model it uses, what it has done, and what it is told.
 *
 * The model chain is the important part. The spec's zero-abandonment rule means every agent has a
 * primary and two fallbacks, and the order is edited *here*, written straight into agents.yaml -
 * no code change, no restart of the app. The same file is where a twelfth agent would be added,
 * which is why the panel below shows the raw entry as well.
 *
 * Reordering is deliberately click-based rather than drag-only: dragging is nice, but a user who
 * cannot drag still needs to be able to move FALLBACK 2 to the top.
 */
const SLOT_LABELS = ["Primary", "Fallback 1", "Fallback 2"];

export function AgentDetail() {
  const { agentId = "" } = useParams();
  const navigate = useNavigate();
  const { data: agent, isLoading, error } = useAgent(agentId);
  const { data: agents } = useAgents();
  const { data: providers } = useProviders();
  const { data: history } = useAgentHistory(agentId);
  const { data: usage } = useAgentUsage(agentId);
  const { data: contextPreview } = useAgentContextPreview(agentId);
  const update = useUpdateAgent();
  const chain = useUpdateChain();
  const reload = useReloadAgents();
  const showToast = useStudio((state) => state.showToast);
  const [draftChain, setDraftChain] = useState<ChainEntry[]>([]);
  const [promptOpen, setPromptOpen] = useState(false);
  const [prompt, setPrompt] = useState("");

  const raw = agent as
    | ((typeof agent) & { id?: string; chain_entries?: ChainEntry[]; system_prompt?: string })
    | undefined;
  // `/api/agents/{id}` answers with the raw agents.yaml entry (`id`), while the list endpoint
  // answers with the resolved roster (`agent_id`). Accepting both here keeps one view working
  // against either payload instead of quietly breaking when the shape differs.
  const id = raw?.agent_id ?? raw?.id ?? agentId;

  useEffect(() => {
    if (raw?.chain_entries) setDraftChain(raw.chain_entries);
    else if (raw?.chain) setDraftChain(raw.chain);
    setPrompt(raw?.system_prompt ?? "");
  }, [raw]);

  const modelOptions = useMemo(() => {
    const catalogue = (agents as { model_catalogue?: Record<string, string[]> } | undefined)?.model_catalogue ?? {};
    return Object.entries(catalogue).flatMap(([providerId, models]) =>
      models.map((model) => ({ providerId, model })),
    );
  }, [agents]);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="Reading the agent" />
      </div>
    );
  }

  if (error || !raw) {
    return (
      <div className="p-6">
        <EmptyState
          icon={<IconRobot />}
          title="That agent is not in the roster"
          detail={error instanceof Error ? error.message : "Open Settings > Agents to see the full list."}
        />
      </div>
    );
  }

  const configured = (providers?.providers ?? []).filter((provider) => provider.configured).map((p) => p.id);

  const move = (index: number, direction: -1 | 1) => {
    const next = [...draftChain];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setDraftChain(next);
  };

  const saveChain = () => {
    const [primary, ...fallbacks] = draftChain;
    if (!primary) return;
    chain.mutate(
      {
        agentId: id,
        primary: { provider: primary.provider, model: primary.model },
        fallbacks: fallbacks.map((entry) => ({ provider: entry.provider, model: entry.model })),
      },
      {
        onSuccess: () => showToast({ title: "Chain saved", body: "agents.yaml updated. No restart needed.", tone: "success" }),
        onError: (failure: Error) => showToast({ title: "Could not save the chain", body: failure.message, tone: "danger" }),
      },
    );
  };

  const dirty =
    JSON.stringify(draftChain.map((entry) => `${entry.provider}/${entry.model}`)) !==
    JSON.stringify((raw.chain_entries ?? raw.chain).map((entry) => `${entry.provider}/${entry.model}`));

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-5xl space-y-6">
        <button
          type="button"
          onClick={() => navigate("/settings")}
          className="flex items-center gap-1 text-xs text-muted transition-colors duration-fast hover:text-mint-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <IconChevronRight size={12} className="rotate-180" />
          All agents
        </button>

        <header className="flex flex-wrap items-start gap-4">
          <span
            aria-hidden
            className="mt-1 flex h-10 w-10 items-center justify-center rounded-lg"
            style={{ backgroundColor: `${raw.color ?? raw.colour}22`, color: raw.color ?? raw.colour }}
          >
            <IconRobot />
          </span>
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold text-mint-100">{raw.name}</h1>
            <p className="mt-1 text-sm text-mint-200">{raw.role}</p>
            <p className="mt-2 max-w-2xl text-xs text-muted">{raw.description}</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Badge tone={raw.enabled ? "success" : "muted"}>{raw.enabled ? "Working" : "Turned off"}</Badge>
            <Switch
              checked={raw.enabled}
              label=""
              onCheckedChange={(value) =>
                update.mutate(
                  { agentId: id, patch: { enabled: value } },
                  {
                    onSuccess: () =>
                      showToast({
                        title: value ? `${raw.name} is back on` : `${raw.name} is off`,
                        tone: "info",
                      }),
                  },
                )
              }
            />
          </div>
        </header>

        <section className="grid gap-4 sm:grid-cols-4">
          {[
            { label: "Finished", value: usage?.tasks ?? 0 },
            { label: "Attempts", value: usage?.attempts ?? 0 },
            { label: "Average verdict", value: usage?.average_score ?? "-" },
            { label: "Sent back", value: usage?.declines ?? 0 },
          ].map((stat) => (
            <div key={stat.label} className="rounded-lg border border-app-border bg-app-surface px-4 py-3">
              <p className="text-xs uppercase tracking-wide text-muted">{stat.label}</p>
              <p className="mt-1 text-xl font-semibold text-mint-100">{stat.value}</p>
            </div>
          ))}
        </section>

        <section className="rounded-lg border border-app-border bg-app-surface">
          <header className="flex items-center justify-between gap-3 border-b border-app-border px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-mint-100">Models, in order</h2>
              <p className="text-xs text-muted">
                If the primary fails, the next one takes over. If all three fail, the task pauses instead of
                disappearing.
              </p>
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" loading={reload.isPending} onClick={() => reload.mutate()}>
                <IconRefresh size={14} />
                Reload the file
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={!dirty || chain.isPending}
                loading={chain.isPending}
                onClick={saveChain}
                data-primary-action
                className="disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Save the order
              </Button>
            </div>
          </header>

          <ol className="divide-y divide-app-border">
            {draftChain.map((entry, index) => (
              <li key={`${entry.slot}-${index}`} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs",
                    index === 0 ? "bg-accent text-accent-foreground" : "bg-surface-700 text-mint-200",
                  )}
                >
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-mint-100">
                    {SLOT_LABELS[index] ?? `Slot ${index + 1}`} - {humanise(entry.provider)}
                  </p>
                  <p className="text-xs text-muted">{entry.model || "no model chosen"}</p>
                </div>
                {entry.provider && !configured.includes(entry.provider) && entry.provider !== "demo" ? (
                  <Badge tone="warning">Key missing</Badge>
                ) : null}
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={index === 0}
                    aria-label="Move up"
                    onClick={() => move(index, -1)}
                  >
                    Up
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={index === draftChain.length - 1}
                    aria-label="Move down"
                    onClick={() => move(index, 1)}
                  >
                    Down
                  </Button>
                </div>
                <Select
                  aria-label={`Provider for slot ${index + 1}`}
                  value={`${entry.provider}|${entry.model}`}
                  onChange={(event) => {
                    const [providerId, model] = event.target.value.split("|");
                    setDraftChain((previous) =>
                      previous.map((row, rowIndex) =>
                        rowIndex === index ? { ...row, provider: providerId, model } : row,
                      ),
                    );
                  }}
                  className="w-56"
                >
                  <option value={`${entry.provider}|${entry.model}`}>
                    {entry.provider}/{entry.model}
                  </option>
                  {modelOptions.map((option) => (
                    <option key={`${option.providerId}|${option.model}`} value={`${option.providerId}|${option.model}`}>
                      {option.providerId} / {option.model}
                    </option>
                  ))}
                </Select>
              </li>
            ))}
          </ol>

          {draftChain.some((entry) => entry.provider && !configured.includes(entry.provider)) ? (
            <p className="flex items-start gap-2 border-t border-app-border px-4 py-3 text-xs text-canary-200">
              <IconAlert size={14} className="mt-1 shrink-0" />
              One or more slots use a provider with no key saved. The agent skips those and uses the next slot that
              has one.
            </p>
          ) : null}
        </section>

        <section className="rounded-lg border border-app-border bg-app-surface">
          <header className="flex items-center justify-between gap-3 border-b border-app-border px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-mint-100">What it is told</h2>
              <p className="text-xs text-muted">
                {contextPreview
                  ? `${contextPreview.stats.rendered_tokens} tokens prepared against a ${contextPreview.stats.budget_tokens} budget`
                  : "The brief it receives before every task."}
              </p>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setPromptOpen((open) => !open)}>
              <IconSparkle size={14} />
              {promptOpen ? "Hide" : "Show"} the full brief
            </Button>
          </header>
          {promptOpen ? (
            <div className="px-4 py-3">
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                aria-label="Agent brief"
                className="h-72 w-full rounded-md border border-app-border bg-charcoal-800 p-3 font-mono text-xs text-mint-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              />
              <div className="mt-2 flex justify-end">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    update.mutate(
                      { agentId: id, patch: { system_prompt: prompt } },
                      { onSuccess: () => showToast({ title: "Brief saved", tone: "success" }) },
                    )
                  }
                >
                  Save the brief
                </Button>
              </div>
            </div>
          ) : (
            <p className="px-4 py-3 text-xs text-muted">
              {raw.system_prompt?.slice(0, 240) ?? "No brief written."}
              {(raw.system_prompt?.length ?? 0) > 240 ? "..." : ""}
            </p>
          )}
        </section>

        <section className="rounded-lg border border-app-border bg-app-surface">
          <header className="border-b border-app-border px-4 py-3">
            <h2 className="text-sm font-semibold text-mint-100">Recent work</h2>
            <p className="text-xs text-muted">
              {history?.history?.length
                ? `${history.history.length} recorded runs, newest first.`
                : "Nothing yet. Runs appear here after this agent's first task."}
            </p>
          </header>
          <div className="px-4 py-3">
            {history?.history?.length ? (
              <ul className="space-y-2">
                {history.history.slice(0, 20).map((row) => (
                  <li key={row.task_id} className="flex flex-wrap items-center gap-3 text-xs">
                    <span className="font-mono text-teal-300">{row.task_id}</span>
                    <span className="text-mint-200">{humanise(row.status ?? "")}</span>
                    <span className="truncate text-muted" title={row.model ?? row.provider ?? ""}>
                      {humanise(row.provider ?? "")}
                      {row.model ? ` / ${row.model}` : ""}
                    </span>
                    {row.success === false ? <Badge tone="warning">Fell back</Badge> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">
                When this agent finishes a task, what it produced and how the Auditor scored it will show up here.
              </p>
            )}

            {history?.failure_patterns?.length ? (
              <div className="mt-4">
                <h3 className="mb-2 text-xs uppercase tracking-wide text-muted">What tends to go wrong</h3>
                <ul className="space-y-1">
                  {history.failure_patterns.map((pattern) => (
                    <li key={pattern} className="text-xs text-canary-200">
                      {pattern}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
