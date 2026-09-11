import { useEffect, useMemo, useState } from "react";
import { cn, humanise, relativeTime } from "@/lib/utils";
import { useDesignDocs, useDesignHistory, useSaveDesign } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { EmptyState, Spinner } from "@/components/ui/feedback";
import { MarkdownView } from "@/components/MarkdownView";
import { IconBook, IconCheck, IconRefresh } from "@/components/Icons";

/**
 * The design document and the story, exactly as they are on disk.
 *
 * These files are the project's source of truth - the studio keeps no database of "the design".
 * So this screen is a viewer with one write path back: the human can edit a document here, and the
 * save goes straight to the Markdown file with no commit, because committing is what approving a
 * task does. If an agent is mid-write on the same file the save is refused rather than allowed to
 * race it, which is the same rule the task bus enforces between agents.
 */
const KIND_LABEL: Record<string, string> = {
  design: "Design document",
  story: "Story",
  memory: "Working notes",
  other: "Document",
};

export function DesignView() {
  const { data, isLoading, error } = useDesignDocs();
  const save = useSaveDesign();
  const showToast = useStudio((state) => state.showToast);
  const [active, setActive] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const docs = useMemo(() => data?.documents ?? [], [data]);
  const shown = useMemo(() => docs.find((doc) => doc.path === active) ?? docs[0], [docs, active]);
  const { data: history } = useDesignHistory(shown?.path ?? "");

  useEffect(() => {
    if (shown) setActive(shown.path);
  }, [shown]);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="Opening the design" />
      </div>
    );
  }

  if (docs.length === 0) {
    return (
      <div className="p-6">
        <EmptyState
          icon={<IconBook />}
          title="Nothing written yet"
          detail={
            error instanceof Error
              ? error.message
              : "The design document is written after you finish the planning questions and confirm the summary. Story files appear as the Story Writer works through a phase."
          }
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-app-border px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold text-mint-100">Game Design and Story</h1>
          <p className="text-xs text-muted">
            {docs.length} documents, plain Markdown in the project folder and versioned with it.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {editing ? (
            <>
              <Button
                variant="ghost"
                onClick={() => {
                  setEditing(false);
                }}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={save.isPending}
                disabled={!shown}
                onClick={() =>
                  shown &&
                  save.mutate(
                    { path: shown.path, content: draft },
                    {
                      onSuccess: () => {
                        setEditing(false);
                        showToast({
                          title: "Saved",
                          body: "The team reads this version from now on. It joins the history with the next approved task.",
                          tone: "success",
                        });
                      },
                      onError: (failure: Error) =>
                        showToast({ title: "Could not save", body: failure.message, tone: "danger" }),
                    },
                  )
                }
              >
                <IconCheck size={16} />
                Save the document
              </Button>
            </>
          ) : (
            <Button
              variant="primary"
              data-primary-action
              disabled={!shown}
              onClick={() => {
                setDraft(shown?.content ?? "");
                setEditing(true);
              }}
            >
              Edit this document
            </Button>
          )}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <nav aria-label="Documents" className="w-56 shrink-0 overflow-y-auto border-r border-app-border p-3">
          {docs.map((doc) => (
            <button
              key={doc.path}
              type="button"
              onClick={() => {
                setActive(doc.path);
                setEditing(false);
              }}
              aria-current={shown?.path === doc.path}
              className={cn(
                "mb-1 w-full rounded-md px-3 py-2 text-left text-sm transition-colors duration-fast",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                shown?.path === doc.path ? "bg-charcoal-800 text-canary-200" : "text-mint-200 hover:bg-surface-700",
              )}
            >
              <span className="block truncate">{doc.title}</span>
              <span className="mt-1 block text-[11px] text-muted">{KIND_LABEL[doc.kind] ?? humanise(doc.kind)}</span>
            </button>
          ))}
        </nav>

        <div className="min-w-0 flex-1 overflow-y-auto px-8 py-6">
          <div className="mx-auto max-w-3xl">
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-semibold text-mint-100">{shown?.title}</h2>
              <Badge tone="muted">{shown?.path}</Badge>
            </div>

            {editing ? (
              <Textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                aria-label="Document contents"
                disabled={save.isPending}
                className="min-h-[60vh] font-mono text-sm disabled:opacity-50 disabled:cursor-not-allowed"
                spellCheck={false}
              />
            ) : (
              <MarkdownView content={shown?.content ?? ""} />
            )}

            {history?.commits?.length ? (
              <section className="mt-10 border-t border-app-border pt-4">
                <h3 className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-muted">
                  <IconRefresh size={14} /> Earlier versions of this file
                </h3>
                <ul className="space-y-2">
                  {history.commits.slice(0, 8).map((version) => (
                    <li key={version.sha} className="flex flex-wrap items-center gap-3 text-xs">
                      <span className="font-mono text-teal-300">{(version.short ?? version.sha).slice(0, 7)}</span>
                      <span className="text-mint-200">{version.message ?? "saved"}</span>
                      {version.date ? <span className="text-muted">{relativeTime(version.date)}</span> : null}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
