import { useState } from "react";
import { cn, humanise, plural, relativeTime } from "@/lib/utils";
import { revealProjectFolder } from "@/lib/desktop";
import { useActiveProject } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { useGitBranches, useGitDiff, useGitLog, useGitStatus, useTaskCommits } from "@/lib/queries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ScrollArea, Spinner } from "@/components/ui/feedback";
import { IconBranch, IconCheck, IconInfo } from "@/components/Icons";

/**
 * The project's history, in the order it actually happened.
 *
 * The important property of this screen is what it does *not* show: there is no way to commit from
 * here. Commits happen in exactly one place - when a person approves a task - so the log is a
 * record of human decisions, and each entry carries the task that produced it. A user reading this
 * in six months can see not just what changed but which agent changed it and who said yes.
 */
export function GitHistory() {
  const { data, isLoading, error } = useGitLog(100);
  const { data: status } = useGitStatus();
  const { data: branches } = useGitBranches();
  const { data: taskCommits } = useTaskCommits();
  const { data: active } = useActiveProject();
  const showToast = useStudio((state) => state.showToast);
  const projectId = active?.project.project_id ?? "";
  const [selected, setSelected] = useState("");
  const { data: detail } = useGitDiff(selected);

  const commits = data?.commits ?? [];
  const uncommitted = status?.status?.files ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-app-border px-6 py-4">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-mint-100">
            <IconBranch size={18} />
            History
          </h1>
          <p className="text-xs text-muted">
            {plural(commits.length, "saved point")}
            {branches?.current ? ` on ${branches.current}` : ""}
            {taskCommits?.count ? ` - ${taskCommits.count} tied to approved tasks` : ""}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="primary"
            data-primary-action
            disabled={!projectId}
            className="disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={async () => {
              if (!projectId) return;
              try {
                const result = await revealProjectFolder(projectId);
                showToast({
                  title: result.opened ? "Opened the project folder" : "Project folder path",
                  body: result.opened ? result.detail : result.path,
                  tone: "info",
                });
              } catch (failure) {
                showToast({
                  title: "Could not find that folder",
                  body: failure instanceof Error ? failure.message : String(failure),
                  tone: "danger",
                });
              }
            }}
          >
            Open the project folder
          </Button>
          {branches?.branches?.length ? (
            <select
              aria-label="Branches"
              defaultValue={branches.current}
              className="rounded-md border border-app-border bg-charcoal-800 px-3 py-2 text-sm text-mint-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {branches.branches.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          ) : null}
          {uncommitted.length > 0 ? (
            <Badge tone="warning">
              {uncommitted.length} file{uncommitted.length === 1 ? "" : "s"} not yet saved
            </Badge>
          ) : (
            <Badge tone="success">
              <IconCheck size={12} />
              Everything approved is saved
            </Badge>
          )}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto px-6 py-4">
          {isLoading ? (
            <Spinner label="Reading the history" />
          ) : error ? (
            <EmptyState
              title="History could not be read"
              detail={error instanceof Error ? error.message : "Open a project and try again."}
            />
          ) : commits.length === 0 ? (
            <EmptyState
              icon={<IconBranch />}
              title="Nothing saved yet"
              detail="Each time you approve a finished task, the studio records a point here with the task, the agent and your note. The first one appears when you approve your first task."
            />
          ) : (
            <ol className="space-y-2">
              {commits.map((commit) => {
                const taskId = commit.task_id ?? "";
                return (
                  <li key={commit.sha}>
                    <button
                      type="button"
                      onClick={() => setSelected(commit.sha)}
                      className={cn(
                        "w-full rounded-lg border px-4 py-3 text-left transition-colors duration-fast",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                        selected === commit.sha
                          ? "border-accent bg-app-surface-raised"
                          : "border-app-border bg-app-surface hover:border-surface-500",
                      )}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-teal-300">
                          {(commit.short ?? commit.sha).slice(0, 7)}
                        </span>
                        <span className="text-sm text-mint-100">{commit.subject}</span>
                        {taskId ? <Badge tone="muted">{taskId}</Badge> : null}
                        <span className="ml-auto text-[11px] text-muted">{relativeTime(commit.date)}</span>
                      </div>
                      <p className="mt-1 text-[11px] text-muted">
                        {commit.author || "unknown author"}
                        {commit.files?.length ? ` - ${commit.files.length} files` : ""}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ol>
          )}

          {uncommitted.length > 0 ? (
            <section className="mt-8">
              <h2 className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-muted">
                <IconInfo size={14} />
                Changed but not approved yet
              </h2>
              <ul className="space-y-1">
                {uncommitted.map((line) => (
                  <li key={line} className="font-mono text-xs text-mint-200">
                    {line}
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-muted">
                These are the files the team has touched. They are saved to the project folder as the work happens,
                and they enter the history when you approve the task that produced them.
              </p>
            </section>
          ) : null}
        </div>

        {selected ? (
          <aside className="w-[520px] shrink-0 border-l border-app-border bg-app-surface">
            <div className="flex items-center justify-between gap-3 border-b border-app-border px-4 py-3">
              <div className="min-w-0">
                <h2 className="truncate text-sm font-semibold text-mint-100">{detail?.sha.slice(0, 7) ?? selected.slice(0, 7)}</h2>
                <p className="text-[11px] text-muted">{humanise("changed files")}</p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setSelected("")}>
                Close
              </Button>
            </div>
            <ScrollArea className="h-[calc(100%-57px)]">
              <div className="px-4 py-3">
                {detail?.stat ? (
                  <pre className="mb-3 overflow-x-auto rounded-md bg-charcoal-800 p-3 font-mono text-[11px] text-mint-200">
                    {detail.stat}
                  </pre>
                ) : null}
                <pre className="overflow-x-auto rounded-md bg-charcoal-900 p-3 font-mono text-[11px] leading-relaxed text-mint-200">
                  {detail?.diff || "Reading the changes..."}
                </pre>
              </div>
            </ScrollArea>
          </aside>
        ) : null}
      </div>
    </div>
  );
}
