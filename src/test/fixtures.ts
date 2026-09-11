/**
 * A fake backend for the frontend tests, built from the real response shapes.
 *
 * The fixtures here are copied from live responses captured by the API probes, not invented. That
 * matters: a fixture that says `{items: [...]}` for an endpoint that really returns
 * `{agents: [...]}` makes a test pass against a dashboard that would show nothing in production,
 * which is worse than no test.
 *
 * Only the endpoints the nine views read are implemented. Anything else throws, so a view that
 * starts calling a new endpoint fails the test loudly instead of silently rendering an empty state.
 */

export type Json = Record<string, unknown>;

const ACTIVE_PROJECT = {
  project: {
    project_id: "proj_test",
    name: "Harbour Lights",
    path: "/tmp/pulseg-test/harbour-lights",
    mode: "fresh",
    genre: "platformer",
    art_style: "pixel art",
    perspective: "side-on",
    godot_version: "4.3",
    phase: 1,
    status: "building",
    created_at: "2026-09-11T10:00:00+00:00",
    git_initialised: true,
  },
  overview: {
    phase: { phase: 1, phases_total: 6, approved: 2, total: 6, percent: 33 },
    phases: [
      { id: "0", name: "Planning", goal: "Concept and signed-off design" },
      { id: "1", name: "Playable core", goal: "A controllable character that runs" },
    ],
    stats: { tasks_total: 6, tasks_approved: 2, tasks_review: 1, tasks_pending: 3 },
    git: { initialised: true, branch: "phase/1-playable-core", dirty: true },
    gdd_summary: "A cosy platformer about a lighthouse keeper guiding boats home.",
    memory_files: { progress_lines: 12, gdd_sections: ["SUMMARY", "MECHANICS"] },
  },
  board: {
    lanes: [],
    counts: {},
    phase: { phase: 1, phases_total: 6, approved: 2, total: 6, percent: 33 },
    review_count: 1,
  },
  run_state: runState(),
};

function runState() {
  return {
    status: "running",
    project_id: "proj_test",
    project_name: "Harbour Lights",
    ticks: 42,
    uptime_s: 120,
    last_tick_at: 1757584000,
    last_error: "",
    interval_s: 2,
    last_outcome: { dispatched: ["TASK_004"], created: ["TASK_005"] },
  };
}

const TASK = {
  task_id: "TASK_004",
  project_id: "proj_test",
  phase: 1,
  created_by: "prompter",
  assigned_to: "programmer",
  status: "NEEDS_HUMAN_REVIEW",
  retry_count: 0,
  priority: 100,
  title: "Implement the lamp beam",
  instruction: "Create the lamp beam as an 80 degree cone that sweeps while the aim key is held.",
  dependencies: [],
  context_files: ["memory/gdd.md"],
  expected_outputs: ["godot_project/scripts/lamp.gd"],
  file_claims: ["godot_project/scripts/lamp.gd"],
  screenshots: ["screenshots/task_TASK_004_20260911.png"],
  artifacts: ["godot_project/scripts/lamp.gd"],
  provider_used: "demo",
  model_used: "demo-1",
  created_at: "2026-09-11T10:10:00+00:00",
  updated_at: "2026-09-11T10:12:00+00:00",
  audit_attempts: 1,
  human_rejections: 0,
  comments: [],
  output: "RESULT: wrote godot_project/scripts/lamp.gd\nRISKS: not run in Godot yet",
  auditor_verdict: {
    task_id: "TASK_004",
    verdict: "APPROVED_WITH_NOTES",
    score: 8,
    skill_flags: { no_ai_slop: ["two decorative lists"] },
    screenshots_reviewed: ["task_TASK_004_20260911.png"],
    fix_note: "Consider naming the sweep speed constant.",
  },
  human_decision: null,
  human_note: "",
};

