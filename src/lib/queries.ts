import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, query, ApiError } from "./api";
import type {
  ActiveProject,
  AgentRow,
  Board,
  FirstRun,
  GitCommit,
  KnowledgeRow,
  AssetRow,
  NotificationStatus,
  Overview,
  PlanningState,
  PreviewGraph,
  PreviewState,
  Project,
  ProviderRow,
  RunState,
  Task,
  ThemeResponse,
} from "./types";

/**
 * Every server read and write, in one file.
 *
 * Two conventions worth knowing before adding to it:
 *
 * * **Query keys are nouns.** `["tasks"]`, `["task", id]`, `["agents"]`. The event stream
 *   invalidates by noun, so a websocket message about a task invalidates `["tasks"]` and
 *   `["board"]` without anything subscribing to individual sockets.
 * * **Mutations return the server's own payload.** The backend already answers with the updated
 *   object plus what changed (for example `{task, commit, unblocked}`), so callers show real
 *   data rather than guessing what happened.
 */

export const keys = {
  activeProject: ["project", "active"] as const,
  projects: ["projects"] as const,
  overview: (id: string) => ["overview", id] as const,
  board: ["board"] as const,
  tasks: (params?: Record<string, unknown>) => ["tasks", params ?? {}] as const,
  task: (id: string) => ["task", id] as const,
  review: ["review"] as const,
  agents: ["agents"] as const,
  agent: (id: string) => ["agent", id] as const,
  agentStates: ["agent", "states"] as const,
  providers: ["providers"] as const,
  quota: ["providers", "quota"] as const,
  planning: ["planning"] as const,
  assets: ["assets"] as const,
  knowledge: (term: string) => ["knowledge", term] as const,
  logs: ["logs"] as const,
  gitLog: ["git", "log"] as const,
  gitStatus: ["git", "status"] as const,
  preview: ["preview"] as const,
  graph: ["preview", "graph"] as const,
  theme: ["design", "theme"] as const,
  notifications: ["notifications"] as const,
  firstRun: ["system", "first-run"] as const,
  settings: ["settings"] as const,
  systemInfo: ["system", "info"] as const,
};

// --- projects ---------------------------------------------------------------------------

export function useActiveProject() {
  return useQuery({
    queryKey: keys.activeProject,
    queryFn: () => api.get<ActiveProject>("/api/projects/active"),
    // No project open is a normal state during setup, not an error worth retrying.
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
    staleTime: 5_000,
  });
}

export function useProjects() {
  return useQuery({
    queryKey: keys.projects,
    queryFn: () => api.get<{ projects: Project[]; active_project_id: string }>("/api/projects"),
  });
}

export function useCreateProject() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: { name: string; mode: "fresh" | "existing"; concept?: string; path?: string }) =>
      api.post<{ project: Project; overview: Overview }>("/api/projects", payload),
    onSuccess: () => {
      void client.invalidateQueries();
    },
  });
}

export function useActivateProject() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.post<{ project: Project }>(`/api/projects/${projectId}/activate`),
    onSuccess: () => {
      void client.invalidateQueries();
    },
  });
}

// --- tasks and the human gate -------------------------------------------------------------

