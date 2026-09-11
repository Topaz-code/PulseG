import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { useAgents, useCreateTask, useProviders } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Dialog } from "@/components/ui/feedback";
import { aggregateQuota } from "@/components/QuotaMeter";
import { IconClip, IconSend } from "@/components/Icons";

/**
 * The command bar: always at the bottom, on every screen.
 *
 * It is how work gets started. Three things happen here:
 *
 * * **Direction chips.** Genre, art style, perspective and Godot version default to Auto - the
 *   Planning Agent works them out from what you type - but a user who already knows can say so.
 * * **@agent routing.** `@programmer write the door script` creates a task for that agent
 *   directly, bypassing the Prompter, which is what a person wants when they know exactly who
 *   should do it. Those tasks are recorded as coming from a human, and they still pass the
 *   Auditor and the human gate like every other task.
 * * **The build button.** One canary-yellow action with the live free-tier headroom next to it,
 *   so nobody starts a long run on an allowance that is nearly gone.
 */
const CHIPS = [
  { key: "genre", label: "Genre", options: ["Auto", "Platformer", "Top-down Adventure", "Roguelike", "Puzzle", "Shooter", "RPG", "Farming Sim"] },
  { key: "artStyle", label: "Art Style", options: ["Auto", "Pixel Art", "Vector Flat", "Hand-painted", "Low-poly 2D", "Minimal Mono"] },
  { key: "perspective", label: "Perspective", options: ["Auto", "Side-on", "Top-down", "Isometric", "Front-facing"] },
  { key: "godotVersion", label: "Godot Ver", options: ["Auto", "4.4", "4.3", "4.2"] },
] as const;

export function CommandBar() {
  const draft = useStudio((state) => state.draft);
  const setDraft = useStudio((state) => state.setDraft);
  const meta = useStudio((state) => state.draftMeta);
  const setMeta = useStudio((state) => state.setDraftMeta);
  const showToast = useStudio((state) => state.showToast);
  const { data: agents } = useAgents();
  const { data: providers } = useProviders();
  const createTask = useCreateTask();
  const [referenceOpen, setReferenceOpen] = useState(false);
  const [referenceUrl, setReferenceUrl] = useState("");
  const [referenceNote, setReferenceNote] = useState("");

  const quota = useMemo(() => aggregateQuota(providers?.providers ?? []), [providers]);

  /** `@programmer do the thing` -> that agent, otherwise the Prompter when the project starts. */
  const parsed = useMemo(() => {
    const match = draft.match(/^\s*@([a-z_]+)\s+([\s\S]+)$/i);
    if (!match) return { agentId: "", body: draft.trim() };
    return { agentId: (match[1] ?? "").toLowerCase(), body: (match[2] ?? "").trim() };
  }, [draft]);

  const known = (agents?.agents ?? []).map((agent) => agent.agent_id);
  const unknownAgent = parsed.agentId && known.length > 0 && !known.includes(parsed.agentId);
  const canSend = parsed.body.length >= 8 && !unknownAgent && !createTask.isPending;

  const send = () => {
    if (!canSend) return;
    const instruction = [
      parsed.body,
      meta.genre !== "Auto" ? `Genre: ${meta.genre}.` : "",
      meta.artStyle !== "Auto" ? `Art style: ${meta.artStyle}.` : "",
      meta.perspective !== "Auto" ? `Perspective: ${meta.perspective}.` : "",
      meta.godotVersion !== "Auto" ? `Target Godot ${meta.godotVersion}.` : "",
      meta.reference ? `Reference material: ${meta.reference}.` : "",
    ]
      .filter(Boolean)
      .join(" ");

    createTask.mutate(
      {
        instruction,
        assigned_to: parsed.agentId || "planning_agent",
        created_by: "human_direct",
        kind: parsed.agentId ? "implementation" : "planning",
      },
      {
        onSuccess: (result) => {
          setDraft("");
          showToast({
            title: parsed.agentId ? `${parsed.agentId} has the task` : "Added to the design session",
            body: `Task ${result.task.task_id} is on the board.`,
            tone: "success",
          });
        },
        onError: (error: Error) =>
          showToast({ title: "That did not send", body: error.message, tone: "danger" }),
      },
    );
  };

  return (
    <>
      <div className="shrink-0 border-t border-app-border bg-app-surface px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          {CHIPS.map((chip) => (
            <label key={chip.key} className="flex items-center gap-2">
              <span className="text-xs text-muted">{chip.label}</span>
              <select
                value={meta[chip.key]}
                onChange={(event) => setMeta({ [chip.key]: event.target.value })}
                aria-label={chip.label}
                className={cn(
                  "rounded-sm border border-app-border bg-charcoal-800 px-2 py-1 text-xs text-mint-100",
                  "transition-colors duration-fast hover:border-surface-500",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                )}
              >
                {chip.options.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          ))}

          <Button variant="ghost" size="sm" onClick={() => setReferenceOpen(true)}>
            <IconClip size={14} />
            Reference
          </Button>

          {meta.reference ? (
            <Badge tone="muted" className="max-w-[240px] truncate">
              {meta.reference}
            </Badge>
          ) : null}
        </div>

        <div className="flex items-end gap-3">
          <div className="flex-1">
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              aria-label="Instruction for the team"
              placeholder="Describe what to build, or type @programmer and tell one agent exactly what you want"
              className="h-11"
              disabled={createTask.isPending}
            />
            {unknownAgent ? (
              <p className="text-xs text-danger-soft mt-1">
                There is no agent called {parsed.agentId}. Available: {known.slice(0, 5).join(", ")} and{" "}
                {Math.max(0, known.length - 5)} more.
              </p>
            ) : null}
          </div>

          <div className="text-right">
            <p className="text-xs text-muted">
              {quota.total > 0 ? `${100 - quota.percent}% free allowance left today` : "No keys added yet"}
            </p>
            <Button
              variant="primary"
              size="lg"
              className="mt-1 min-w-[132px]"
              onClick={send}
              disabled={!canSend}
              loading={createTask.isPending}
              data-primary-action
            >
              <IconSend size={16} />
              BUILD / SEND
            </Button>
          </div>
        </div>
      </div>

      <Dialog
        open={referenceOpen}
        onOpenChange={setReferenceOpen}
        title="Add reference material"
        detail="A link, a game, a film, a piece of art. The designers read it before they write the design."
        width="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setReferenceOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={referenceNote.trim().length < 3 && referenceUrl.trim().length < 3}
              onClick={() => {
                const value = [referenceUrl.trim(), referenceNote.trim()].filter(Boolean).join(" - ");
                setMeta({ reference: value });
                setReferenceOpen(false);
              }}
            >
              Use this reference
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Input
            value={referenceUrl}
            onChange={(event) => setReferenceUrl(event.target.value)}
            placeholder="https://... or a game name"
            aria-label="Reference link"
          />
          <Input
            value={referenceNote}
            onChange={(event) => setReferenceNote(event.target.value)}
            placeholder="What should we take from it? (for example: the mood of the night levels)"
            aria-label="Reference note"
          />
        </div>
      </Dialog>
    </>
  );
}
