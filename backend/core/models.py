"""Domain models shared by the orchestration layer, the API and the SQLite index.

These are the canonical shapes. Because files are the source of truth, every model has
``to_json()``/``from_json()`` semantics through Pydantic's ``model_dump``/``model_validate``
and is stored as plain JSON on disk.

Task status flow (spec C.1)::

    PENDING -> IN_PROGRESS -> SUBMITTED -> AUDITING
       -> NEEDS_HUMAN_REVIEW -> APPROVED | REJECTED (requeued with combined notes)
    FAILED (all fallbacks exhausted) -> NEEDS_INTERVENTION (paused, never dead)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    return (dt or utcnow()).isoformat(timespec="seconds")


class Status(str, Enum):
    """Canonical task states. Values are the strings written to task_queue.json."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    AUDITING = "AUDITING"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    NEEDS_INTERVENTION = "NEEDS_INTERVENTION"

    @property
    def is_open(self) -> bool:
        """True while the task still occupies the pipeline (not finished)."""
        return self not in (Status.APPROVED,)

    @property
    def is_terminal(self) -> bool:
        return self is Status.APPROVED

    @property
    def is_paused(self) -> bool:
        return self is Status.NEEDS_INTERVENTION


#: Linear kanban columns, in display order. "Declined/Retrying" groups REJECTED+FAILED.
KANBAN_LANES: list[dict[str, Any]] = [
    {"id": "pending", "title": "Pending", "statuses": [Status.PENDING]},
    {"id": "in_progress", "title": "In Progress", "statuses": [Status.IN_PROGRESS]},
    {"id": "auditing", "title": "Auditing", "statuses": [Status.SUBMITTED, Status.AUDITING]},
    {"id": "review", "title": "Needs Your Review", "statuses": [Status.NEEDS_HUMAN_REVIEW]},
    {"id": "approved", "title": "Approved", "statuses": [Status.APPROVED]},
    {"id": "declined", "title": "Declined / Retrying", "statuses": [Status.REJECTED, Status.FAILED]},
    {"id": "intervention", "title": "Needs Intervention", "statuses": [Status.NEEDS_INTERVENTION]},
]

#: Allowed transitions, enforced by TaskBus.transition(). Anything else raises.
ALLOWED_TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDING: {Status.IN_PROGRESS, Status.NEEDS_INTERVENTION, Status.PENDING},
    Status.IN_PROGRESS: {
        Status.SUBMITTED,
        Status.FAILED,
        Status.NEEDS_INTERVENTION,
        Status.IN_PROGRESS,
        Status.PENDING,  # returned to queue on graceful pause / cancellation
    },
    Status.SUBMITTED: {Status.AUDITING, Status.FAILED, Status.NEEDS_INTERVENTION},
    Status.AUDITING: {Status.NEEDS_HUMAN_REVIEW, Status.FAILED, Status.NEEDS_INTERVENTION},
    Status.NEEDS_HUMAN_REVIEW: {
        Status.APPROVED,
        Status.REJECTED,
        Status.NEEDS_INTERVENTION,
        Status.IN_PROGRESS,  # human asked for a re-run before deciding
    },
    Status.APPROVED: {Status.APPROVED},
    Status.REJECTED: {Status.PENDING, Status.IN_PROGRESS, Status.NEEDS_INTERVENTION},
    Status.FAILED: {Status.PENDING, Status.NEEDS_INTERVENTION, Status.NEEDS_HUMAN_REVIEW},
    Status.NEEDS_INTERVENTION: {
        Status.PENDING,  # resumed by human retry / key added / chain edited
        Status.IN_PROGRESS,
    },
}


class ProviderKind(str, Enum):
    LLM = "llm"
    VISION = "vision"
    STT = "stt"
    IMAGE = "image"
    SEARCH = "search"
    AUDIO_SEARCH = "audio_search"
    NOTIFY = "notify"
    QA = "qa"


