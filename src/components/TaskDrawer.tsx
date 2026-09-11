import { useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cn, baseName, humanise, relativeTime } from "@/lib/utils";
import { useCommentOnTask, useDecideTask, useRetryTask, useTask } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import type { Task } from "@/lib/types";
import { AgentChip, Badge, StatusPill } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { ScrollArea, Spinner } from "@/components/ui/feedback";
import { MarkdownView } from "@/components/MarkdownView";
import { IconAlert, IconCamera, IconCheck, IconClose, IconRefresh, IconSend } from "@/components/Icons";

/**
 * The review drawer: everything about one task in one place, and the three decisions a person can
 * make about it.
 *
 * This is the human gate. Nothing reaches `APPROVED` - and therefore nothing gets committed -
 * without one of these buttons. The drawer is built so the decision is easy: the instruction at
 * the top, what the agent actually produced next, the Auditor's verdict and reasoning after that,
 * and the screenshots beside them. Approve, send it back with a note, or overrule the Auditor.
 */
export function TaskDrawer() {
  const openTaskId = useStudio((state) => state.openTaskId);
  const closeTask = useStudio((state) => state.closeTask);
  const showToast = useStudio((state) => state.showToast);
  const { data, isLoading, error } = useTask(openTaskId);
  const decide = useDecideTask();
  const retry = useRetryTask();
  const comment = useCommentOnTask();
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");

  const task = data as (Task & { audit_summary?: string; blocked_by?: string[]; dependents?: string[] }) | undefined;
  const open = Boolean(openTaskId);

  const act = (decision: "approve" | "reject" | "override") => {
    if (!task) return;
    if (decision === "reject" && note.trim().length < 4) {
      showToast({ title: "A note is needed", body: "Say what to change so the agent can fix it.", tone: "danger" });
      return;
    }
    if (decision === "override" && note.trim().length < 4) {
      showToast({
        title: "A reason is needed",
        body: "Overruling the Auditor is recorded with your note.",
        tone: "danger",
      });
      return;
    }
    decide.mutate(
      { taskId: task.task_id, decision, note },
      {
        onSuccess: () => {
          setNote("");
          showToast({
            title:
              decision === "approve"
                ? "Approved and committed"
                : decision === "override"
                  ? "Approved against the Auditor's call"
                  : "Sent back with your note",
            body:
              decision === "reject"
                ? "The agent picks it up again with the note attached."
                : "The next tasks that were waiting on this one can start.",
            tone: decision === "reject" ? "info" : "success",
          });
          closeTask();
        },
        onError: (failure: Error) =>
          showToast({ title: "That decision was refused", body: failure.message, tone: "danger" }),
      },
    );
  };

  const verdict = task?.auditor_verdict;
  const flagged = Object.entries(verdict?.skill_flags ?? {}).flatMap(([skill, notes]) =>
    (notes ?? []).map((text) => `${humanise(skill)}: ${text}`),
  );

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => (next ? undefined : closeTask())}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-charcoal-900/70 animate-fade-in" />
        <DialogPrimitive.Content
          className="fixed right-0 top-0 z-50 flex h-full w-full max-w-2xl flex-col border-l border-app-border bg-app-surface animate-slide-in"
          aria-describedby={undefined}
        >
          <div className="flex items-start justify-between gap-4 border-b border-app-border px-6 py-4">
            <div className="min-w-0">
              {task ? (
                <>
                  <div className="flex items-center gap-2">
                    <DialogPrimitive.Title className="text-base font-semibold text-mint-100 truncate">
                      {task.title || task.instruction.slice(0, 60)}
                    </DialogPrimitive.Title>
                    <StatusPill status={task.status} />
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                    <span>{task.task_id}</span>
                    <span aria-hidden>|</span>
                    <AgentChip agentId={task.assigned_to} label={humanise(task.assigned_to)} />
                    <span aria-hidden>|</span>
                    <span>Phase {task.phase}</span>
                    <span aria-hidden>|</span>
                    <span>created {relativeTime(task.created_at)}</span>
                    {task.retry_count > 0 ? (
                      <>
                        <span aria-hidden>|</span>
                        <span>{task.retry_count} retries</span>
                      </>
                    ) : null}
                  </div>
                </>
              ) : (
                <DialogPrimitive.Title className="text-base font-semibold text-mint-100">
                  Task
                </DialogPrimitive.Title>
              )}
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close the task">
                <IconClose />
              </Button>
            </DialogPrimitive.Close>
          </div>

          <ScrollArea className="flex-1">
            {isLoading ? (
              <div className="p-6">
                <Spinner label="Loading the task" />
              </div>
            ) : error || !task ? (
              <div className="p-6">
                <p className="text-sm text-danger-soft">
                  {error instanceof Error ? error.message : "This task is no longer available."}
                </p>
              </div>
            ) : (
              <div className="space-y-6 px-6 py-5">
                <section>
                  <h3 className="text-xs uppercase tracking-wide text-muted mb-2">What was asked</h3>
                  <p className="text-sm text-mint-100 whitespace-pre-wrap">{task.instruction}</p>
                  {task.expected_outputs.length > 0 ? (
                    <ul className="mt-3 space-y-1">
                      {task.expected_outputs.map((item) => (
                        <li key={item} className="text-xs text-muted font-mono">
                          {item}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </section>

                {task.output ? (
                  <section>
                    <h3 className="text-xs uppercase tracking-wide text-muted mb-2">What came back</h3>
                    <div className="rounded-md border border-app-border bg-charcoal-800 p-4 max-h-80 overflow-y-auto">
                      <MarkdownView content={task.output} />
                    </div>
                  </section>
                ) : null}

                {task.artifacts.length > 0 ? (
                  <section>
                    <h3 className="text-xs uppercase tracking-wide text-muted mb-2">
                      Files written ({task.artifacts.length})
                    </h3>
                    <ul className="space-y-1">
                      {task.artifacts.map((path) => (
                        <li key={path} className="text-xs font-mono text-teal-300">
                          {path}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {task.screenshots.length > 0 ? (
                  <section>
                    <h3 className="text-xs uppercase tracking-wide text-muted mb-2 flex items-center gap-2">
                      <IconCamera size={14} /> Screenshots
                    </h3>
                    <div className="flex flex-wrap gap-3">
                      {task.screenshots.map((shot) => (
                        <a
                          key={shot}
                          href={`/media/${shot}`}
                          target="_blank"
                          rel="noreferrer"
                          className="group block overflow-hidden rounded-md border border-app-border transition-colors duration-fast hover:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                        >
                          <img
                            src={`/media/${shot}`}
                            alt={`Screenshot from ${task.task_id}: ${baseName(shot)}`}
                            className="h-32 w-auto object-cover"
                            loading="lazy"
                          />
                        </a>
                      ))}
                    </div>
                  </section>
                ) : null}

                {verdict ? (
                  <section>
                    <h3 className="text-xs uppercase tracking-wide text-muted mb-2">The Auditor's read</h3>
                    <div
                      className={cn(
                        "rounded-md border p-4",
                        verdict.verdict === "APPROVED"
                          ? "border-teal-600 bg-charcoal-800"
                          : "border-canary-600 bg-charcoal-800",
                      )}
                    >
                      <div className="flex items-center gap-3">
                        <Badge tone={verdict.verdict === "APPROVED" ? "success" : "warning"}>{verdict.verdict}</Badge>
                        <span className="text-sm text-mint-200">Score {verdict.score}/10</span>
                      </div>
                      {task.audit_summary ? (
                        <p className="mt-3 text-sm text-mint-200 whitespace-pre-wrap">{task.audit_summary}</p>
                      ) : null}
                      {flagged.length > 0 ? (
                        <ul className="mt-3 space-y-1">
                          {flagged.map((line) => (
                            <li key={line} className="text-xs text-canary-200">
                              {line}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {verdict.fix_note ? (
                        <p className="mt-3 text-xs text-muted whitespace-pre-wrap">{verdict.fix_note}</p>
                      ) : null}
                    </div>
                  </section>
                ) : null}

                {task.errors.length > 0 ? (
                  <section>
                    <h3 className="text-xs uppercase tracking-wide text-muted mb-2 flex items-center gap-2">
                      <IconAlert size={14} /> Attempt history
                    </h3>
                    <ul className="space-y-2">
                      {task.errors.slice(-6).map((line, index) => (
                        <li key={`${index}-${line}`} className="rounded-md bg-charcoal-800 px-3 py-2 text-xs text-mint-200">
                          {line}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {task.human_decision ? (
                  <section>
                    <h3 className="text-xs uppercase tracking-wide text-muted mb-2">Your decision</h3>
                    <p className="text-sm text-mint-200">
                      {task.human_decision.decision}
                      {task.human_decision.overrode_auditor ? " (overruling the Auditor)" : ""} -{" "}
                      {relativeTime(task.human_decision.decided_at)}
                    </p>
                    {task.human_decision.note ? (
                      <p className="text-xs text-muted mt-1">{task.human_decision.note}</p>
                    ) : null}
                  </section>
                ) : null}

                <section>
                  <h3 className="text-xs uppercase tracking-wide text-muted mb-2">Notes on this task</h3>
                  {task.comments.length === 0 ? (
                    <p className="text-sm text-muted">
                      No notes yet. Notes stay with the task, so the agent sees them on its next attempt.
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {task.comments.map((entry) => (
                        <li key={entry.message_id} className="rounded-md bg-charcoal-800 px-3 py-2">
                          <p className="text-xs text-muted mb-1">
                            {entry.role === "human" ? "You" : humanise(entry.agent_id ?? entry.role)} -{" "}
                            {relativeTime(entry.created_at)}
                          </p>
                          <p className="text-sm text-mint-100 whitespace-pre-wrap">{entry.content}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="mt-3 flex items-end gap-2">
                    <Textarea
                      value={message}
                      onChange={(event) => setMessage(event.target.value)}
                      placeholder="Add a note for the next attempt"
                      aria-label="Note on this task"
                      className="min-h-[64px]"
                    />
                    <Button
                      variant="secondary"
                      size="icon"
                      aria-label="Save the note"
                      loading={comment.isPending}
                      disabled={message.trim().length < 2}
                      onClick={() =>
                        comment.mutate(
                          { taskId: task.task_id, message: message.trim() },
                          {
                            onSuccess: () => {
                              setMessage("");
                              showToast({ title: "Note added", tone: "success" });
                            },
                          },
                        )
                      }
                    >
                      <IconSend size={16} />
                    </Button>
                  </div>
                </section>

                {task.status === "NEEDS_INTERVENTION" ? (
                  <section className="rounded-md border border-danger-700 bg-charcoal-800 p-4">
                    <h3 className="text-sm font-medium text-mint-100">This task is paused, not lost</h3>
                    <p className="text-xs text-muted mt-1">
                      Every provider in its chain failed. Add a key in Settings, change the chain, or retry it now -
                      the work it already produced is still here.
                    </p>
                    <Button
                      variant="secondary"
                      className="mt-3"
                      loading={retry.isPending}
                      onClick={() =>
                        retry.mutate(
                          { taskId: task.task_id },
                          { onSuccess: () => showToast({ title: "Back in the queue", tone: "success" }) },
                        )
                      }
                    >
                      <IconRefresh size={16} />
                      Try again now
                    </Button>
                  </section>
                ) : null}
              </div>
            )}
          </ScrollArea>

          {task && task.status === "NEEDS_HUMAN_REVIEW" ? (
            <div className="border-t border-app-border px-6 py-4">
              <Textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="A note for the agent, or your reason for overruling the Auditor"
                aria-label="Decision note"
                className="mb-3 min-h-[64px]"
              />
              <div className="flex items-center justify-end gap-3">
                <Button
                  variant="danger"
                  onClick={() => act("reject")}
                  loading={decide.isPending}
                  disabled={note.trim().length < 4}
                >
                  Send back with note
                </Button>
                {verdict?.verdict === "DECLINED" ? (
                  <Button
                    variant="secondary"
                    onClick={() => act("override")}
                    loading={decide.isPending}
                    disabled={note.trim().length < 4}
                  >
                    Approve anyway
                  </Button>
                ) : null}
                <Button
                  variant="primary"
                  onClick={() => act("approve")}
                  loading={decide.isPending}
                  data-primary-action
                >
                  <IconCheck size={16} />
                  Approve and commit
                </Button>
              </div>
            </div>
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
