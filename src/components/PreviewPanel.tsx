import { useMemo, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { cn, humanise } from "@/lib/utils";
import { useExportWeb, usePreviewGraph, usePreviewState } from "@/lib/queries";
import { useStudio } from "@/lib/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/feedback";
import { IconGamepad, IconGraph, IconRefresh } from "@/components/Icons";

/**
 * The Live Preview panel, pinned under the planning rail.
 *
 * Two modes, one toggle. **Graph** is the studio itself: the twelve agents, who reports to whom,
 * and which one is working right now - the fastest way to answer "why is nothing happening?".
 * **Game** is the Godot Web export of the actual build, served by the backend and refreshed when
 * an approved phase completes.
 *
 * The graph is drawn with the agents' own colours from the theme, so a chip in the Overview and a
 * node here are obviously the same agent.
 */
export function PreviewPanel() {
  const mode = useStudio((state) => state.previewMode);
  const setMode = useStudio((state) => state.setPreviewMode);
  const showToast = useStudio((state) => state.showToast);
  const { data: graph, isLoading: graphLoading } = usePreviewGraph();
  const { data: preview } = usePreviewState();
  const exportWeb = useExportWeb();
  const [selected, setSelected] = useState<string>("");

  const nodes: Node[] = useMemo(() => {
    if (!graph) return [];
    // A simple two-ring layout: the first six around the top, the rest below, so twelve nodes
    // stay legible without a physics engine deciding where they go.
    return graph.nodes.map((node, index) => {
      const ring = index < 6 ? 0 : 1;
      const positionInRing = ring === 0 ? index : index - 6;
      const perRing = ring === 0 ? 6 : 6;
      const angle = (positionInRing / perRing) * Math.PI * 2;
      const radius = ring === 0 ? 1 : 0.62;
      return {
        id: node.id,
        position: { x: 320 + Math.cos(angle) * 240 * radius, y: 200 + Math.sin(angle) * 150 * radius },
        data: { label: node.label },
        style: {
          background: "var(--app-surface-raised)",
          color: "var(--text-primary)",
          border: `2px solid ${node.colour}`,
          borderRadius: 8,
          fontSize: 11,
          padding: 6,
          width: 130,
          opacity: node.enabled ? 1 : 0.45,
          boxShadow: node.status === "working" ? `0 0 0 3px ${node.colour}55` : undefined,
        },
      };
    });
  }, [graph]);

  const edges: Edge[] = useMemo(() => {
    if (!graph) return [];
    return graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.label,
      animated: edge.animated,
      style: { stroke: "var(--surface-500)", strokeWidth: 1.4 },
      labelStyle: { fill: "var(--text-muted)", fontSize: 10 },
    }));
  }, [graph]);

  const selectedNode = graph?.nodes.find((node) => node.id === selected);

  return (
    <div className="flex h-[300px] shrink-0 flex-col border-t border-app-border">
      <div className="flex items-center gap-2 px-3 py-2">
        <div className="flex rounded-md border border-app-border bg-charcoal-800 p-0.5" role="tablist" aria-label="Preview mode">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "graph"}
            onClick={() => setMode("graph")}
            className={cn(
              "flex items-center gap-2 rounded-sm px-3 py-1 text-xs transition-colors duration-fast",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
              mode === "graph" ? "bg-accent text-accent-foreground" : "text-mint-200 hover:bg-surface-700",
            )}
          >
            <IconGraph size={14} />
            Team
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "game"}
            onClick={() => setMode("game")}
            className={cn(
              "flex items-center gap-2 rounded-sm px-3 py-1 text-xs transition-colors duration-fast",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
              mode === "game" ? "bg-accent text-accent-foreground" : "text-mint-200 hover:bg-surface-700",
            )}
          >
            <IconGamepad size={14} />
            Game
          </button>
        </div>

        {mode === "graph" && graph?.review_count ? (
          <Badge tone="accent">{graph.review_count} waiting</Badge>
        ) : null}

        <div className="ml-auto flex items-center gap-2">
          {mode === "game" && preview?.game.available ? (
            <Button
              variant="ghost"
              size="sm"
              loading={exportWeb.isPending}
              onClick={() =>
                exportWeb.mutate(undefined, {
                  onSuccess: (result) =>
                    showToast({
                      title: "Preview refreshed",
                      body: result.detail,
                      tone: "success",
                    }),
                  onError: (error: Error) =>
                    showToast({ title: "Could not rebuild the preview", body: error.message, tone: "danger" }),
                })
              }
            >
              <IconRefresh size={14} />
              Rebuild
            </Button>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1">
        {mode === "graph" ? (
          graphLoading ? (
            <div className="flex h-full items-center justify-center">
              <Spinner label="Drawing the team" />
            </div>
          ) : nodes.length === 0 ? (
            <p className="p-4 text-sm text-muted">The team appears here once a project is open.</p>
          ) : (
            <div className="h-full">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                fitView
                proOptions={{ hideAttribution: true }}
                nodesDraggable={false}
                nodesConnectable={false}
                onNodeClick={(_event, node) => setSelected(node.id)}
              >
                <Background color="var(--charcoal-800)" gap={24} />
                <Controls showInteractive={false} />
              </ReactFlow>
              {selectedNode ? (
                <div className="pointer-events-none absolute bottom-4 left-3 rounded-md border border-app-border bg-charcoal-800 px-3 py-2 text-xs text-mint-100">
                  <p className="font-medium">{selectedNode.label}</p>
                  <p className="text-muted">{selectedNode.role}</p>
                  <p className="text-muted">
                    {selectedNode.status}
                    {selectedNode.active_title ? ` - ${selectedNode.active_title}` : ""}
                  </p>
                  <p className="text-muted">
                    {humanise(selectedNode.primary)}
                    {selectedNode.tasks_completed ? ` - ${selectedNode.tasks_completed} finished` : ""}
                  </p>
                </div>
              ) : null}
            </div>
          )
        ) : preview?.game.available ? (
          <iframe
            title="The game so far"
            src={preview.game.url}
            className="h-full w-full border-0"
            sandbox="allow-scripts allow-same-origin"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
            <IconGamepad className="text-muted" />
            <p className="text-sm text-mint-100">The playable build shows up here</p>
            <p className="text-xs text-muted max-w-xs">
              {preview?.explanation ??
                "Finish a phase and approve it, and the studio exports the game to run right here."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
