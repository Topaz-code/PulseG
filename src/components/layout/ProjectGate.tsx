import { useState } from "react";
import { useCreateProject } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { FieldRow, Input, Textarea } from "@/components/ui/field";
import { EmptyState } from "@/components/ui/feedback";
import { IconFolder, IconSparkle } from "@/components/Icons";

/**
 * Shown when no project is open - which is the very first thing a new user sees, so it doubles as
 * the explanation of what this app is.
 *
 * "Start fresh" scaffolds a Godot project; "Connect an existing project" points at a folder that
 * already has one and reads it in. Both are one screen, because a person who already has a game
 * should not have to go through the same funnel as someone starting from nothing.
 */
export function ProjectGate() {
  const create = useCreateProject();
  const showToast = useStudio((state) => state.showToast);
  const openDialog = useStudio((state) => state.openDialog);
  const [mode, setMode] = useState<"fresh" | "existing">("fresh");
  const [name, setName] = useState("");
  const [concept, setConcept] = useState("");
  const [path, setPath] = useState("");

  const submit = () => {
    create.mutate(
      { name: name.trim(), mode, concept: concept.trim(), path: path.trim() },
      {
        onSuccess: () =>
          showToast({
            title: `${name.trim()} is ready`,
            body: mode === "fresh" ? "Discuss the design in the Planning rail." : "Existing work read in.",
            tone: "success",
          }),
        onError: (error: Error) => showToast({ title: "Could not open that project", body: error.message, tone: "danger" }),
      },
    );
  };

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col justify-center gap-8 px-8 py-12">
      <div>
        <p className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted">
          <IconSparkle size={14} className="text-canary-200" />
          PulseG Studio
        </p>
        <h1 className="mt-3 text-3xl font-semibold text-mint-100">Describe a game. Approve the work. Play it.</h1>
        <p className="mt-3 max-w-2xl text-sm text-mint-200">
          Twelve agents design, build and test a 2D Godot game with you at every gate. Nothing is committed until you
          say so. Start something new, or point the studio at a game you have already begun.
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <div className="flex gap-2">
            {(["fresh", "existing"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setMode(option)}
                className={cn(
                  "flex-1 rounded-md border px-4 py-3 text-left transition-colors duration-fast",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                  mode === option
                    ? "border-accent bg-app-surface-raised"
                    : "border-app-border bg-app-surface hover:border-surface-500",
                )}
              >
                <span className="block text-sm text-mint-100">
                  {option === "fresh" ? "Start fresh" : "Connect an existing project"}
                </span>
                <span className="mt-1 block text-xs text-muted">
                  {option === "fresh"
                    ? "We scaffold a new Godot project for you."
                    : "Point at a folder that already has a Godot project."}
                </span>
              </button>
            ))}
          </div>

          <FieldRow label="Project name">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Lighthouse Keeper"
              aria-label="Project name"
            />
          </FieldRow>

          {mode === "fresh" ? (
            <FieldRow label="Your idea, in your own words" hint="Rough is fine. The design questions come next.">
              <Textarea
                value={concept}
                onChange={(event) => setConcept(event.target.value)}
                placeholder="A cosy platformer about a lighthouse keeper who guides ships home through fog."
                aria-label="Game idea"
                className="min-h-[96px]"
              />
            </FieldRow>
          ) : (
            <FieldRow label="Folder that contains the project" hint="We read what is there before any new work starts.">
              <Input
                value={path}
                onChange={(event) => setPath(event.target.value)}
                placeholder="C:\Users\you\Documents\my-game"
                aria-label="Existing project folder"
              />
            </FieldRow>
          )}

          <Button
            variant="primary"
            size="lg"
            data-primary-action
            loading={create.isPending}
            disabled={name.trim().length < 2 || (mode === "existing" && path.trim().length < 2)}
            onClick={submit}
          >
            <IconFolder size={16} />
            {mode === "fresh" ? "Create the project" : "Open and read it in"}
          </Button>
        </div>

        <EmptyState
          className="h-full"
          title="What happens next"
          detail="The Planning Agent asks a few short questions about your game. When the design is complete, you send it to the build team and work starts appearing on the board."
          action={
            <Button variant="ghost" onClick={() => openDialog("setup")}>
              Reopen setup
            </Button>
          }
        />
      </div>
    </div>
  );
}