const AGENTS = [
  {
    agent_id: "planning_agent",
    name: "Planning Agent",
    role: "Pre-build intake, /grillme interrogation",
    enabled: true,
    chain: [
      { slot: "primary", provider: "mistral", model: "mistral-large-latest", usable: true },
      { slot: "fallback_1", provider: "google", model: "gemini-2.5-flash", usable: true },
    ],
    usable_options: 4,
    status: "idle",
    missing_providers: [],
  },
  {
    agent_id: "programmer",
    name: "Programmer",
    role: "Writes the Godot scenes and GDScript",
    enabled: true,
    chain: [{ slot: "primary", provider: "mistral", model: "codestral-latest", usable: true }],
    usable_options: 3,
    status: "working",
    missing_providers: [],
  },
];

function agentStates() {
  return {
    agents: AGENTS.map((agent) => ({
      agent_id: agent.agent_id,
      name: agent.name,
      role: agent.role,
      colour: "#7FC6A4",
      status: agent.status,
      current_task: agent.status === "working" ? "TASK_004" : "",
      last_task: "TASK_003",
      provider: agent.chain[0].provider,
      model: agent.chain[0].model,
      tasks_completed: 3,
      tasks_failed: 0,
      avg_latency_ms: 1200,
      tokens_in: 12000,
      tokens_out: 4000,
      note: "",
    })),
  };
}

/**
 * Route a request path to a fixture. Throws on anything unmapped - see the note at the top.
 */
/**
 * Fixtures a test has changed, by route.
 *
 * Most tests want the captured response exactly as it came off the wire. A few need one field in a
 * different state - the planning rail has to be checked both when the intake read an answer and
 * when it could not. Rather than duplicating a whole response, a test patches the field it is
 * about, and `installFetch` clears the patches so they cannot leak into the next test.
 */
const overrides = new Map<string, Json>();

/**
 * Patch one fixture for the next install. The patch is merged, so a test names only what it changes.
 */
export function stubFixture(path: string, patch: Json): void {
  const base = fixtureFor(path);
  overrides.set(path, { ...base, ...patch });
}

/**
 * The response for one API call.
 *
 * `path` is the pathname with the query string removed, and `params` carries the query, because a
 * handful of endpoints (the project file reads, the knowledge search) answer differently per
 * parameter and a fixture that ignored them would let a broken query pass the tests.
 */
