import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { cn, humanise } from "@/lib/utils";
import { useAnswerQuestion, useConfirmDesign, useHandoff, usePlanning, useSeedTasks } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { EmptyState, Progress, ScrollArea, Spinner } from "@/components/ui/feedback";
import { MarkdownView } from "@/components/MarkdownView";
import { IconAlert, IconCheck, IconChevronRight, IconKey, IconSend, IconSparkle } from "@/components/Icons";

/**
 * The planning rail: the design conversation that has to finish before the build team starts.
 *
 * This is the human end of the /grillme intake. The team will not begin work until every
 * character, mechanic and art decision is pinned down, so this rail is where a user turns "a
 * platformer about a lighthouse" into something twelve agents can actually build. The gate is
 * visible at all times - the checklist and the blockers are the same data the backend enforces,
 * so the button never fails for a reason the screen did not already show.
 */
export function ContextRail() {
  const { data: planning, isLoading } = usePlanning();
  const answer = useAnswerQuestion();
  const handoff = useHandoff();
  const confirm = useConfirmDesign();
  const seed = useSeedTasks();
  const showToast = useStudio((state) => state.showToast);
  const navigate = useNavigate();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [confirmedText, setConfirmedText] = useState("");
  const [draft, setDraft] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="Reading the design notes" />
      </div>
    );
  }

  if (!planning) {
    return (
      <EmptyState
        className="m-4"
        title="No design session yet"
        detail="Open a project and describe the game you want. Questions appear here one small batch at a time."
      />
    );
  }

  const ledger = planning.ledger;
  const steps = [
    { key: "mechanics_complete", label: "Core mechanics have rules" },
    { key: "characters_complete", label: "Every character is defined" },
    { key: "art_complete", label: "Art direction is set" },
    { key: "scope_complete", label: "Scope is decided" },
    { key: "tech_complete", label: "Engine version is known" },
  ] as const;

  const openSettings = () => navigate("/settings");

  const submitAnswer = (question: string) => {
    const value = (answers[question] ?? "").trim();
    if (!value) return;
    answer.mutate(
      { question, answer: value },
      {
        onSuccess: () => {
          setAnswers((previous) => ({ ...previous, [question]: "" }));
        },
        onError: (error: Error) => showToast({ title: "That answer was not saved", body: error.message, tone: "danger" }),
      },
    );
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-app-border px-4 py-3">
        <div className="flex items-center gap-2">
          <IconSparkle size={16} className="text-canary-200" />
          <span className="text-sm font-semibold text-mint-100">Planning</span>
        </div>
        <Badge tone={planning.confirmed ? "success" : planning.allowed ? "accent" : "muted"}>
          {planning.confirmed ? "Design agreed" : planning.allowed ? "Ready to send" : "Still asking"}
        </Badge>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-6 px-4 py-4">
          <section>
            <h3 className="text-xs uppercase tracking-wide text-muted mb-2">What we know</h3>
            <div className="space-y-2 text-sm text-mint-200">
              <p>
                <span className="text-muted">Genre: </span>
                {humanise(planning.genre) || "not decided"}
              </p>
              <p>
                <span className="text-muted">Art: </span>
                {planning.art_direction || "not described yet"}
              </p>
              <p>
                <span className="text-muted">Scope: </span>
                {planning.level_count ? `${planning.level_count} levels` : "not decided"}
                {planning.play_length_minutes ? `, about ${planning.play_length_minutes} minutes` : ""}
              </p>
            </div>
          </section>

          <section>
            <div className="flex items-center justify-between gap-2 mb-2">
              <h3 className="text-xs uppercase tracking-wide text-muted">Design checklist</h3>
              <span className="text-xs text-muted">{Math.round(ledger.completion * 100)}%</span>
            </div>
            <Progress value={ledger.completion * 100} label="" className="mb-3" />
            <ul className="space-y-2">
              {steps.map((step) => {
                const done = Boolean(ledger[step.key]);
                return (
                  <li key={step.key} className="flex items-start gap-2 text-sm">
                    <span
                      className={cn(
                        "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-sm",
                        done ? "bg-teal-500 text-charcoal-900" : "border border-app-border",
                      )}
                    >
                      {done ? <IconCheck size={12} /> : null}
                    </span>
                    <span className={done ? "text-muted line-through" : "text-mint-200"}>{step.label}</span>
                  </li>
                );
              })}
            </ul>
          </section>

          <ExtractionNote extraction={planning.extraction} onOpenSettings={openSettings} />

          {planning.questions.length > 0 && !planning.confirmed ? (
            <section>
              <h3 className="text-xs uppercase tracking-wide text-muted mb-2">
                Questions for you ({planning.questions.length})
              </h3>
              <div className="space-y-4">
                {planning.questions.map((question) => (
                  <div key={question.index} className="rounded-md border border-app-border bg-charcoal-800 p-3">
                    <p className="text-xs text-muted mb-1">{humanise(question.theme)}</p>
                    <p className="text-sm text-mint-100 mb-3">{question.text}</p>
                    <div className="flex items-end gap-2">
                      <Input
                        value={answers[question.index] ?? ""}
                        onChange={(event) =>
                          setAnswers((previous) => ({ ...previous, [question.index]: event.target.value }))
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Enter") submitAnswer(question.index);
                        }}
                        placeholder="Type your answer"
                        aria-label={question.text}
                        disabled={answer.isPending}
                      />
                      <Button
                        variant="secondary"
                        size="icon"
                        onClick={() => submitAnswer(question.index)}
                        aria-label="Send this answer"
                        loading={answer.isPending}
                      >
                        <IconSend size={16} />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {!planning.allowed && planning.blockers.length > 0 ? (
            <section>
              <h3 className="text-xs uppercase tracking-wide text-muted mb-2">Before the team can start</h3>
              <ul className="space-y-2">
                {planning.blockers.map((blocker) => (
                  <li key={blocker} className="flex items-start gap-2 text-sm text-mint-200">
                    <IconChevronRight size={14} className="mt-0.5 shrink-0 text-canary-400" />
                    {blocker}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {draft ? (
            <section>
              <h3 className="text-xs uppercase tracking-wide text-muted mb-2">Have a read before we build</h3>
              <div className="rounded-md border border-app-border bg-charcoal-800 p-3">
                <MarkdownView content={draft} />
              </div>
              <Textarea
                className="mt-3"
                value={confirmedText}
                onChange={(event) => setConfirmedText(event.target.value)}
                placeholder="Anything you want changed? Say it here, or type 'looks right' to lock the design."
                aria-label="Design confirmation"
              />
              <Button
                variant="primary"
                className="mt-3 w-full"
                disabled={confirmedText.trim().length < 4 || confirm.isPending}
                loading={confirm.isPending}
                data-primary-action
                onClick={() =>
                  confirm.mutate(
                    { confirmed_text: confirmedText },
                    {
                      onSuccess: () => {
                        showToast({ title: "Design locked in. Phase 0 starts now.", tone: "success" });
                        setDraft(null);
                        seed.mutate();
                      },
                      onError: (error: Error) =>
                        showToast({ title: "Could not lock the design", body: error.message, tone: "danger" }),
                    },
                  )
                }
              >
                Confirm and start building
              </Button>
            </section>
          ) : null}

          {planning.chat.length > 0 ? (
            <section>
              <h3 className="text-xs uppercase tracking-wide text-muted mb-2">Conversation</h3>
              <div className="space-y-3">
                {planning.chat.slice(-12).map((message) => (
                  <div
                    key={message.message_id}
                    className={cn(
                      "rounded-md px-3 py-2 text-sm",
                      message.role === "human"
                        ? "bg-charcoal-800 text-mint-100 ml-6"
                        : "bg-app-surface text-mint-200 mr-6",
                    )}
                  >
                    <p className="text-xs text-muted mb-1">
                      {message.role === "human" ? "You" : humanise(message.agent_id ?? "Planning Agent")}
                    </p>
                    <p className="whitespace-pre-wrap">{message.content}</p>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </ScrollArea>

      <div className="border-t border-app-border p-3">
        <Button
          variant="primary"
          className="w-full"
          disabled={!planning.allowed || planning.confirmed}
          loading={handoff.isPending}
          onClick={() =>
            handoff.mutate(undefined, {
              onSuccess: (result) => setDraft(result.draft_gdd),
              onError: (error: Error) =>
                showToast({ title: "Not ready to send yet", body: error.message, tone: "danger" }),
            })
          }
        >
          Send to Build Team
        </Button>
        <p className="text-xs text-muted mt-2">
          The team starts when you confirm the design summary. Nothing is built before that.
        </p>
      </div>
    </div>
  );
}

/**
 * What the last answer did, in one line.
 *
 * The intake reads prose into structured fields through a model, so an answer can land with nothing
 * extracted - no key configured, a provider outage, or text with nothing usable in it. In every one
 * of those cases the checklist above simply does not move, which looks like the studio ignored the
 * human. This says which of them happened and what to do about it. Nothing is shown before the
 * first answer, and nothing is shown when the read worked and changed nothing, because "read
 * successfully, nothing new" is not worth a line of screen.
 */
function ExtractionNote({
  extraction,
  onOpenSettings,
}: {
  extraction?: { ok: boolean; reason?: string; changed?: string[]; needs_key?: boolean; at?: string };
  onOpenSettings: () => void;
}) {
  if (!extraction || (!extraction.ok && !extraction.reason)) return null;

  if (extraction.ok) {
    const changed = extraction.changed ?? [];
    if (changed.length === 0) return null;
    return (
      <section className="rounded-md border border-app-border bg-charcoal-800 px-3 py-2">
        <p className="text-xs text-muted">Read from your last answer</p>
        <p className="text-sm text-mint-200">{changed.map((field) => humanise(field)).join(", ")}</p>
      </section>
    );
  }

  return (
    <section className="rounded-md border border-canary-700 bg-charcoal-800 px-3 py-2">
      <p className="flex items-center gap-2 text-xs text-canary-300">
        {extraction.needs_key ? <IconKey size={14} /> : <IconAlert size={14} />}
        Nothing was read from your last answer
      </p>
      <p className="mt-1 text-sm text-mint-200">
        {extraction.reason || "The planning model did not answer."}
      </p>
      {extraction.needs_key ? (
        <Button variant="secondary" size="sm" className="mt-2" onClick={onOpenSettings}>
          Add a provider key
        </Button>
      ) : null}
    </section>
  );
}
