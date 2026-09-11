/**
 * Types for the parts of the API the UI actually renders.
 *
 * Kept deliberately close to the server's own models: `backend/core/models.py` is the source of
 * truth, and `docs/API.md` (generated from OpenAPI) is the reference when one of these looks
 * wrong. Fields are optional where the backend omits them rather than sending null, because the
 * UI then has to decide what to show instead of crashing on a missing key.
 */

export type RunStatus = "idle" | "running" | "paused" | "stopping";
export type TaskStatus =
  | "PENDING"
  | "IN_PROGRESS"
  | "SUBMITTED"
  | "AUDITING"
  | "NEEDS_HUMAN_REVIEW"
  | "APPROVED"
  | "REJECTED"
  | "FAILED"
  | "NEEDS_INTERVENTION";

export type AgentStatus = "idle" | "working" | "paused" | "blocked" | "offline" | "awaiting_key" | "disabled";

export type RunState = {
  status: RunStatus;
  project_id: string;
  project_name: string;
  ticks: number;
  uptime_s: number;
  last_tick_at: number;
  last_error: string;
  interval_s: number;
  last_outcome?: {
    dispatched?: string[];
    submitted?: string[];
    needs_review?: string[];
    paused?: string[];
    resumed?: string[];
    created?: string[];
    errors?: string[];
    reasoning?: string;
  };
};

export type Task = {
  task_id: string;
  project_id: string;
  phase: number;
  created_by: string;
  assigned_to: string;
  status: TaskStatus;
  title: string;
  instruction: string;
  kind: string;
  priority: number;
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  retry_count: number;
  decline_count: number;
  human_rejections: number;
  provider_used: string;
  model_used: string;
  output: string;
  artifacts: string[];
  expected_outputs: string[];
  screenshots: string[];
  dependencies: string[];
  file_claims: string[];
  context_files: string[];
  errors: string[];
  notes: string[];
  comments: ChatMessage[];
  auditor_verdict: Verdict | null;
  human_decision: HumanDecision | null;
  attempt_history?: Array<Record<string, unknown>>;
};

export type Verdict = {
  task_id: string;
  verdict: "APPROVED" | "DECLINED" | "NEEDS_HUMAN_REVIEW" | string;
  score: number;
  skill_flags: Record<string, string[]>;
  screenshots_reviewed: string[];
  fix_note: string;
  checked_at?: string;
  provider?: string;
  model?: string;
};

export type HumanDecision = {
  decision: "APPROVED" | "REJECTED" | string;
  note: string;
  decided_at: string;
  overrode_auditor: boolean;
  commit_sha?: string;
};

export type ChatMessage = {
  message_id: string;
  role: string;
  agent_id?: string | null;
  content: string;
  created_at: string;
  meta?: Record<string, unknown>;
};

export type Lane = {
  id: string;
  title: string;
  statuses: string[];
  count: number;
  tasks: Task[];
};

export type Board = {
  lanes: Lane[];
  counts: Record<string, number>;
  phase: PhaseProgress;
  review_count: number;
  run_state: RunState;
};

export type PhaseProgress = {
  phase: number;
  phases_total: number;
  approved: number;
  total: number;
  percent: number;
};

export type Project = {
  project_id: string;
  name: string;
  path: string;
  mode: "fresh" | "existing" | string;
  genre: string;
  art_style: string;
  perspective: string;
  godot_version: string;
  phase: number;
  phases_total: number;
  status: string;
  summary: string;
  created_at: string;
  last_opened: string;
  git_initialised: boolean;
  godot_project_path: string;
};

export type ProjectStats = {
  tasks_total: number;
  tasks_approved: number;
  tasks_open: number;
  tasks_needing_review: number;
  tasks_paused: number;
  gdd_sections: string[];
  asset_requests_open: number;
  knowledge_entries: number;
};

/**
 * `/api/projects/{id}/overview` (and the `overview` key of `/api/projects/active`) returns the
 * project record with `phase` replaced by a progress object, so it is spelled out as an Omit
 * rather than intersecting the integer from `Project`.
 */
export type Overview = Omit<Project, "phase"> & {
  exists: boolean;
  stats: ProjectStats;
  phase: PhaseProgress;
  phases: Array<{ id: string; name: string; goal: string }>;
  git: { initialised: boolean; branch?: string; dirty?: boolean };
  gdd_summary: string;
  memory_files?: { progress_lines: number; gdd_sections: string[] };
  board?: Board;
};

export type ActiveProject = {
  project: Project;
  overview: Overview;
  board?: Board;
  run_state?: RunState;
};

export type ChainEntry = {
  slot: string;
  provider: string;
  provider_name?: string;
  model: string;
  usable?: boolean;
  verified?: string;
  usable_options?: number;
  status?: string;
};

