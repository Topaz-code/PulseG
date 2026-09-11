import { useNavigate } from "react-router-dom";
import { cn, humanise, relativeTime } from "@/lib/utils";
import {
  useActiveProject,
  useAgentStates,
  useBoard,
  useProviders,
  useReviewQueue,
  useNotifications,
  useRunControl,
} from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { AgentChip, Badge, StatusPill } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, Stat } from "@/components/ui/card";
import { EmptyState, Progress, Spinner } from "@/components/ui/feedback";
import { QuotaMeter } from "@/components/QuotaMeter";
import { IconAlert, IconCheck, IconPlay, IconSparkle, IconStep } from "@/components/Icons";

/**
 * The first screen: is anything waiting for me, what is the team doing, and is the free allowance
 * going to last.
 *
 * The order is deliberate. A person opens this app to answer three questions in that order, so the
 * review call to action is above the fold, the agent grid is next, and the provider meters - which
 * only matter occasionally - sit at the bottom right.
 */
export function Overview() {
  const navigate = useNavigate();
  const { data: active } = useActiveProject();
  const { data: agents } = useAgentStates();
  const { data: providers } = useProviders();
  const { data: review } = useReviewQueue();
  const { data: board } = useBoard();
  const { start, tick } = useRunControl();
  const openTask = useStudio((state) => state.openTask);
  const { data: notifications } = useNotifications();

  const project = active?.project;
  const stats = active?.overview.stats;
  const phase = active?.overview.phase;
  const runState = board?.run_state;

  if (!project) {
    return (
      <div className="p-8">
        <EmptyState
          title="No project open"
          detail="Create a project and the studio fills this screen with the team's work."
        />
      </div>
    );
  }

  const agentRows = agents?.agents ?? [];
  const waiting = review?.items ?? [];
  const paused = board?.counts?.NEEDS_INTERVENTION ?? 0;

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-mint-100">{project.name}</h1>
            <p className="mt-1 text-sm text-mint-200">
              {humanise(project.genre)} - {humanise(project.art_style)} - Godot {project.godot_version}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => tick.mutate()}
              loading={tick.isPending}
              aria-label="Run one step of the build"
            >
              <IconStep size={16} />
              Run one step
            </Button>
            <Button
              variant="primary"
              data-primary-action
              loading={start.isPending}
              className="disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={runState?.status === "running"}
              onClick={() => start.mutate()}
            >
              <IconPlay size={16} />
              {runState?.status === "running" ? "The team is working" : "Start the build team"}
            </Button>
          </div>
        </div>

        {waiting.length > 0 || paused > 0 ? (
          <Card className="border-accent">
            <CardHeader
              title={
                <span className="flex items-center gap-2">
                  <IconSparkle size={16} className="text-canary-200" />
                  {waiting.length > 0
                    ? `${waiting.length} finished task${waiting.length === 1 ? "" : "s"} waiting for your review`
                    : `${paused} paused task${paused === 1 ? "" : "s"}`}
                </span>
              }
              detail={
                waiting.length > 0
                  ? "Approve them to unlock the next steps and save the work to the project's history."
                  : "These paused instead of failing. Add a key or send them back in."
              }
              action={
                <Button variant="secondary" onClick={() => navigate("/board")}>
                  Open the board
                </Button>
              }
            />
            {waiting.length > 0 ? (
              <CardBody className="space-y-2">
                {waiting.slice(0, 4).map((task) => (
                  <button
                    key={task.task_id}
                    type="button"
                    onClick={() => openTask(task.task_id)}
                    className={cn(
                      "flex w-full items-center justify-between gap-4 rounded-md border border-app-border bg-charcoal-800 px-4 py-3 text-left",
                      "transition-colors duration-fast hover:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                    )}
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-mint-100">{task.title || task.instruction}</span>
                      <span className="mt-1 block text-xs text-muted">
                        {humanise(task.assigned_to)} - finished {relativeTime(task.finished_at || task.updated_at)}
                      </span>
                    </span>
                    <StatusPill status={task.status} />
                  </button>
                ))}
              </CardBody>
            ) : null}
          </Card>
        ) : null}

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardBody>
              <Stat label="Approved" value={stats?.tasks_approved ?? 0} tone="success" detail="saved in history" />
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <Stat label="Open work" value={stats?.tasks_open ?? 0} detail="in the queue or running" />
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <Stat
                label="Waiting on you"
                value={(stats?.tasks_needing_review ?? 0) + (stats?.tasks_paused ?? 0)}
                tone={stats?.tasks_needing_review ? "accent" : "default"}
              />
            </CardBody>
          </Card>
          <Card>
            <CardBody>
              <Stat
                label="Phase"
                value={`${phase?.phase ?? 0} of ${phase?.phases_total ?? 6}`}
                detail={`${phase?.approved ?? 0} of ${phase?.total ?? 0} tasks approved in this phase`}
              />
            </CardBody>
          </Card>
        </div>

        <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
          <Card>
            <CardHeader
              title="The team"
              detail="Twelve agents. A ring means that agent is working right now."
              action={
                runState?.status === "running" ? <Spinner label="Build running" /> : <Badge tone="muted">Idle</Badge>
              }
            />
            <CardBody>
              {agentRows.length === 0 ? (
                <EmptyState
                  title="Agents are loading"
                  detail="The roster is read from your agents file, which you can edit in Settings."
                />
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {agentRows.map((agent) => {
                    const colour = agent.colour ?? agent.color ?? "var(--slate-500)";
                    const working = agent.status === "working";
                    return (
                      <button
                        key={agent.agent_id}
                        type="button"
                        onClick={() => navigate(`/agents/${agent.agent_id}`)}
                        className={cn(
                          "rounded-md border bg-charcoal-800 px-3 py-3 text-left transition-colors duration-fast",
                          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                          working ? "border-teal-500" : "border-app-border hover:border-surface-500",
                        )}
                      >
                        <div className="flex items-center gap-2">
                          <span aria-hidden className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: colour }} />
                          <span className="truncate text-sm text-mint-100">{agent.name}</span>
                          {working ? <span className="ml-auto text-xs text-teal-400">working</span> : null}
                        </div>
                        <p className="mt-2 text-xs text-muted truncate">
                          {agent.current_task ? `On ${agent.current_task}` : humanise(agent.provider || "no model set")}
                        </p>
                        <p className="mt-1 text-xs text-muted">
                          {agent.tasks_completed ?? 0} finished
                          {agent.tasks_failed ? ` - ${agent.tasks_failed} sent back` : ""}
                        </p>
                      </button>
                    );
                  })}
                </div>
              )}
            </CardBody>
          </Card>

          <div className="space-y-6">
            <Card>
              <CardHeader title="What just happened" detail="Newest first, straight from the studio." />
              <CardBody className="space-y-3">
                {(notifications?.recent ?? []).length === 0 ? (
                  <p className="text-sm text-muted">
                    Nothing yet. Approvals, pauses and finished phases are recorded here.
                  </p>
                ) : (
                  (notifications?.recent ?? []).slice(0, 6).map((entry) => (
                    <div key={entry.event_id} className="flex items-start gap-3">
                      <span
                        className={cn(
                          "mt-1 h-2 w-2 shrink-0 rounded-full",
                          entry.kind === "needs_review"
                            ? "bg-accent"
                            : entry.kind === "needs_intervention"
                              ? "bg-danger-solid"
                              : entry.kind === "phase_complete"
                                ? "bg-teal-400"
                                : "bg-slate-400",
                        )}
                      />
                      <div className="min-w-0">
                        <p className="text-sm text-mint-100">{entry.title}</p>
                        <p className="text-xs text-muted">{relativeTime(entry.created_at)}</p>
                      </div>
                    </div>
                  ))
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Free allowance" detail="Today's headroom per provider." />
              <CardBody className="space-y-4">
                {(providers?.providers ?? []).filter((provider) => provider.configured).length === 0 ? (
                  <EmptyState
                    title="No keys added yet"
                    detail="Add a free key in Settings and the team can build. Everything else works without one."
                    action={
                      <Button variant="secondary" onClick={() => navigate("/settings")}>
                        Open settings
                      </Button>
                    }
                  />
                ) : (
                  (providers?.providers ?? [])
                    .filter((provider) => provider.configured)
                    .slice(0, 5)
                    .map((provider) => <QuotaMeter key={provider.id} provider={provider} compact />)
                )}
                {providers?.providers.some((provider) => provider.usage.in_cooldown) ? (
                  <p className="flex items-start gap-2 text-xs text-canary-200">
                    <IconAlert size={14} className="mt-1 shrink-0" />
                    One provider is cooling down after a rate limit. The team falls back to the next one in the chain
                    automatically.
                  </p>
                ) : null}
              </CardBody>
            </Card>
          </div>
        </div>

        <Card>
          <CardHeader
            title="Progress"
            detail={`Phase ${phase?.phase ?? 0}: ${phase?.approved ?? 0} of ${phase?.total ?? 0} tasks approved`}
            action={
              <Button variant="ghost" size="sm" onClick={() => navigate("/git")}>
                See the history
              </Button>
            }
          />
          <CardBody>
            <Progress
              value={phase?.percent ?? 0}
              label={phase?.percent ? `${phase.percent}% of this phase approved` : "Nothing approved in this phase yet"}
            />
            <div className="mt-4 flex flex-wrap gap-2">
              {["PENDING", "IN_PROGRESS", "NEEDS_HUMAN_REVIEW", "APPROVED", "NEEDS_INTERVENTION"].map((status) => (
                <span key={status} className="flex items-center gap-2">
                  <AgentChip
                    agentId={status}
                    label={`${humanise(status)}: ${board?.counts?.[status] ?? 0}`}
                    colour={
                      status === "APPROVED"
                        ? "var(--teal-500)"
                        : status === "NEEDS_HUMAN_REVIEW"
                          ? "var(--canary-500)"
                          : status === "NEEDS_INTERVENTION"
                            ? "var(--danger-500)"
                            : "var(--slate-600)"
                    }
                  />
                </span>
              ))}
            </div>
          </CardBody>
        </Card>

        {project.summary ? (
          <Card>
            <CardHeader title="Design summary" detail="The one section every agent is given." />
            <CardBody>
              <p className="text-sm text-mint-200 whitespace-pre-wrap">{project.summary}</p>
            </CardBody>
          </Card>
        ) : null}

        <p className="flex items-center gap-2 pb-2 text-xs text-muted">
          <IconCheck size={14} />
          Every task stops here for your approval before anything is saved. Nothing is committed without you.
        </p>
      </div>
    </div>
  );
}