export function useBoard() {
  return useQuery({
    queryKey: keys.board,
    queryFn: () => api.get<Board>("/api/tasks/board"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useTask(taskId: string) {
  return useQuery({
    queryKey: keys.task(taskId),
    queryFn: () => api.get<Task & { audit_summary?: string; blocked_by?: string[]; dependents?: string[] }>(
      `/api/tasks/${taskId}`,
    ),
    enabled: Boolean(taskId),
  });
}

export function useReviewQueue() {
  return useQuery({
    queryKey: keys.review,
    queryFn: () =>
      api.get<{ items: Task[]; count: number; unreviewed_artifacts: number }>("/api/tasks/review"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useCreateTask() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      instruction: string;
      assigned_to: string;
      title?: string;
      expected_outputs?: string[];
      dependencies?: string[];
      context_files?: string[];
      file_claims?: string[];
      created_by?: string;
      phase?: number;
      kind?: string;
      priority?: number;
    }) => api.post<{ task: Task }>("/api/tasks", payload),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.board }),
  });
}

export function useDecideTask() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      taskId,
      decision,
      note,
      override = false,
    }: {
      taskId: string;
      decision: "approve" | "reject" | "override";
      note?: string;
      override?: boolean;
    }) => {
      if (decision === "approve") {
        return api.post<{ task: Task; commit?: { ok: boolean; detail: string; sha?: string }; unblocked?: string[] }>(
          `/api/tasks/${taskId}/approve`,
          { note: note ?? "", override },
        );
      }
      if (decision === "override") {
        return api.post<{ task: Task; commit?: { ok: boolean; detail: string; sha?: string }; unblocked?: string[] }>(
          `/api/tasks/${taskId}/override`,
          { note: note ?? "" },
        );
      }
      return api.post<{ task: Task; requeued: boolean }>(`/api/tasks/${taskId}/reject`, { note: note ?? "" });
    },
    onSuccess: (_result, variables) => {
      void client.invalidateQueries({ queryKey: keys.board });
      void client.invalidateQueries({ queryKey: keys.review });
      void client.invalidateQueries({ queryKey: keys.task(variables.taskId) });
      void client.invalidateQueries({ queryKey: keys.gitLog });
    },
  });
}

export function useCommentOnTask() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, message }: { taskId: string; message: string }) =>
      api.post<Task>(`/api/tasks/${taskId}/comment`, { message, author: "human" }),
    onSuccess: (_task, variables) => client.invalidateQueries({ queryKey: keys.task(variables.taskId) }),
  });
}

export function useRetryTask() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, note }: { taskId: string; note?: string }) =>
      api.post<Task>(`/api/tasks/${taskId}/retry`, { note: note ?? "Retried from the dashboard." }),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.board }),
  });
}

// --- run control ---------------------------------------------------------------------------

export function useRunState() {
  return useQuery({
    queryKey: ["run-state"],
    queryFn: () => api.get<{ run_state: RunState }>("/api/logs/run-state"),
    refetchInterval: 5_000,
  });
}

export function useRunControl() {
  const client = useQueryClient();
  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ["run-state"] });
    void client.invalidateQueries({ queryKey: keys.board });
  };
  return {
    start: useMutation({ mutationFn: () => api.post("/api/logs/run-state/start"), onSuccess: invalidate }),
    pause: useMutation({ mutationFn: () => api.post("/api/logs/run-state/pause"), onSuccess: invalidate }),
    stop: useMutation({ mutationFn: () => api.post("/api/logs/run-state/stop"), onSuccess: invalidate }),
    tick: useMutation({ mutationFn: () => api.post("/api/logs/tick"), onSuccess: invalidate }),
  };
}

// --- agents --------------------------------------------------------------------------------

export function useAgents() {
  return useQuery({
    queryKey: keys.agents,
    queryFn: () => api.get<{ agents: AgentRow[]; count: number; missing_providers?: string[] }>("/api/agents"),
    staleTime: 10_000,
  });
}

export function useAgentStates() {
  return useQuery({
    queryKey: keys.agentStates,
    queryFn: () => api.get<{ agents: AgentRow[] }>("/api/agents/states"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useAgent(agentId: string) {
  return useQuery({
    queryKey: keys.agent(agentId),
    queryFn: () => api.get<AgentRow & { history?: unknown[]; recent_verdicts?: unknown[] }>(`/api/agents/${agentId}`),
    enabled: Boolean(agentId),
  });
}

export function useUpdateChain() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      agentId,
      primary,
      fallbacks,
    }: {
      agentId: string;
      primary: { provider: string; model: string };
      fallbacks: Array<{ provider: string; model: string }>;
    }) => api.put<{ agent: AgentRow }>(`/api/agents/${agentId}/chain`, { primary, fallbacks }),
    onSuccess: (_result, variables) => {
      void client.invalidateQueries({ queryKey: keys.agents });
      void client.invalidateQueries({ queryKey: keys.agent(variables.agentId) });
    },
  });
}

