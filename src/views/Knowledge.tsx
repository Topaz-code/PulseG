import { useMemo, useState } from "react";
import { cn, formatBytes, humanise, relativeTime } from "@/lib/utils";
import { useKnowledge, useKnowledgeSearch, useKnowledgeStats, useTranscripts } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import type { KnowledgeRow } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { EmptyState, ScrollArea, Spinner } from "@/components/ui/feedback";
import { MarkdownView } from "@/components/MarkdownView";
import { IconAlert, IconSearch, IconTerminal } from "@/components/Icons";

/**
 * The knowledge base: what the team has learned, and where each fact came from.
 *
 * The research agents read documentation, watch tutorials and search the web. That material has to
 * live somewhere the Tester and Programmer can consult without re-reading it, and it has to be
 * traceable: `trust` is shown on every entry because "the official Godot docs say character bodies
 * need a shape" and "a forum post from 2019 says the same" are not the same kind of claim. Low
 * trust is surfaced, not hidden.
 */
export function Knowledge() {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState<KnowledgeRow | null>(null);
  const [results, setResults] = useState<KnowledgeRow[] | null>(null);
  const { data, isLoading } = useKnowledge("");
  const { data: stats } = useKnowledgeStats();
  const { data: transcripts } = useTranscripts();
  const search = useKnowledgeSearch();
  const showToast = useStudio((state) => state.showToast);

  const entries = results ?? data?.entries ?? [];
  const untrusted = new Set(stats?.untrusted ?? []);

  const kinds = useMemo(() => {
    const map = new Map<string, number>();
    for (const entry of data?.entries ?? []) map.set(entry.kind, (map.get(entry.kind) ?? 0) + 1);
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [data]);

  const runSearch = () => {
    const needle = query.trim();
    if (!needle) {
      setResults(null);
      return;
    }
    search.mutate(
      { q: needle, kind, limit: 100 },
      {
        onSuccess: (result) => {
          setResults(result.results);
          showToast({
            title: `${result.count} result${result.count === 1 ? "" : "s"}`,
            body: result.note,
            tone: "info",
          });
        },
        onError: (failure: Error) => showToast({ title: "Search failed", body: failure.message, tone: "danger" }),
      },
    );
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-app-border px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold text-mint-100">Knowledge Base</h1>
          <p className="text-xs text-muted">
            {stats?.entries ?? 0} entries from {stats?.sources.length ?? 0} sources, {formatBytes(stats?.bytes ?? 0)}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") runSearch();
            }}
            placeholder="Search everything the team has read"
            aria-label="Search the knowledge base"
            className="w-72"
          />
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value)}
            aria-label="Filter by kind"
            className="rounded-md border border-app-border bg-charcoal-800 px-3 py-2 text-sm text-mint-100 transition-colors duration-fast hover:border-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <option value="">Every kind</option>
            {kinds.map(([name, count]) => (
              <option key={name} value={name}>
                {humanise(name)} ({count})
              </option>
            ))}
          </select>
          <Button variant="primary" data-primary-action onClick={runSearch} loading={search.isPending}>
            <IconSearch size={16} />
            Search
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto px-6 py-4">
          {isLoading ? (
            <Spinner label="Reading the index" />
          ) : entries.length === 0 ? (
            <EmptyState
              icon={<IconSearch />}
              title={query ? "Nothing matched that" : "Nothing collected yet"}
              detail={
                query
                  ? "Try a shorter phrase, or clear the filter. The index only holds what the research agents have actually read."
                  : "The Researcher and Transcriptor agents fill this as they work: engine documentation, tutorials, video transcripts. Every entry records where it came from."
              }
            />
          ) : (
            <ul className="space-y-3">
              {entries.map((entry) => (
                <li key={entry.entry_id}>
                  <button
                    type="button"
                    onClick={() => setSelected(entry)}
                    className={cn(
                      "w-full rounded-lg border border-app-border bg-app-surface px-4 py-3 text-left",
                      "transition-colors duration-fast hover:border-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-mint-100">{entry.title}</span>
                      <Badge tone="muted">{humanise(entry.kind)}</Badge>
                      {untrusted.has(entry.entry_id) || untrusted.has(entry.source) ? (
                        <Badge tone="warning">Low trust</Badge>
                      ) : null}
                      <span className="ml-auto text-[11px] text-muted">{relativeTime(entry.created_at)}</span>
                    </div>
                    <p className="mt-2 line-clamp-2 text-sm text-mint-200">{entry.summary}</p>
                    <p className="mt-1 truncate text-[11px] text-muted">
                      {entry.source || entry.path || "source not recorded"}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {transcripts?.count ? (
            <section className="mt-8">
              <h2 className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-muted">
                <IconTerminal size={14} /> Video transcripts ({transcripts.count})
              </h2>
              <ul className="space-y-1">
                {transcripts.transcripts.map((row, index) => (
                  <li key={String(row.transcript_id ?? index)} className="text-xs text-mint-200">
                    {String(row.title ?? row.source ?? "Untitled")}{" "}
                    <span className="text-muted">{String(row.duration ?? "")}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>

        {selected ? (
          <aside className="w-[440px] shrink-0 border-l border-app-border bg-app-surface">
            <div className="flex items-start justify-between gap-3 border-b border-app-border px-4 py-3">
              <div className="min-w-0">
                <h2 className="truncate text-sm font-semibold text-mint-100">{selected.title}</h2>
                <p className="text-[11px] text-muted">
                  {humanise(selected.kind)} - {selected.source || selected.path || "source not recorded"}
                </p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
                Close
              </Button>
            </div>
            <ScrollArea className="h-[calc(100%-57px)]">
              <div className="px-4 py-4">
                {selected.tags?.length ? (
                  <div className="mb-3 flex flex-wrap gap-2">
                    {selected.tags.map((tag) => (
                      <Badge key={tag} tone="muted">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                ) : null}
                {untrusted.has(selected.entry_id) || untrusted.has(selected.source) ? (
                  <p className="mb-3 flex items-start gap-2 rounded-md border border-canary-700 bg-charcoal-800 px-3 py-2 text-xs text-canary-200">
                    <IconAlert size={14} className="mt-1 shrink-0" />
                    This came from a source without editorial review. Check it against the engine documentation before
                    the team relies on it.
                  </p>
                ) : null}
                <MarkdownView content={selected.summary || "No summary was written for this entry."} />
              </div>
            </ScrollArea>
          </aside>
        ) : null}
      </div>
    </div>
  );
}
