import { useMemo, useState } from "react";
import { baseName, formatBytes, humanise, plural, relativeTime } from "@/lib/utils";
import { useAssetActions, useAssets, useIngestAsset, useStyleLock } from "@/lib/queries";
import type { AssetRow } from "@/lib/types";
import { useStudio } from "@/lib/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, EmptyState, Spinner } from "@/components/ui/feedback";
import { FieldRow, Input, Textarea } from "@/components/ui/field";
import { IconAlert, IconImages, IconPlus, IconRefresh, IconSparkle } from "@/components/Icons";

/**
 * The asset library: what the game is made of, and the requests for what is still missing.
 *
 * Two rules from the brief are visible here. An agent must look in this library before it
 * generates anything, so this is the first place a person goes when a sprite looks wrong. And the
 * style lock at the top is what keeps art consistent - every generation prompt is prefixed with
 * it, and it is a plain file the user can edit.
 *
 * Asking for a change here never edits a file directly: it creates a task, which then goes through
 * the Auditor and the human gate like everything else.
 */
/**
 * The kind vocabulary of the API, not a guess at it.
 *
 * `backend/orchestration/projects.py` classifies a file by its suffix when it is ingested or
 * scanned - .png is a sprite, .ogg is sfx, .md is a reference - and the library stores that word. A
 * chip list saying "image" and "audio" would filter every real asset out of the gallery, so these
 * are the same words the backend uses. "All" stays first.
 */
const CATEGORIES = [
  "all",
  "sprite",
  "background",
  "tile",
  "ui",
  "sfx",
  "bgm",
  "reference",
] as const;

/** The name shown on a card. The API sends one; a path is always there to fall back on. */
function assetName(asset: AssetRow): string {
  return asset.name || baseName(asset.path);
}

type AssetRequestRow = {
  request_id: string;
  name: string;
  kind: string;
  description: string;
  detail?: string;
  status: string;
  task_id: string;
  created_at: string;
  requested_by: string;
};