export function useUpdateAgent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ agentId, patch }: { agentId: string; patch: Record<string, unknown> }) =>
      api.patch<{ agent: AgentRow }>(`/api/agents/${agentId}`, patch),
    onSuccess: (_result, variables) => client.invalidateQueries({ queryKey: keys.agent(variables.agentId) }),
  });
}

// --- providers and keys --------------------------------------------------------------------

export function useProviders() {
  return useQuery({
    queryKey: keys.providers,
    queryFn: () =>
      api.get<{
        providers: ProviderRow[];
        configured: string[];
        substitutions: Record<string, { substitute: string; model?: string; reason?: string }>;
        verified_on?: string;
      }>("/api/providers"),
  });
}

export function useSaveKey() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: { provider_id: string; api_key: string }) =>
      api.put<{ provider_id: string; masked_key: string; test: unknown; resumed_tasks: string[] }>(
        "/api/providers/key",
        payload,
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.providers });
      void client.invalidateQueries({ queryKey: keys.agents });
    },
  });
}

export function useDeleteKey() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (providerId: string) => api.delete<{ removed: boolean }>(`/api/providers/key/${providerId}`),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.providers }),
  });
}

export function useTestProvider() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: { provider_id: string; model?: string }) =>
      api.post<{ provider_id: string; status: string; detail: string }>("/api/providers/test", payload),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.providers }),
  });
}

export function useProviderModels(providerId: string) {
  return useQuery({
    queryKey: ["providers", providerId, "models"],
    queryFn: () => api.get<{ provider_id: string; models: string[]; source: string }>(
      `/api/providers/${providerId}/models`,
    ),
    enabled: Boolean(providerId),
    staleTime: 60_000,
  });
}

// --- planning ------------------------------------------------------------------------------

export function usePlanning() {
  return useQuery({
    queryKey: keys.planning,
    queryFn: () => api.get<PlanningState>("/api/planning/state"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useAnswerQuestion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: { question: string; answer: string }) => api.post("/api/planning/answer", payload),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.planning }),
  });
}

export function useHandoff() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ draft_gdd: string; blockers?: string[] }>("/api/planning/handoff"),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.planning }),
  });
}

export function useConfirmDesign() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: { confirmed_text: string }) =>
      api.post<{ finalised: boolean; task_ids?: string[] }>("/api/planning/confirm", payload),
    onSuccess: () => {
      void client.invalidateQueries();
    },
  });
}

export function useSeedTasks() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ created: string[] }>("/api/planning/seed-tasks"),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.board }),
  });
}

// --- assets, knowledge, git ------------------------------------------------------------------