export function fixtureFor(path: string, params: URLSearchParams = new URLSearchParams()): Json {
  const [route] = path.split("?");
  const exact: Record<string, Json> = {
    "/api/projects/active": ACTIVE_PROJECT,
    "/api/projects": { projects: [ACTIVE_PROJECT.project], active_project_id: "proj_test" },
    "/api/tasks/board": {
      lanes: [
        { id: "pending", title: "Pending", statuses: ["PENDING"], count: 1, tasks: [{ ...TASK, task_id: "TASK_005", status: "PENDING", title: "Add checkpoint lanterns" }] },
        { id: "in_progress", title: "In Progress", statuses: ["IN_PROGRESS"], count: 0, tasks: [] },
        { id: "auditing", title: "Auditing", statuses: ["AUDITING"], count: 0, tasks: [] },
        { id: "review", title: "Needs Your Review", statuses: ["NEEDS_HUMAN_REVIEW"], count: 1, tasks: [TASK] },
        { id: "approved", title: "Approved", statuses: ["APPROVED"], count: 1, tasks: [{ ...TASK, task_id: "TASK_001", status: "APPROVED", title: "Freeze the design summary" }] },
        { id: "declined", title: "Declined - Retrying", statuses: ["REJECTED"], count: 0, tasks: [] },
        { id: "intervention", title: "Needs Intervention", statuses: ["NEEDS_INTERVENTION"], count: 0, tasks: [] },
      ],
      counts: { PENDING: 1, NEEDS_HUMAN_REVIEW: 1, APPROVED: 1 },
      phase: ACTIVE_PROJECT.overview.phase,
      review_count: 1,
    },
    "/api/tasks/review": {
      items: [
        {
          task_id: "TASK_004",
          title: "Implement the lamp beam",
          assigned_to: "programmer",
          verdict: "APPROVED_WITH_NOTES",
          score: 8,
          phase: 1,
          waiting_s: 120,
          fix_note: "Consider naming the sweep speed constant.",
          screenshots: 1,
          declined: false,
        },
      ],
      count: 1,
      unreviewed_artifacts: 0,
    },
    "/api/logs/run-state": { run_state: runState(), recovered: true },
    "/api/agents": {
      agents: AGENTS,
      raw: AGENTS.map((agent) => ({ id: agent.agent_id, name: agent.name, role: agent.role })),
      problems: [],
      missing_providers: [],
      provider_options: [
        {
          id: "mistral",
          name: "Mistral",
          requires_key: true,
          free_forever: true,
          one_time_credit: false,
          verified: "2026-09-11",
          models: ["mistral-large-latest", "codestral-latest"],
          signup_url: "https://console.mistral.ai/",
          docs_url: "https://docs.mistral.ai/",
          free_tier: "Experiment tier",
          limits: "1 request/second",
          notes: "",
          supports_vision: false,
        },
      ],
      model_catalogue: { mistral: ["mistral-large-latest", "codestral-latest"] },
    },
    "/api/agents/states": agentStates(),
    "/api/agents/programmer": {
      id: "programmer",
      name: "Programmer",
      role: "Writes the Godot scenes and GDScript",
      description: "Turns an approved design into working Godot 4 scenes and scripts.",
      icon: "code",
      color: "#7FC6A4",
      enabled: true,
      primary: { provider: "mistral", model: "codestral-latest", note: "" },
      fallbacks: [{ provider: "openrouter", model: "qwen3-coder:free", note: "" }],
      system_prompt: "You are the Programmer for PulseG Studio.",
      system_prompt_file: "",
      skills: ["no_ai_slop"],
      capabilities: ["code"],
      temperature: 0.2,
      max_tokens: 8000,
      timeout_s: 240,
      max_decline_rotations: 3,
      chain_entries: [
        { slot: "primary", provider: "mistral", provider_name: "Mistral", model: "codestral-latest", usable: true, verified: "2026-09-11", usable_options: 3, status: "ready" },
        { slot: "fallback_1", provider: "openrouter", provider_name: "OpenRouter", model: "qwen3-coder:free", usable: true, verified: "2026-09-11", usable_options: 2, status: "ready" },
      ],
      history: [],
      recent_verdicts: [],
    },
    "/api/agents/programmer/history": { history: [], failure_patterns: [] },
    "/api/agents/programmer/usage": {
      agent_id: "programmer",
      tasks: 4,
      attempts: 5,
      failed_attempts: 1,
      tokens_in: 24000,
      tokens_out: 8000,
      providers: ["mistral", "demo"],
      models: ["codestral-latest"],
      average_score: 7.5,
      declines: 1,
    },
    "/api/providers": {
      // Copied from a live /api/providers row: this endpoint's shape is the widest in the API and
      // every field here is read by Settings, Overview or the QuotaMeter.
      providers: [
        {
          id: "mistral",
          name: "Mistral",
          has_key: true,
          configured: true,
          keyless: false,
          enabled: true,
          masked_key: "****abcd",
          requires_key: true,
          verified: "2026-09-11",
          verified_on: "2026-09-11",
          kinds: ["chat"],
          free_forever: true,
          one_time_credit: false,
          supports_vision: false,
          free_tier: "Experiment tier",
          limits: "1 request/second",
          notes: "",
          docs_url: "https://docs.mistral.ai/",
          signup_url: "https://console.mistral.ai/",
          default_models: ["mistral-large-latest", "codestral-latest"],
          substitution: "",
          override: {},
          quota_visible: true,
          last_test_at: "2026-09-11T10:00:00+00:00",
          last_test_status: "valid",
          usage: {
            provider: "mistral",
            requests_today: 12,
            requests_minute: 0,
            tokens_today: 42000,
            limit_day: 100,
            limit_minute: 60,
            limit_kind: "requests",
            ratio: 0.12,
            percent: 12,
            free_forever: true,
            in_cooldown: false,
            cooldown_remaining_s: 0,
            last_status: "ok",
            last_error: "",
            verified: "verified",
          },
        },
      ],
      verified_on: "2026-09-11",
      substitutions: {},
      llm_options: [{ id: "mistral", name: "Mistral" }],
      aggregate_quota_percent: 12,
      vault_file: "/tmp/pulseg-test/home/keys.enc",
    },
    "/api/planning/state": {
      state: { concept: "A cosy platformer about a lighthouse keeper.", title: "Harbour Lights", genre: "platformer" },
      ledger: {
        mechanics_complete: true,
        characters_complete: false,
        art_complete: true,
        scope_complete: false,
        tech_complete: true,
        completion: 0.6,
        missing: ["no characters defined yet"],
        notes: [],
        ready: false,
      },
      allowed: false,
      blockers: ["no characters defined yet"],
      checklist: "- [ ] every character has role, personality, abilities and behaviour",
      genre: "platformer",
      questions: [{ theme: "characters", text: "Who does the keeper meet?", index: "1" }],
      chat: [],
      concept: "A cosy platformer about a lighthouse keeper.",
      confirmed: false,
      started: false,
      // Empty until the human answers something: the rail shows nothing until there is a read to
      // report. `/api/planning/state` sends {} here, never null, which is what the view checks.
      extraction: {},
    },
    "/api/knowledge": { entries: [], count: 0, by_type: {}, by_trust: {} },
    "/api/knowledge/stats": { entries: 0, files: 0, bytes: 0, sources: [], untrusted: [], note: "" },
    "/api/knowledge/transcripts": { transcripts: [], count: 0 },
    "/api/git/log": {
      initialised: true,
      branch: "phase/1-playable-core",
      commits: [
        {
          sha: "18f2b65b05c15b2fe8b20d61f184560b30c2a139",
          short: "18f2b65",
          subject: "documenter: Freeze the design summary",
          author: "PulseG Studio",
          date: "2026-09-11T10:55:13+00:00",
        },
      ],
      status: { initialised: true, dirty: true, files: ["M memory/gdd.md"], branch: "phase/1-playable-core" },
    },
    "/api/git/status": {
      initialised: true,
      identity: { name: "PulseG Studio", email: "studio@pulseg.local", available: "true" },
      status: { initialised: true, dirty: true, files: ["M memory/gdd.md"], branch: "phase/1-playable-core" },
      branch: "phase/1-playable-core",
    },
    "/api/git/branches": { branches: ["main", "phase/1-playable-core"], current: "phase/1-playable-core" },
    "/api/git/task-commits": {
      commits: [{ sha: "18f2b65", short: "18f2b65", task_id: "TASK_001", message: "documenter: Freeze the design summary", agent: "documenter", date: "2026-09-11T10:55:13+00:00" }],
      count: 1,
    },
    "/api/settings": {
      config: {
        version: 1,
        first_run_complete: true,
        projects_root: "/tmp/pulseg-test/projects",
        active_project_id: "proj_test",
        godot: { executable: "/usr/bin/godot", version: "4.3", detected: [] },
        git: { user_name: "PulseG Studio", user_email: "studio@pulseg.local", auto_init: true, branch_per_phase: true, commit_on_approval_only: true },
        notifications: { telegram_enabled: false, windows_toasts: true },
        runtime: { max_parallel: 3, interval_s: 2 },
        ui: { rail_open: true },
      },
      paths: {
        studio_home: "/tmp/pulseg-test/home",
        projects_root: "/tmp/pulseg-test/projects",
        config_file: "/tmp/pulseg-test/home/config.yaml",
        agents_file: "/tmp/pulseg-test/home/agents.yaml",
        theme_file: "/tmp/pulseg-test/home/theme.json",
        vault_file: "/tmp/pulseg-test/home/keys.enc",
      },
      git: { available: true, detected: [], project: { initialised: true, branch: "main" } },
      godot: { configured: true, version: "4.3", detected: ["/usr/bin/godot"], ffmpeg: { available: true, path: "/usr/bin/ffmpeg" } },
    },
    "/api/settings/git": {
      global: { available: true, name: "PulseG Studio", email: "studio@pulseg.local" },
      project: { initialised: true, identity: { name: "", email: "", available: true }, branch: "main", status: {} },
      available: true,
      config: { user_name: "PulseG Studio", user_email: "studio@pulseg.local", auto_init: true, branch_per_phase: true, commit_on_approval_only: true },
    },
    "/api/settings/godot": { configured: true, version: "4.3", detected: ["/usr/bin/godot"], works: true, message: "" },
    // GET, and the shape the route really returns: the candidates it found and the configured path.
    "/api/settings/godot/detect": { candidates: ["/usr/bin/godot"], configured: "/usr/bin/godot" },
    "/api/settings/storage": {
      studio_home: { path: "/tmp/pulseg-test/home", bytes: 1024 },
      projects_root: { path: "/tmp/pulseg-test/projects", bytes: 20480 },
      projects: [],
      note: "",
    },
    "/api/settings/agents-file": { path: "/tmp/pulseg-test/home/agents.yaml", exists: true, content: "agents:\n  - id: programmer\n", note: "" },
    "/api/settings/providers-file": { path: "/tmp/pulseg-test/home/providers.yaml", exists: false, content: "", note: "" },
    "/api/settings/notifications": {
      telegram: { enabled: false, has_token: false, chat_id: "", ready: false, detail: "No token yet.", bot_username: "" },
      windows_toasts: { enabled: true, platform_supported: true },
      notify_on: ["needs_review", "needs_intervention", "chain_exhausted", "phase_complete"],
      quiet_hours: { enabled: false, start: "22:00", end: "07:00" },
      quiet_now: false,
      unread: 1,
      recent: [
        {
          event_id: "notif_1",
          kind: "needs_review",
          title: "TASK_004 is waiting for your review",
          body: "The Auditor approved it with notes.",
          project_id: "proj_test",
          task_id: "TASK_004",
          created_at: "2026-09-11T10:12:00+00:00",
          delivered: { log: true, telegram: false, windows_toast: false },
          delivery_detail: ["Telegram is not configured, so only the in-app list was updated."],
        },
      ],
    },
    "/api/system/first-run": {
      first_run_complete: true,
      projects_root: "/tmp/pulseg-test/projects",
      projects_root_exists: true,
      projects_root_writable: true,
      godot_configured: true,
      godot_detected: ["/usr/bin/godot"],
      git_available: true,
      git_identity: { available: true, name: "PulseG Studio", email: "studio@pulseg.local" },
      has_any_key: true,
      has_project: true,
      keyless_providers: [],
      steps: [{ id: "projects_root", label: "Choose where projects live", required: true }],
    },
    "/api/system/notifications": {
      telegram: { enabled: false, has_token: false, chat_id: "", ready: false, detail: "No token yet.", bot_username: "" },
      windows_toasts: { enabled: true, platform_supported: true },
      notify_on: ["needs_review"],
      quiet_hours: { enabled: false, start: "22:00", end: "07:00" },
      quiet_now: false,
      unread: 0,
      recent: [
        {
          event_id: "notif_1",
          kind: "needs_review",
          title: "TASK_004 is waiting for your review",
          body: "The Auditor approved it with notes.",
          project_id: "proj_test",
          task_id: "TASK_004",
          created_at: "2026-09-11T10:12:00+00:00",
          delivered: { log: true, telegram: false, windows_toast: false },
          delivery_detail: ["Telegram is not configured, so only the in-app list was updated."],
        },
      ],
    },
    "/api/design/theme": { theme: {}, path: "/tmp/pulseg-test/home/theme.json", contrast: [], note: "" },
    "/api/design/report": {
      markdown: "# Design taste audit\n\n9 of 9 screens pass.",
      summary: { screens: 9, passing: 9, failing: [] },
    },
    "/api/design/checklist": { items: [{ id: "one_primary_action", label: "One primary action per screen", enforced_by: "data-primary-action" }] },
    "/api/preview/state": { project_id: "proj_test", mode: "graph", game: { available: false, url: "", path: "", bytes: 0 }, milestones: [], last_milestone: null, explanation: "No web export yet." },
    "/api/preview/graph": { nodes: AGENTS.map((agent) => ({ id: agent.agent_id, label: agent.name, role: agent.role, status: agent.status })), edges: [], active_count: 1, review_count: 1, run_state: runState(), updated_at: "2026-09-11T10:12:00+00:00" },
    "/api/assets": {
      assets: [
        {
          path: "assets/sprites/lighthouse_keeper.png",
          kind: "sprite",
          original: "generated",
          name: "lighthouse_keeper.png",
          bytes: 2048,
          size_bytes: 2048,
          modified: "2026-09-11T10:00:00+00:00",
          url: "/media/assets/sprites/lighthouse_keeper.png",
          audio: false,
          exists: true,
        },
        {
          path: "assets/audio/bgm/waves.ogg",
          kind: "bgm",
          original: "user",
          name: "waves.ogg",
          bytes: 91234,
          size_bytes: 91234,
          modified: "2026-09-11T09:40:00+00:00",
          url: "/media/assets/audio/bgm/waves.ogg",
          audio: true,
          exists: true,
        },
      ],
      categories: { sprite: 1, bgm: 1 },
      total_bytes: 93282,
      requests: [],
    },
    "/api/assets/requests": { requests: [], open: 0 },
    "/api/assets/style-lock": { style: "Warm 32x32 pixel art, dusk palette", source: "human", note: "" },
    "/api/tasks/TASK_004": TASK,
    "/api/tasks/TASK_004/attempts": { attempts: [], errors: [], summary: { provider: "demo", model: "demo-1" }, retry_count: 0 },
    "/api/tasks/TASK_004/dependencies": { dependencies: [], dependents: [], unblocked: [] },
    "/api/tasks/TASK_004/screenshots": { screenshots: ["screenshots/task_TASK_004_20260911.png"], notes: "" },
    "/api/tasks/TASK_004/output": { task_id: "TASK_004", output: TASK.output, artifacts: TASK.artifacts, provider: "demo", model: "demo-1" },
    "/api/tasks/TASK_004/audit-report": { report: "# Audit\n\nScore 8/10.", verdict: TASK.auditor_verdict },
    "/api/logs": {
      // Exactly the envelope the event bus publishes: {seq, type, at, payload}. A log line carries
      // level/logger/message/agent_id/task_id, which is what the Live Terminal colours by agent.
      events: [
        {
          seq: 1,
          type: "task_created",
          at: "2026-09-11T10:10:00+00:00",
          payload: { task_id: "TASK_004", agent_id: "prompter", message: "Queued TASK_004: Implement the lamp beam" },
        },
        {
          seq: 2,
          type: "log",
          at: "2026-09-11T10:11:00+00:00",
          payload: {
            level: "INFO",
            logger: "pulseg.agents.programmer",
            message: "wrote godot_project/scripts/lamp.gd",
            agent_id: "programmer",
            task_id: "TASK_004",
            phase: 1,
          },
        },
      ],
      count: 2,
      stats: { subscribers: 1, ring_size: 500, last_seq: 2 },
    },
    "/api/logs/failures": { patterns: [] },
    "/api/logs/stream-info": { websocket_path: "/ws/events", replay_available: true, subscribers: 1, last_seq: 2, types: ["task_created"], note: "" },
    "/api/logs/context-stats": {
      gdd_tokens_full: 900,
      gdd_tokens_sent: 300,
      progress_tokens_full: 1200,
      progress_tokens_sent: 200,
      saved_tokens: 1600,
      note: "ADHD context filter",
      tasks_with_verdicts: 4,
      average_score: 7.5,
      declines: 1,
      history_lines: 40,
      gdd_sections: ["SUMMARY", "MECHANICS"],
    },
    "/api/knowledge/search": { results: [], count: 0, query: "", note: "" },
    "/api/git/file-history": { path: "memory/gdd.md", commits: [] },
    "/api/git/show": { sha: "18f2b65", markdown: "# Commit\n", diff: "" },
  };

  if (overrides.has(route)) return overrides.get(route) as Json;
  if (route in exact) return exact[route];

  // Prefix rules for the few endpoints with an id in the middle.
  const projectFile = route.match(/^\/api\/projects\/[^/]+\/file$/);
  if (projectFile) {
    const wanted = params.get("path") ?? "";
    const DOCS: Record<string, { title: string; body: string }> = {
      "memory/gdd.md": {
        title: "Game Design Document",
        body: "# Harbour Lights\n\n## SUMMARY\n\nA cosy platformer about a lighthouse keeper who guides boats home through fog by night.\n\n## MECHANICS\n\n- The lamp beam sweeps in an 80 degree cone while the aim key is held.\n",
      },
      "memory/progress.md": {
        title: "Progress",
        body: "# Progress\n\n- TASK_001 approved: the keeper walks and jumps.\n- TASK_004 submitted: the lamp beam sweeps.\n",
      },
      "story/outline.md": {
        title: "Story Outline",
        body: "# Story\n\n## Act 1\n\nThe keeper finds the harbour dark and the lighthouse unlit.\n",
      },
    };
    const found = DOCS[wanted];
    if (!found) return { path: wanted, content: "" };
    return { path: wanted, content: found.body, title: found.title, bytes: found.body.length };
  }

  if (route.endsWith("/files")) {
    // The project file listing. Only Markdown, which is what the design screen asks for.
    return {
      files: ["memory/gdd.md", "memory/progress.md", "story/outline.md"],
      count: 3,
      truncated: false,
    };
  }

  if (route.startsWith("/api/knowledge/entry")) return { entry: {}, markdown: "" };
  if (route.startsWith("/api/tasks/") && route.endsWith("/comments")) return { comments: [] };
  if (route.startsWith("/api/design/audit")) return { screens: [], summary: { screens: 0, passing: 0, failing: [] }, result: {}, theme: {}, views_dir: "", written: "" };
  if (route.startsWith("/api/providers/quota")) return { providers: [], aggregate_percent: 12, warnings: [] };
  if (route.startsWith("/api/preview/godot-window")) return { modes: { engine: "headless", os: "window" }, note: "" };
  if (route.startsWith("/api/planning/gdd")) return { markdown: "# Harbour Lights\n\n## SUMMARY\n\nA cosy platformer.", sections: [{ title: "SUMMARY", body: "A cosy platformer." }], summary: "A cosy platformer." };
  if (route.startsWith("/api/planning/design-doc")) return { markdown: "# Harbour Lights\n\n## SUMMARY\n\nA cosy platformer.", sections: [{ title: "SUMMARY", body: "A cosy platformer." }], versioned_by: "git" };
  if (route.startsWith("/api/planning/questions")) return { questions: [{ theme: "characters", text: "Who does the keeper meet?", index: "1" }], text: "1. [characters] Who does the keeper meet?" };

  throw new Error(
    `No fixture for ${route}. Add it to src/test/fixtures.ts - a view that reads an unmapped ` +
      "endpoint would otherwise render an empty state and the test would pass against a broken screen.",
  );
}

/** Install the fixture router as global fetch. Returns a restore function. */
export function installFetch(): () => void {
  overrides.clear();
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const [route, search = ""] = path.split("?");
    let payload: Json;
    try {
      payload = fixtureFor(route, new URLSearchParams(search));
    } catch (error) {
      return new Response(JSON.stringify({ error: "no_fixture", message: String(error) }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }
    void init;
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  return () => {
    globalThis.fetch = original;
  };
}