class FailureKind(str, Enum):
    """Why a provider call failed. Drives retry vs fallback vs pause decisions."""

    RATE_LIMITED = "rate_limited"          # retry same model after backoff
    INVALID_KEY = "invalid_key"            # fallback immediately, tell the human
    EMPTY_RESPONSE = "empty_response"      # fallback (model produced nothing usable)
    TIMEOUT = "timeout"                    # retry once, then fallback
    UNAVAILABLE = "unavailable"            # provider 5xx / down: fallback
    CONTENT_FILTERED = "content_filtered"  # fallback with a rewritten prompt
    BAD_REQUEST = "bad_request"            # our prompt/schema is wrong: log, don't burn keys
    QUOTA_EXHAUSTED = "quota_exhausted"    # free tier used up: fallback, surface in UI
    NOT_CONFIGURED = "not_configured"      # no key: skip without counting as a failure
    UNKNOWN = "unknown"

    @property
    def retryable_on_same_model(self) -> bool:
        return self in (FailureKind.RATE_LIMITED, FailureKind.TIMEOUT, FailureKind.UNKNOWN)

    @property
    def human_actionable(self) -> bool:
        return self in (
            FailureKind.INVALID_KEY,
            FailureKind.QUOTA_EXHAUSTED,
            FailureKind.NOT_CONFIGURED,
        )


class ModelRef(BaseModel):
    """A concrete (provider, model) pair inside a fallback chain."""

    model_config = ConfigDict(extra="allow")

    provider: str
    model: str
    note: str = ""


class HumanDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    OVERRIDDEN = "OVERRIDDEN"  # human flipped the Auditor's verdict


class AttemptRecord(BaseModel):
    """One provider call attempt. Kept forever (zero-abandonment audit trail)."""

    model_config = ConfigDict(extra="allow")

    at: str = Field(default_factory=lambda: iso(utcnow()))
    provider: str
    model: str
    slot: Literal["primary", "fallback1", "fallback2", "extra"] = "primary"
    ok: bool = False
    failure_kind: FailureKind | None = None
    message: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    http_status: int | None = None