export function useAssets() {
  return useQuery({
    queryKey: keys.assets,
    queryFn: () =>
      api.get<{
        assets: AssetRow[];
        categories: Record<string, number>;
        total_bytes: number;
        requests: Array<Record<string, unknown>>;
      }>("/api/assets"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useKnowledge(term: string) {
  return useQuery({
    queryKey: keys.knowledge(term),
    queryFn: () =>
      api.get<{ entries: KnowledgeRow[]; count: number; by_type: Record<string, number> }>(
        `/api/knowledge${query({ q: term })}`,
      ),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useGitLog(limit = 50) {
  return useQuery({
    queryKey: [...keys.gitLog, limit],
    queryFn: () =>
      api.get<{ initialised: boolean; branch: string; commits: GitCommit[]; status?: Record<string, unknown> }>(
        `/api/git/log${query({ limit })}`,
      ),
  });
}

export function useGitDiff(sha: string) {
  return useQuery({
    queryKey: ["git", "show", sha],
    queryFn: () => api.get<{ sha: string; diff: string; stat: string }>(`/api/git/show${query({ sha })}`),
    enabled: Boolean(sha),
  });
}

// --- preview, logs, design, notifications ----------------------------------------------------

export function usePreviewState() {
  return useQuery({
    queryKey: keys.preview,
    queryFn: () => api.get<PreviewState>("/api/preview/state"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function usePreviewGraph() {
  return useQuery({
    queryKey: keys.graph,
    queryFn: () => api.get<PreviewGraph>("/api/preview/graph"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useExportWeb() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ ok: boolean; detail: string; path: string }>("/api/preview/export"),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.preview }),
  });
}

export function useLogEvents(limit = 300) {
  return useQuery({
    queryKey: [...keys.logs, limit],
    queryFn: () =>
      api.get<{ events: Array<{ seq: number; type: string; at: string; payload: Record<string, unknown> }>; count: number }>(
        `/api/logs${query({ limit })}`,
      ),
  });
}

export function useTheme() {
  return useQuery({ queryKey: keys.theme, queryFn: () => api.get<ThemeResponse>("/api/design/theme") });
}

export function useNotifications() {
  return useQuery({
    queryKey: keys.notifications,
    queryFn: () => api.get<NotificationStatus>("/api/system/notifications"),
    refetchInterval: 30_000,
  });
}

export function useFirstRun() {
  return useQuery({ queryKey: keys.firstRun, queryFn: () => api.get<FirstRun>("/api/system/first-run") });
}

export function useSystemInfo() {
  return useQuery({
    queryKey: keys.systemInfo,
    queryFn: () => api.get<{ app: { name: string; version: string }; tools: Record<string, unknown>; paths: Record<string, string> }>(
      "/api/system/info",
    ),
  });
}

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => api.get<{ config: Record<string, unknown> }>("/api/settings") });
}

export function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.put<{ config: Record<string, unknown> }>("/api/settings", patch),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.settings }),
  });
}

// --- asset library actions ---------------------------------------------------------------------
//
// Every button on the Assets screen creates a *task* rather than editing a file. That is the whole
// point of the screen: a user asking for a better lantern sprite is asking the team for work, and
// the work then passes the Auditor and the human gate like any other. Anything that edited an
// asset in place would be a second path to approval, which this product does not have.

export type AssetAction =
  | { kind: "request"; name: string; detail?: string; assetKind?: string }
  | { kind: "regenerate"; path: string; reason?: string; agent?: "image_generator" | "audio_curator" }
  | { kind: "recurate"; path: string; reason?: string }
  | { kind: "fulfil"; requestId: string; path?: string }
  | { kind: "style"; note: string };

export function useStyleLock() {
  return useQuery({
    queryKey: ["assets", "style-lock"],
    queryFn: () => api.get<{ style: string; source: string; note: string }>("/api/assets/style-lock"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useIngestAsset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ folder, note }: { folder: string; note?: string }) =>
      api.post<{ added: number; total: number; note: string; files?: string[] }>("/api/assets/ingest", {
        source_dir: folder,
        kind: "",
        note,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.assets }),
  });
}

/**
 * One hook for the whole screen because the buttons are variations on one idea. It returns the
 * same `{task_id}` shape whichever action it ran, so the toast code stays in one place.
 */