export type AgentRow = {
  agent_id: string;
  name: string;
  role?: string;
  description?: string;
  colour?: string;
  color?: string;
  enabled: boolean;
  chain: ChainEntry[];
  missing_providers: string[];
  status?: AgentStatus;
  current_task?: string;
  last_task?: string | null;
  provider?: string;
  model?: string;
  tasks_completed?: number;
  tasks_failed?: number;
  avg_latency_ms?: number;
  system_prompt?: string;
  temperature?: number;
  skills?: string[];
};

export type ProviderRow = {
  id: string;
  name: string;
  kinds: string[];
  requires_key: boolean;
  keyless: boolean;
  configured: boolean;
  has_key: boolean;
  masked_key: string;
  last_test_status: string;
  last_test_at: string;
  verified: string;
  verified_on: string;
  free_tier: string;
  signup_url: string;
  docs_url: string;
  notes: string;
  default_models: string[];
  free_forever: boolean;
  one_time_credit: boolean;
  substitution?: { substitute: string; model?: string; reason?: string } | null;
  usage: {
    provider: string;
    requests_today: number;
    requests_minute: number;
    tokens_today: number;
    limit_day: number | null;
    limit_minute: number | null;
    limit_kind: string;
    percent: number | null;
    free_forever: boolean;
    in_cooldown: boolean;
    cooldown_remaining_s: number;
    last_status: string;
    last_error: string;
  };
  override: Record<string, unknown> | null;
  enabled: boolean;
};

export type PlanningState = {
  state: Record<string, unknown>;
  title: string;
  genre: string;
  genre_scores?: Record<string, number>;
  mechanics: Array<{ name: string; rule: string }>;
  characters: Array<{
    name: string;
    role: string;
    personality: string;
    abilities: string[];
    ai_behaviour: string;
  }>;
  art_direction: string;
  level_count: number | null;
  play_length_minutes: number | null;
  ledgers: unknown;
  ledger: {
    mechanics_complete: boolean;
    characters_complete: boolean;
    art_complete: boolean;
    scope_complete: boolean;
    tech_complete: boolean;
    completion: number;
    missing: string[];
  };
  allowed: boolean;
  blockers: string[];
  questions: Array<{ theme: string; text: string; index: string }>;
  chat: ChatMessage[];
  concept: string;
  confirmed: boolean;
  started: boolean;
};

export type GraphNode = {
  id: string;
  label: string;
  role: string;
  colour: string;
  status: AgentStatus;
  enabled: boolean;
  primary: string;
  active_task: string;
  active_title: string;
  provider: string;
  tasks_completed: number;
  avg_latency_ms: number;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
  animated: boolean;
};

export type PreviewGraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  review_count: number;
  run_state: RunState;
};

export type PreviewState = {
  project_id: string;
  mode: "graph" | "game";
  game: { available: boolean; url: string; path: string; bytes: number };
  milestones: Array<{ phase: number; label: string; approved_at: string; export_path?: string }>;
  last_milestone: Record<string, unknown> | null;
  explanation: string;
};

export type NotificationRow = {
  event_id: string;
  kind: string;
  title: string;
  body: string;
  project_id: string;
  task_id: string;
  created_at: string;
  delivered: Record<string, boolean>;
  delivery_detail: string[];
  read: boolean;
};

export type NotificationStatus = {
  telegram: {
    enabled: boolean;
    has_token: boolean;
    chat_id: string;
    ready: boolean;
    detail: string;
    bot_username: string;
  };
  windows_toasts: { enabled: boolean; platform_supported: boolean };
  notify_on: string[];
  quiet_hours: string;
  quiet_now: boolean;
  unread: number;
  recent: NotificationRow[];
};

export type LogLine = {
  seq: number;
  at: string;
  level: string;
  logger: string;
  message: string;
  task_id?: string;
  agent_id?: string;
  project_id?: string;
};

export type FirstRun = {
  first_run_complete: boolean;
  projects_root: string;
  projects_root_exists: boolean;
  projects_root_writable: boolean;
  godot_configured: boolean;
  godot_detected: string[];
  git_available: boolean;
  git_identity: { available: string; name: string; email: string };
  has_any_key: boolean;
  has_project: boolean;
  keyless_providers: Array<{ id: string; name: string; note: string; docs_url: string }>;
  steps: Array<{ id: string; label: string; required: boolean }>;
};

export type ThemeResponse = {
  theme: Record<string, unknown>;
  path: string;
  contrast: Array<{
    pair: string;
    foreground: string;
    background: string;
    contrast: number;
    required: number;
    passes: boolean;
  }>;
  note: string;
};

export type GitCommit = {
  sha: string;
  short: string;
  subject: string;
  author: string;
  email: string;
  date: string;
  files?: string[];
  task_id?: string;
};

export type AssetRow = {
  name: string;
  path: string;
  kind: string;
  bytes: number;
  modified: string;
  url?: string;
  tags?: string[];
  used_by?: string[];
};

export type KnowledgeRow = {
  entry_id: string;
  title: string;
  kind: string;
  source: string;
  summary: string;
  created_at: string;
  tags: string[];
  path?: string;
};
