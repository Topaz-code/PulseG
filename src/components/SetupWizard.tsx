import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useFirstRun, useProviders, useSaveKey } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, Spinner } from "@/components/ui/feedback";
import { FieldRow, Input } from "@/components/ui/field";
import { IconCheck, IconKey, IconSparkle } from "@/components/Icons";

/**
 * First run, in four steps. The wizard exists because the app needs three things it cannot guess:
 * where Godot is, where projects should live, and who is committing the work.
 *
 * The API key step is optional and last, with the free providers listed first, because a new user
 * can legitimately start with none: the studio scaffolds the project, the design session works,
 * and only the build needs a model. Anything the user cannot finish now can be finished later in
 * Settings.
 */
const FREE_FIRST = ["groq", "google_ai_studio", "mistral", "openrouter"];

export function SetupWizard({ open, onFinished }: { open: boolean; onFinished: () => void }) {
  const { data: firstRun } = useFirstRun();
  const { data: providers } = useProviders();
  const saveKey = useSaveKey();
  const client = useQueryClient();
  const showToast = useStudio((state) => state.showToast);
  const [step, setStep] = useState(0);
  const [projectsRoot, setProjectsRoot] = useState("");
  const [godotPath, setGodotPath] = useState("");
  const [gitName, setGitName] = useState("");
  const [gitEmail, setGitEmail] = useState("");
  const [providerId, setProviderId] = useState("groq");
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    if (!firstRun) return;
    setProjectsRoot((current) => current || firstRun.projects_root || "");
    setGodotPath((current) => current || firstRun.godot_detected[0] || "");
    setGitName((current) => current || firstRun.git_identity.name || "");
    setGitEmail((current) => current || firstRun.git_identity.email || "");
  }, [firstRun]);

  const detect = useMutation({
    mutationFn: () => api.post<{ candidates: string[]; found: string }>("/api/settings/godot/detect"),
    onSuccess: (result) => {
      if (result.found) {
        setGodotPath(result.found);
        showToast({ title: "Found Godot", body: result.found, tone: "success" });
      } else {
        showToast({
          title: "Godot was not found automatically",
          body: "Download it from godotengine.org, then paste the path to the .exe here.",
          tone: "info",
        });
      }
    },
  });

  const finish = useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean; config: Record<string, unknown> }>("/api/system/setup", {
        projects_root: projectsRoot,
        godot_executable: godotPath,
        git_name: gitName,
        git_email: gitEmail,
      }),
    onSuccess: () => {
      showToast({ title: "PulseG Studio is ready", body: "Open a project to start.", tone: "success" });
      void client.invalidateQueries();
      onFinished();
    },
    onError: (error: Error) => showToast({ title: "Setup could not be saved", body: error.message, tone: "danger" }),
  });

  const steps = ["Where Godot lives", "Where games are saved", "Who commits the work", "A model to build with"];
  const catalogued = (providers?.providers ?? []).filter((provider) =>
    FREE_FIRST.includes(provider.id),
  );
  const selected = (providers?.providers ?? []).find((provider) => provider.id === providerId);

  return (
    <Dialog
      open={open}
      onOpenChange={() => undefined}
      title="Welcome to PulseG Studio"
      detail="Four short steps. Everything here can be changed later in Settings."
      width="lg"
      footer={
        <>
          {step > 0 ? (
            <Button variant="ghost" onClick={() => setStep((value) => value - 1)}>
              Back
            </Button>
          ) : null}
          {step < steps.length - 1 ? (
            <Button variant="primary" onClick={() => setStep((value) => value + 1)} data-primary-action>
              Continue
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={() => finish.mutate()}
              loading={finish.isPending}
              disabled={!projectsRoot}
              data-primary-action
            >
              <IconCheck size={16} />
              Finish setup
            </Button>
          )}
        </>
      }
    >
      <ol className="mb-6 flex flex-wrap items-center gap-3">
        {steps.map((label, index) => (
          <li key={label} className="flex items-center gap-2">
            <span
              className={cn(
                "flex h-6 w-6 items-center justify-center rounded-full text-xs",
                index < step
                  ? "bg-teal-500 text-charcoal-900"
                  : index === step
                    ? "bg-accent text-accent-foreground"
                    : "bg-surface-700 text-muted",
              )}
            >
              {index < step ? <IconCheck size={12} /> : index + 1}
            </span>
            <span className={index === step ? "text-sm text-mint-100" : "text-sm text-muted"}>{label}</span>
          </li>
        ))}
      </ol>

      {step === 0 ? (
        <div>
          <p className="text-sm text-mint-200 mb-4">
            PulseG Studio drives a Godot editor you install yourself, so your games stay open and editable outside
            this app. Godot 4.x is a free download from godotengine.org.
          </p>
          <FieldRow label="Path to Godot" hint="For example C:\Program Files\Godot\Godot_v4.4-stable_win64.exe">
            <Input
              value={godotPath}
              onChange={(event) => setGodotPath(event.target.value)}
              placeholder="Not set - you can add it later"
              aria-label="Godot executable path"
            />
          </FieldRow>
          <div className="flex items-center gap-3">
            <Button variant="secondary" onClick={() => detect.mutate()} loading={detect.isPending}>
              Find Godot on this computer
            </Button>
            {firstRun?.godot_configured ? <Badge tone="success">Already detected</Badge> : null}
          </div>
        </div>
      ) : null}

      {step === 1 ? (
        <div>
          <p className="text-sm text-mint-200 mb-4">
            Every project is a normal folder on your disk, with plain files inside. You can open it, back it up or move
            it without this app.
          </p>
          <FieldRow label="Projects folder">
            <Input
              value={projectsRoot}
              onChange={(event) => setProjectsRoot(event.target.value)}
              placeholder="C:\Users\you\Documents\PulseG Projects"
              aria-label="Projects folder"
            />
          </FieldRow>
          <p className="text-xs text-muted">
            The folder is created if it does not exist. If it cannot be written to, setup says so and asks again.
          </p>
        </div>
      ) : null}

      {step === 2 ? (
        <div>
          <p className="text-sm text-mint-200 mb-4">
            Publishing an approved task saves a snapshot of it in the project's own version history, so you can always
            look back at what changed and why.
          </p>
          <FieldRow label="Name">
            <Input value={gitName} onChange={(event) => setGitName(event.target.value)} aria-label="Your name" />
          </FieldRow>
          <FieldRow label="Email">
            <Input value={gitEmail} onChange={(event) => setGitEmail(event.target.value)} aria-label="Your email" />
          </FieldRow>
          {firstRun?.git_identity.name ? (
            <p className="text-xs text-muted">
              Found an existing name and email on this computer. Change them here to use something else for these
              projects only.
            </p>
          ) : null}
        </div>
      ) : null}

      {step === 3 ? (
        <div>
          <p className="text-sm text-mint-200 mb-4">
            The team needs at least one model to write with. These four have free tiers that do not ask for a card.
            This step is optional - you can add keys later, and the design session works without them.
          </p>
          <div className="mb-4 grid gap-2 sm:grid-cols-2">
            {catalogued.map((provider) => (
              <button
                key={provider.id}
                type="button"
                onClick={() => setProviderId(provider.id)}
                className={cn(
                  "rounded-md border px-3 py-2 text-left transition-colors duration-fast",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                  providerId === provider.id
                    ? "border-accent bg-charcoal-800"
                    : "border-app-border bg-charcoal-800 hover:border-surface-500",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm text-mint-100">{provider.name}</span>
                  {provider.free_forever ? <Badge tone="success">Free</Badge> : null}
                </div>
                <p className="text-xs text-muted mt-1">{provider.free_tier || provider.notes}</p>
              </button>
            ))}
          </div>
          <FieldRow label={`${selected?.name ?? "Provider"} key`} hint="Stored encrypted on this computer, never uploaded.">
            <Input
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="Paste the key from the provider's website"
              aria-label="Provider key"
            />
          </FieldRow>
          <div className="flex items-center gap-3">
            <Button
              variant="secondary"
              loading={saveKey.isPending}
              disabled={apiKey.trim().length < 8}
              onClick={() =>
                saveKey.mutate(
                  { provider_id: providerId, api_key: apiKey.trim() },
                  {
                    onSuccess: (result) => {
                      setApiKey("");
                      showToast({
                        title: "Key saved and tested",
                        body: `Saved as ${result.masked_key}.`,
                        tone: "success",
                      });
                    },
                    onError: (error: Error) =>
                      showToast({ title: "That key was not accepted", body: error.message, tone: "danger" }),
                  },
                )
              }
            >
              <IconKey size={16} />
              Save and test the key
            </Button>
            {selected?.signup_url ? (
              <a
                href={selected.signup_url}
                target="_blank"
                rel="noreferrer"
                className="text-sm text-canary-200 underline decoration-canary-600 underline-offset-2 transition-colors duration-fast hover:text-canary-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Get a free key from {selected.name}
              </a>
            ) : null}
          </div>
        </div>
      ) : null}

      {!firstRun && open ? (
        <div className="mt-4">
          <Spinner label="Checking this computer" />
        </div>
      ) : null}

      {open ? (
        <p className="mt-6 flex items-center gap-2 text-xs text-muted">
          <IconSparkle size={14} className="text-canary-200" />
          Nothing is sent anywhere until you add a key and start a build.
        </p>
      ) : null}
    </Dialog>
  );
}