export function Assets() {
  const { data, isLoading, error } = useAssets();
  const { data: styleLock } = useStyleLock();
  const ingest = useIngestAsset();
  const actions = useAssetActions();
  const showToast = useStudio((state) => state.showToast);
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>("all");
  const [query, setQuery] = useState("");
  const [askOpen, setAskOpen] = useState(false);
  const [requestName, setRequestName] = useState("");
  const [requestNote, setRequestNote] = useState("");
  const [preview, setPreview] = useState("");

  const assets = data?.assets ?? [];
  const requests = (data?.requests ?? []) as unknown as AssetRequestRow[];

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return assets.filter((asset) => {
      if (category !== "all" && asset.kind !== category) return false;
      if (!needle) return true;
      return assetName(asset).toLowerCase().includes(needle) || asset.path.toLowerCase().includes(needle);
    });
  }, [assets, category, query]);

  // The API decides what can be played in a player (it knows the audio suffixes); the screen only
  // decides what to do with that answer.
  const sound = visible.filter((asset) => asset.audio);
  const images = visible.filter((asset) => !asset.audio);
  const openRequests = requests.filter((row) => row.status === "open");

  const report = (title: string, body: string, tone: "info" | "success" | "danger" = "success") =>
    showToast({ title, body, tone });

  const run = (action: Parameters<typeof actions.mutate>[0], successTitle: string) =>
    actions.mutate(action, {
      onSuccess: (result) =>
        report(
          successTitle,
          result.task_id ? `Task ${result.task_id} is on the board.` : result.note ?? "Done.",
          "success",
        ),
      onError: (failure: Error) => report("That did not go through", failure.message, "danger"),
    });

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-start gap-3 border-b border-app-border px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold text-mint-100">Assets</h1>
          <p className="text-xs text-muted">
            {plural(assets.length, "file")}, {formatBytes(data?.total_bytes ?? 0)} - the team checks here
            before generating
            anything new
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Find an asset"
            aria-label="Find an asset"
            className="w-44 disabled:opacity-50 disabled:cursor-not-allowed"
          />
          <select
            value={category}
            onChange={(event) => setCategory(event.target.value as (typeof CATEGORIES)[number])}
            aria-label="Filter by kind"
            className="rounded-md border border-app-border bg-charcoal-800 px-3 py-2 text-sm text-mint-100 transition-colors duration-fast hover:border-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {CATEGORIES.map((kind) => (
              <option key={kind} value={kind}>
                {kind === "all" ? "All kinds" : humanise(kind)}
              </option>
            ))}
          </select>
          <Button
            variant="secondary"
            loading={ingest.isPending}
            onClick={() =>
              ingest.mutate(
                { folder: "assets", note: "Added from the Assets screen." },
                {
                  onSuccess: (result) =>
                    report(
                      `${result.added} file${result.added === 1 ? "" : "s"} added`,
                      `The library now holds ${result.total} files.`,
                    ),
                  onError: (failure: Error) => report("Nothing was added", failure.message, "danger"),
                },
              )
            }
          >
            <IconPlus size={16} />
            Scan the assets folder
          </Button>
          <Button variant="primary" data-primary-action onClick={() => setAskOpen(true)}>
            <IconImages size={16} />
            Ask for an asset
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
        <section className="mb-6 rounded-lg border border-app-border bg-app-surface px-4 py-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="flex items-center gap-2 text-sm font-medium text-mint-100">
                <IconSparkle size={15} className="text-canary-200" />
                Style lock
              </h2>
              <p className="mt-1 text-xs text-mint-200">{styleLock?.style || "Nothing set yet."}</p>
              <p className="mt-1 text-xs text-muted">{styleLock?.note}</p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                run(
                  { kind: "regenerate", path: "assets/STYLE.md", reason: "Recheck the style lock." },
                  "Style check queued",
                )
              }
            >
              <IconRefresh size={14} />
              Recheck it
            </Button>
          </div>
        </section>

        {requests.length > 0 ? (
          <section className="mb-6">
            <h2 className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-muted">
              <IconAlert size={14} />
              Asset requests - {openRequests.length} still open
            </h2>
            <div className="space-y-2">
              {requests.map((row) => (
                <div
                  key={row.request_id}
                  className="flex flex-wrap items-center gap-3 rounded-md border border-canary-700 bg-charcoal-800 px-4 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-mint-100">{row.name}</p>
                    <p className="text-xs text-muted">
                      {humanise(row.kind)} - asked by {humanise(row.requested_by || "the team")}{" "}
                      {relativeTime(row.created_at)}
                      {row.description || row.detail ? ` - ${row.description || row.detail}` : ""}
                    </p>
                  </div>
                  <Badge tone={row.status === "open" ? "warning" : "muted"}>{humanise(row.status)}</Badge>
                  {row.status === "open" ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => run({ kind: "fulfil", requestId: row.request_id }, "Sent to the team")}
                    >
                      Ask the team to make it
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {isLoading ? (
          <Spinner label="Reading the library" />
        ) : error ? (
          <EmptyState
            title="The library could not be read"
            detail={error instanceof Error ? error.message : "Open a project and try again."}
          />
        ) : assets.length === 0 ? (
          <EmptyState
            icon={<IconImages />}
            title="No assets yet"
            detail="Scan the project's assets folder to index files you already have, or ask for one specific piece and the team will make it."
          />
        ) : (
          <>
            <section>
              <h2 className="mb-3 text-xs uppercase tracking-wide text-muted">Artwork ({images.length})</h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {images.map((asset) => (
                  <div key={asset.path} className="overflow-hidden rounded-lg border border-app-border bg-app-surface">
                    <button
                      type="button"
                      onClick={() => setPreview(asset.path)}
                      className="block w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                      aria-label={`Preview ${assetName(asset)}`}
                    >
                      <img
                        src={asset.url ?? `/media/${asset.path}`}
                        alt={assetName(asset)}
                        loading="lazy"
                        className="h-36 w-full bg-charcoal-800 object-contain"
                      />
                    </button>
                    <div className="px-3 py-2">
                      <p className="truncate text-sm text-mint-100" title={assetName(asset)}>
                        {assetName(asset)}
                      </p>
                      <p className="mt-1 text-[11px] text-muted">
                        {humanise(asset.kind)} - {formatBytes(asset.bytes)} - {relativeTime(asset.modified)}
                      </p>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="mt-1 w-full justify-start"
                        onClick={() => run({ kind: "regenerate", path: asset.path }, "Regeneration queued")}
                      >
                        <IconRefresh size={14} />
                        Regenerate
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              {images.length === 0 ? <p className="text-sm text-muted">No artwork matches that filter.</p> : null}
            </section>

            {sound.length > 0 ? (
              <section className="mt-8">
                <h2 className="mb-3 text-xs uppercase tracking-wide text-muted">Sound ({sound.length})</h2>
                <div className="space-y-2">
                  {sound.map((asset) => (
                    <div
                      key={asset.path}
                      className="flex flex-wrap items-center gap-3 rounded-md border border-app-border bg-app-surface px-4 py-3"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-mint-100">{assetName(asset)}</span>
                        <span className="text-[11px] text-muted">
                          {formatBytes(asset.bytes)} - {relativeTime(asset.modified)}
                        </span>
                      </span>
                      <audio controls preload="none" src={asset.url ?? `/media/${asset.path}`} className="h-9" />
                      <Button variant="ghost" size="sm" onClick={() => run({ kind: "recurate", path: asset.path }, "Recuration queued")}>
                        Recurate
                      </Button>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
          </>
        )}
      </div>

      <Dialog
        open={Boolean(preview)}
        onOpenChange={(next) => (next ? undefined : setPreview(""))}
        title={preview.split("/").pop() ?? "Asset"}
        detail={preview}
        width="xl"
      >
        {preview ? (
          <img
            src={`/media/${preview}`}
            alt={preview}
            className="mx-auto max-h-[60vh] rounded-md bg-charcoal-800 object-contain"
          />
        ) : null}
      </Dialog>

      <Dialog
        open={askOpen}
        onOpenChange={setAskOpen}
        title="Ask for an asset"
        detail="The team checks the library first. If the piece is genuinely missing, this becomes a normal task."
        width="md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setAskOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={requestName.trim().length < 3 || actions.isPending}
              loading={actions.isPending}
              onClick={() =>
                actions.mutate(
                  { kind: "request", name: requestName.trim(), detail: requestNote.trim() },
                  {
                    onSuccess: () => {
                      setAskOpen(false);
                      setRequestName("");
                      setRequestNote("");
                      report("Request sent to the team", "The card is on the board under asset requests.");
                    },
                    onError: (failure: Error) => report("Could not send that", failure.message, "danger"),
                  },
                )
              }
            >
              Send the request
            </Button>
          </>
        }
      >
        <FieldRow
          label="What do you need?"
          hint="Be specific. 'A lantern sprite, 32x32, three animation frames' beats 'some lighting'."
        >
          <Input
            value={requestName}
            onChange={(event) => setRequestName(event.target.value)}
            aria-label="Asset name"
          />
        </FieldRow>
        <FieldRow label="Anything else the artist should know?">
          <Textarea
            value={requestNote}
            onChange={(event) => setRequestNote(event.target.value)}
            aria-label="Asset detail"
            disabled={actions.isPending}
            className="disabled:opacity-50 disabled:cursor-not-allowed"
          />
        </FieldRow>
      </Dialog>
    </div>
  );
}