export function useAssetActions() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (action: AssetAction): Promise<{ task_id: string; note?: string; request_id?: string }> => {
      if (action.kind === "request") {
        const created = await api.post<{ request: { request_id: string } }>("/api/assets/requests", {
          name: action.name,
          kind: action.assetKind ?? "sprite",
          description: action.detail ?? "",
        });
        return { task_id: "", request_id: created.request.request_id };
      }
      if (action.kind === "regenerate") {
        const result = await api.post<{ task: { task_id: string }; note?: string }>("/api/assets/regenerate", {
          asset_path: action.path,
          reason: action.reason ?? "Asked for from the asset library.",
          agent: action.agent ?? "image_generator",
        });
        return { task_id: result.task.task_id, note: result.note };
      }
      if (action.kind === "recurate") {
        const result = await api.post<{ task: { task_id: string } }>("/api/assets/request-recuration", {
          asset_path: action.path,
          reason: action.reason ?? "Asked for from the asset library.",
        });
        return { task_id: result.task.task_id };
      }
      if (action.kind === "fulfil") {
        const result = await api.post<{ ok: boolean; task_id?: string }>(
          `/api/assets/requests/${action.requestId}/fulfil`,
          { path: action.path ?? "", by: "human" },
        );
        return { task_id: result.task_id ?? "" };
      }
      const result = await api.put<{ style: string }>("/api/assets/style-lock", { style: action.note });
      return { task_id: "", note: `Style lock updated to: ${result.style.slice(0, 120)}` };
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.assets });
      void client.invalidateQueries({ queryKey: ["assets", "style-lock"] });
      void client.invalidateQueries({ queryKey: keys.board });
    },
  });
}

// --- knowledge base ---------------------------------------------------------------------------

export function useKnowledgeSearch() {
  return useMutation({
    mutationFn: (payload: { q: string; kind?: string; limit?: number }) =>
      api.post<{ results: KnowledgeRow[]; count: number; note: string; query: string }>(
        "/api/knowledge/search",
        payload,
      ),
  });
}

export function useKnowledgeStats() {
  return useQuery({
    queryKey: ["knowledge", "stats"],
    queryFn: () =>
      api.get<{
        entries: number;
        files: number;
        bytes: number;
        sources: string[];
        untrusted: string[];
        note: string;
      }>("/api/knowledge/stats"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useTranscripts() {
  return useQuery({
    queryKey: ["knowledge", "transcripts"],
    queryFn: () =>
      api.get<{ transcripts: Array<Record<string, unknown>>; count: number }>("/api/knowledge/transcripts"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

// --- logs and the live terminal ---------------------------------------------------------------

export function useStreamInfo() {
  return useQuery({
    queryKey: ["logs", "stream-info"],
    queryFn: () =>
      api.get<{
        websocket_path: string;
        replay_available: number;
        subscribers: number;
        last_seq: number;
        types: string[];
        note: string;
      }>("/api/logs/stream-info"),
  });
}

export function useContextStats() {
  return useQuery({
    queryKey: ["logs", "context-stats"],
    queryFn: () =>
      api.get<{
        gdd_tokens_full: number;
        gdd_tokens_sent: number;
        progress_tokens_full: number;
        progress_tokens_sent: number;
        saved_tokens: number;
        note: string;
        tasks_with_verdicts: number;
        average_score: number;
        declines: number;
        history_lines: number;
        gdd_sections: string[];
      }>("/api/logs/context-stats"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useFailurePatterns() {
  return useQuery({
    queryKey: ["logs", "failures"],
    queryFn: () => api.get<{ patterns: Array<Record<string, unknown>> }>("/api/logs/failures"),
  });
}

// --- git history -------------------------------------------------------------------------------

export function useGitStatus() {
  return useQuery({
    queryKey: keys.gitStatus,
    queryFn: () =>
      api.get<{
        initialised: boolean;
        identity: { name: string; email: string; available: string };
        status: { initialised: boolean; dirty: boolean; files: string[]; branch: string };
        branch: string;
      }>("/api/git/status"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useGitBranches() {
  return useQuery({
    queryKey: ["git", "branches"],
    queryFn: () => api.get<{ branches: string[]; current: string }>("/api/git/branches"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useTaskCommits() {
  return useQuery({
    queryKey: ["git", "task-commits"],
    queryFn: () =>
      api.get<{ commits: GitCommit[]; count: number }>("/api/git/task-commits"),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

// --- settings: the sections the Settings screen edits one at a time -----------------------------

export function useSettingsStorage() {
  return useQuery({
    queryKey: ["settings", "storage"],
    queryFn: () =>
      api.get<{
        studio_home: { path: string; bytes: number };
        projects_root: { path: string; bytes: number };
        projects: Array<{ name: string; path: string; bytes: number }>;
        note: string;
      }>("/api/settings/storage"),
  });
}

export function useAgentsFile() {
  return useQuery({
    queryKey: ["settings", "agents-file"],
    queryFn: () => api.get<{ path: string; exists: boolean; content: string; note: string }>("/api/settings/agents-file"),
  });
}

export function useSaveAgentsFile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (content: string) =>
      api.put<{ ok: boolean; reloaded?: boolean; note?: string }>("/api/settings/agents-file", { content }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.agents });
      void client.invalidateQueries({ queryKey: ["settings", "agents-file"] });
    },
  });
}

export function useReloadAgents() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ reloaded: boolean; agents: number; note: string }>("/api/settings/agents/reload"),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.agents }),
  });
}

export function useGodotStatus() {
  return useQuery({
    queryKey: ["settings", "godot"],
    queryFn: () =>
      api.get<{
        configured: string;
        version: string;
        detected: string[];
        works: boolean;
        message: string;
      }>("/api/settings/godot"),
  });
}

export function useSaveGodot() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => api.put<{ configured: string; works: boolean; message: string }>("/api/settings/godot", { executable: path }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["settings", "godot"] });
      void client.invalidateQueries({ queryKey: keys.settings });
    },
  });
}

export function useSaveGitSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => api.put<{ config: Record<string, unknown> }>("/api/settings/git", patch),
    onSuccess: () => client.invalidateQueries({ queryKey: ["settings", "git"] }),
  });
}

export function useTelegramTest() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ ok: boolean; detail: string; chat_id?: string }>("/api/settings/telegram/test"),
    onSuccess: () => client.invalidateQueries({ queryKey: ["settings", "notifications"] }),
  });
}