class Verdict(BaseModel):
    """Auditor output (spec C.4). Persisted and shown verbatim in the task drawer."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    verdict: Literal["APPROVED", "DECLINED"]
    score: int = Field(ge=0, le=10, description="0-10 quality score")
    skill_flags: dict[str, list[str]] = Field(default_factory=dict)
    rubric: dict[str, float] = Field(default_factory=dict)
    screenshots_reviewed: list[str] = Field(default_factory=list)
    fix_note: str = ""
    summary: str = ""
    model_used: str = ""
    provider_used: str = ""
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    round: int = 1  # which audit attempt for this task

    @property
    def is_declined(self) -> bool:
        return self.verdict == "DECLINED"

    @property
    def total_flags(self) -> int:
        return sum(len(v) for v in self.skill_flags.values())


class Task(BaseModel):
    """A unit of work on the task bus. Spec C.1 fields, plus operational extras."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    project_id: str = ""
    phase: int = 1
    created_by: str = "prompter"
    assigned_to: str = "programmer"
    status: Status = Status.PENDING
    retry_count: int = 0
    provider_used: str | None = None
    model_used: str | None = None
    instruction: str = ""
    title: str = ""
    kind: str = "implementation"  # implementation|research|art|audio|story|test|audit|export|asset_request
    dependencies: list[str] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    auditor_verdict: Verdict | None = None
    human_decision: HumanDecision | None = None
    human_note: str = ""
    output: str = ""
    artifacts: list[str] = Field(default_factory=list)
    file_claims: list[str] = Field(default_factory=list)  # files this task may write
    attempts: list[AttemptRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    comments: list[dict[str, Any]] = Field(default_factory=list)  # human notes that are not decisions
    decline_count: int = 0  # consecutive Auditor declines with the same model (drives rotation)
    human_rejections: int = 0  # times the human sent this back; counted separately on purpose
    audit_rounds: int = 0
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    updated_at: str = Field(default_factory=lambda: iso(utcnow()))
    started_at: str | None = None
    submitted_at: str | None = None
    audited_at: str | None = None
    decided_at: str | None = None
    committed: str | None = None  # git sha after human approval
    priority: int = 100
    crash_recovered: bool = False

    @field_validator("task_id")
    @classmethod
    def _validate_task_id(cls, value: str) -> str:
        if not re.fullmatch(r"TASK_\d{3,6}", value):
            raise ValueError("task_id must look like TASK_047")
        return value

    # --- convenience ------------------------------------------------------------

    @property
    def chain_slot(self) -> str:
        """Which fallback slot the next attempt will use."""
        return ["primary", "fallback1", "fallback2"][min(self.retry_count, 2)]

    @property
    def is_paused(self) -> bool:
        return self.status is Status.NEEDS_INTERVENTION

    @property
    def needs_human(self) -> bool:
        return self.status is Status.NEEDS_HUMAN_REVIEW

    def touch(self) -> None:
        self.updated_at = iso(utcnow())

    def record_attempt(self, attempt: AttemptRecord) -> None:
        self.attempts.append(attempt)
        if not attempt.ok:
            self.errors.append(
                f"[{attempt.at}] {attempt.provider}/{attempt.model} "
                f"({attempt.failure_kind.value if attempt.failure_kind else 'error'}): "
                f"{attempt.message[:300]}"
            )
            # Keep the on-disk tail bounded; the full history lives in attempt records.
            self.errors = self.errors[-50:]
        self.touch()

    def summarise_for_human(self) -> str:
        """One-line status used in notifications and the activity feed."""
        verdict = self.auditor_verdict
        bits = [f"{self.task_id} ({self.assigned_to})", self.status.value.replace("_", " ").title()]
        if verdict:
            bits.append(f"auditor {verdict.verdict.lower()} score {verdict.score}/10")
        if self.retry_count:
            bits.append(f"retry {self.retry_count}")
        return " - ".join(bits)


class AgentDefinition(BaseModel):
    """Config-driven agent roster entry. Everything user-editable from Settings."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    role: str
    description: str = ""
    icon: str = "robot"
    color: str = "#5D737E"
    enabled: bool = True
    primary: ModelRef
    fallbacks: list[ModelRef] = Field(default_factory=list)
    system_prompt: str = ""
    system_prompt_file: str = ""
    skills: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)  # chat, vision, stt, image, search
    temperature: float = 0.4
    max_tokens: int = 4096
    timeout_s: int = 120
    max_decline_rotations: int = 3  # spec B.2 rule 5

    def chain(self) -> list[tuple[str, ModelRef]]:
        """Ordered (slot, ref) pairs, primary first."""
        slots: list[tuple[str, ModelRef]] = [("primary", self.primary)]
        for index, ref in enumerate(self.fallbacks[:2], start=1):
            slots.append((f"fallback{index}", ref))
        return slots


class AgentState(BaseModel):
    """Runtime state written to memory/agent_states.json. Survives restarts."""

    model_config = ConfigDict(extra="allow")

    agent_id: str
    status: Literal["idle", "working", "blocked", "error", "disabled"] = "idle"
    current_task: str | None = None
    last_task: str | None = None
    last_active: str = Field(default_factory=lambda: iso(utcnow()))
    tasks_completed: int = 0
    tasks_failed: int = 0
    declines: int = 0
    total_latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    note: str = ""
    active_provider: str | None = None
    active_model: str | None = None

    @property
    def avg_latency_ms(self) -> int:
        if not self.tasks_completed:
            return 0
        return int(self.total_latency_ms / self.tasks_completed)


class QuotaWindow(BaseModel):
    """Rolling usage counters for one (provider, model) pair."""

    model_config = ConfigDict(extra="allow")

    provider: str
    model: str = "*"
    requests_today: int = 0
    requests_minute: int = 0
    tokens_in_today: int = 0
    tokens_out_today: int = 0
    day: str = Field(default_factory=lambda: utcnow().date().isoformat())
    minute: str = Field(default_factory=lambda: utcnow().strftime("%Y-%m-%dT%H:%M"))
    last_error: str = ""
    cooldown_until: str | None = None

    def roll(self) -> None:
        today = utcnow().date().isoformat()
        minute = utcnow().strftime("%Y-%m-%dT%H:%M")
        if self.day != today:
            self.day = today
            self.requests_today = 0
            self.tokens_in_today = 0
            self.tokens_out_today = 0
        if self.minute != minute:
            self.minute = minute
            self.requests_minute = 0


class ProjectRecord(BaseModel):
    """Entry in ~/.pulsegstudio/projects.json (the project switcher's data source)."""

    model_config = ConfigDict(extra="allow")

    project_id: str
    name: str
    path: str
    mode: Literal["fresh", "existing"] = "fresh"
    genre: str = "auto"
    art_style: str = "auto"
    perspective: str = "auto"
    godot_version: str = "auto"
    phase: int = 0
    phases_total: int = 6
    status: Literal["planning", "building", "paused", "shipped", "error"] = "planning"
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    last_opened: str = Field(default_factory=lambda: iso(utcnow()))
    git_initialised: bool = False
    godot_project_path: str = ""
    summary: str = ""


class AssetRequest(BaseModel):
    """Visible, actionable request card (spec E.2) - never a silent stall."""

    model_config = ConfigDict(extra="allow")

    request_id: str
    project_id: str = ""
    kind: Literal["sprite", "tile", "ui", "background", "bgm", "sfx", "reference", "other"] = "sprite"
    name: str
    description: str
    needed_by_task: str = ""
    requested_by: str = "image_generator"
    status: Literal["open", "fulfilled", "cancelled"] = "open"
    fulfilled_by: str = ""
    fulfilled_path: str = ""
    created_at: str = Field(default_factory=lambda: iso(utcnow()))


class KnowledgeEntry(BaseModel):
    """Researcher/Transcriptor finding indexed for the Knowledge Base view."""

    model_config = ConfigDict(extra="allow")

    entry_id: str
    title: str
    source_url: str = ""
    source_type: Literal["web", "youtube", "docs", "local", "book", "other"] = "web"
    agent: str = "researcher"
    summary: str = ""
    markdown_path: str = ""
    tags: list[str] = Field(default_factory=list)
    trust: Literal["high", "medium", "low"] = "medium"
    created_at: str = Field(default_factory=lambda: iso(utcnow()))


class ChatMessage(BaseModel):
    """Planning chat / command bar transcript."""

    model_config = ConfigDict(extra="allow")

    message_id: str
    role: Literal["human", "planning_agent", "agent", "system"] = "human"
    agent_id: str | None = None
    content: str = ""
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    meta: dict[str, Any] = Field(default_factory=dict)


class ProviderSpec(BaseModel):
    """Static, verified description of a provider. Overridable via providers.yaml."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    kind: list[ProviderKind]
    base_url: str = ""
    auth_style: Literal["bearer", "query", "header", "none"] = "bearer"
    auth_header: str = "Authorization"
    key_query_param: str = "key"
    requires_key: bool = True
    key_format: str = ""
    signup_url: str = ""
    docs_url: str = ""
    free_tier: str = ""
    limits: dict[str, Any] = Field(default_factory=dict)
    test_path: str = ""
    test_model: str = ""
    models_endpoint: str = ""
    default_models: list[str] = Field(default_factory=list)
    verified: Literal["verified", "conditional", "unverified", "substituted"] = "unverified"
    verified_on: str = ""
    notes: str = ""
    supports_vision: bool = False
    supports_streaming: bool = True
    quota_visible: bool = False


class ProviderStatus(BaseModel):
    """Live status of one configured provider, for the BYOK dashboard cards."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    configured: bool = False
    masked_key: str = "not set"
    last_test_status: Literal["untested", "valid", "invalid", "rate_limited", "unreachable"] = "untested"
    last_test_at: str = ""
    last_test_detail: str = ""
    quota: dict[str, Any] = Field(default_factory=dict)
    verified: str = "unverified"
    free_tier: str = ""
    signup_url: str = ""
    docs_url: str = ""
    notes: str = ""


class NotificationEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str
    kind: Literal[
        "needs_review",
        "needs_intervention",
        "pipeline_stalled",
        "phase_complete",
        "test_connection",
        "system",
    ]
    title: str
    body: str = ""
    project_id: str = ""
    task_id: str = ""
    created_at: str = Field(default_factory=lambda: iso(utcnow()))
    #: Which channels accepted the message. ``log`` is always True - the in-app list is the
    #: channel that can never fail, and it is what the bell icon reads.
    delivered: dict[str, bool] = Field(default_factory=dict)
    #: Human-readable delivery notes ("Telegram: sent", "quiet hours", ...).
    delivery_detail: list[str] = Field(default_factory=list)
    read: bool = False


class SkillFinding(BaseModel):
    """One issue found by one skill, with severity and a concrete fix hint."""

    model_config = ConfigDict(extra="allow")

    skill: str
    severity: Literal["info", "low", "medium", "high"] = "medium"
    message: str
    location: str = ""
    excerpt: str = ""
    recommendation: str = ""


class DesignReview(BaseModel):
    """Result of running the frontend design-taste skill over a screen definition."""

    model_config = ConfigDict(extra="allow")

    screen: str
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    reviewed_at: str = Field(default_factory=lambda: iso(utcnow()))