export function useTelegramDiscoverChat() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ chat_id: string; detail: string }>("/api/settings/telegram/chat-id"),
    onSuccess: () => client.invalidateQueries({ queryKey: ["settings", "notifications"] }),
  });
}

export function useDesignChecklist() {
  return useQuery({
    queryKey: ["design", "checklist"],
    queryFn: () =>
      api.get<{ items: Array<{ id: string; label: string; enforced_by: string }> }>("/api/design/checklist"),
  });
}

export function useDesignReport() {
  return useQuery({
    queryKey: ["design", "report"],
    queryFn: () =>
      api.get<{ markdown: string; summary: { screens: number; passing: number; failing: string[] } }>(
        "/api/design/report",
      ),
    retry: false,
  });
}

export function useGodotWindowModes() {
  return useQuery({
    queryKey: ["preview", "godot-window"],
    queryFn: () => api.get<{ modes: Record<string, string>; note: string }>("/api/preview/godot-window"),
  });
}

// --- agent detail: history, usage, and what the agent is actually told --------------------------

/**
 * A recorded run of one agent, as the history endpoint reports it. `success` comes from the
 * underlying attempt record, so a run that fell through to FALLBACK 1 shows the model that
 * actually answered rather than the one that was tried first.
 */
export type AgentHistoryRow = {
  task_id: string;
  agent_id?: string;
  status?: string;
  provider?: string;
  model?: string;
  success?: boolean;
  latency_ms?: number;
  tokens_in?: number;
  tokens_out?: number;
  at?: string;
  error?: string;
};

export function useAgentHistory(agentId: string) {
  return useQuery({
    queryKey: ["agent", agentId, "history"],
    queryFn: () =>
      api.get<{ history: AgentHistoryRow[]; failure_patterns: string[] }>(`/api/agents/${agentId}/history`),
    enabled: Boolean(agentId),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useAgentUsage(agentId: string) {
  return useQuery({
    queryKey: ["agent", agentId, "usage"],
    queryFn: () =>
      api.get<{
        agent_id: string;
        tasks: number;
        attempts: number;
        failed_attempts: number;
        tokens_in: number;
        tokens_out: number;
        providers: string[];
        models: string[];
        average_score: number | null;
        declines: number;
      }>(`/api/agents/${agentId}/usage`),
    enabled: Boolean(agentId),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

export function useAgentContextPreview(agentId: string, taskId = "") {
  return useQuery({
    queryKey: ["agent", agentId, "context", taskId],
    queryFn: () =>
      api.get<{
        agent_id: string;
        task_id: string;
        rendered: string;
        stats: { rendered_tokens: number; budget_tokens: number; omitted_count: number; task_kind?: string };
        omitted: string[];
        sections: Record<string, unknown>;
      }>(`/api/agents/${agentId}/context-preview${query({ task_id: taskId || undefined })}`),
    enabled: Boolean(agentId),
    retry: (count, error) => !(error instanceof ApiError && error.needsProject) && count < 2,
  });
}

// --- design documents: read, edit by hand, and see the file's own history -----------------------
//
// The design document and the story files are ordinary Markdown in the project folder, so this
// hook reads the folder listing first and then the files themselves. Only text the human owns is
// fetched - never a generated artifact - because the drawer and the terminal are where agent
// output belongs.

export type DesignDoc = {
  path: string;
  title: string;
  kind: "design" | "story" | "memory" | "other";
  content: string;
};

const DESIGN_PATTERNS = [
  { match: /(^|\/)gdd\.md$/i, kind: "design" as const },
  { match: /^story\//i, kind: "story" as const },
  { match: /^memory\/.*\.md$/i, kind: "memory" as const },
];

function classify(path: string): DesignDoc["kind"] {
  for (const rule of DESIGN_PATTERNS) if (rule.match.test(path)) return rule.kind;
  return "other";
}

function titleFor(path: string): string {
  const name = path.split("/").pop() ?? path;
  return name
    .replace(/\.md$/i, "")
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function useDesignDocs() {
  const { data: active } = useActiveProject();
  const projectId = active?.project.project_id ?? "";
  return useQuery({
    queryKey: ["design", "docs", projectId],
    enabled: Boolean(projectId),
    queryFn: async () => {
      const listing = await api.get<{ files: string[] }>(
        `/api/projects/${projectId}/files${query({ pattern: "**/*.md", limit: 400 })}`,
      );
      const wanted = listing.files
        .filter((path) => classify(path) !== "other")
        .sort((a, b) => {
          // The design document first, then story, then the memory files the agents keep.
          const order = { design: 0, story: 1, memory: 2, other: 3 } as const;
          return order[classify(a)] - order[classify(b)] || a.localeCompare(b);
        })
        .slice(0, 24);

      const documents = await Promise.all(
        wanted.map(async (path) => {
          const file = await api.get<{ path: string; content: string }>(
            `/api/projects/${projectId}/file${query({ path })}`,
          );
          return { path, title: titleFor(path), kind: classify(path), content: file.content };
        }),
      );
      return { documents };
    },
  });
}

export function useSaveDesign() {
  const client = useQueryClient();
  const { data: active } = useActiveProject();
  const projectId = active?.project.project_id ?? "";
  return useMutation({
    mutationFn: ({ path, content }: { path: string; content: string }) =>
      api.put<{ path: string; bytes: number; committed: boolean }>(`/api/projects/${projectId}/file`, {
        path,
        content,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["design", "docs"] });
      void client.invalidateQueries({ queryKey: keys.planning });
      void client.invalidateQueries({ queryKey: keys.activeProject });
    },
  });
}

export type FileHistoryCommit = {
  sha: string;
  short?: string;
  message?: string;
  author?: string;
  date?: string;
};

export function useDesignHistory(path: string) {
  return useQuery({
    queryKey: ["git", "file-history", path],
    queryFn: () =>
      api.get<{ path: string; commits: FileHistoryCommit[] }>(
        `/api/git/file-history${query({ path, limit: 20 })}`,
      ),
    enabled: Boolean(path),
    retry: false,
  });
}
