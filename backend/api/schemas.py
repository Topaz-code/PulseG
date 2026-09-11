"""Request bodies. Responses are built from the models directly, so only inputs live here.

Every field a user can type has a length limit and a description. That is not decoration: the
OpenAPI document doubles as the API reference in docs/API.md, and a limit that is not written
down is a limit someone will hit at 2am with a 40,000-word design document.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- projects ----------------------------------------------------------------------------


class CreateProjectRequest(Strict):
    name: str = Field(min_length=1, max_length=80, description="Shown in the project menu.")
    mode: Literal["fresh", "existing"] = "fresh"
    existing_path: str = Field(default="", max_length=500, description="Folder to adopt, in existing mode.")
    genre: str = Field(default="auto", max_length=40)
    art_style: str = Field(default="auto", max_length=120)
    perspective: str = Field(default="auto", max_length=40)
    godot_version: str = Field(default="auto", max_length=40)
    parent_dir: str = Field(default="", max_length=500)
    concept: str = Field(default="", max_length=8000, description="The idea the user pasted, if any.")
    git_identity: dict[str, str] = Field(default_factory=dict)


class UpdateProjectRequest(Strict):
    name: str | None = Field(default=None, max_length=80)
    genre: str | None = Field(default=None, max_length=40)
    art_style: str | None = Field(default=None, max_length=120)
    perspective: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=4000)


# --- tasks ---------------------------------------------------------------------------------


class CreateTaskRequest(Strict):
    instruction: str = Field(min_length=8, max_length=8000)
    title: str = Field(default="", max_length=120)
    assigned_to: str = Field(default="programmer", max_length=40)
    kind: str = Field(default="implementation", max_length=40)
    phase: int = Field(default=1, ge=0, le=5)
    dependencies: list[str] = Field(default_factory=list, max_length=40)
    context_files: list[str] = Field(default_factory=list, max_length=40)
    expected_outputs: list[str] = Field(default_factory=list, max_length=40)
    file_claims: list[str] = Field(default_factory=list, max_length=40)
    priority: int = Field(default=100, ge=0, le=1000)
    created_by: str = Field(default="human_direct", max_length=40)


class ApproveRequest(Strict):
    note: str = Field(default="", max_length=2000)
    override: bool = False


class RejectRequest(Strict):
    note: str = Field(min_length=1, max_length=2000)


class OverrideRequest(Strict):
    note: str = Field(min_length=1, max_length=2000, description="Why the decline is being overridden.")


class BulkApproveRequest(Strict):
    task_ids: list[str] = Field(min_length=1, max_length=50)
    note: str = Field(default="", max_length=2000)


class CommentRequest(Strict):
    note: str = Field(min_length=1, max_length=4000)


class TransitionRequest(Strict):
    status: str = Field(max_length=40)
    reason: str = Field(default="", max_length=500)


# --- agents -----------------------------------------------------------------------------------


class ModelRefRequest(Strict):
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(default="", max_length=200)


class UpdateChainRequest(Strict):
    primary: ModelRefRequest
    fallbacks: list[ModelRefRequest] = Field(default_factory=list, max_length=3)


class UpdateAgentRequest(Strict):
    name: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=600)
    system_prompt: str | None = Field(default=None, max_length=20000)
    enabled: bool | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=256, le=32000)
    timeout_s: int | None = Field(default=None, ge=15, le=900)
    max_decline_rotations: int | None = Field(default=None, ge=1, le=10)
    colours: dict[str, str] | None = None


class AddAgentRequest(Strict):
    """Config-only agent creation: no code change is needed to add a thirteenth agent."""

    id: str = Field(min_length=2, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=60)
    role: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=600)
    primary: ModelRefRequest
    fallbacks: list[ModelRefRequest] = Field(default_factory=list, max_length=3)
    system_prompt: str = Field(default="", max_length=20000)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=256, le=32000)
    capabilities: list[str] = Field(default_factory=lambda: ["chat"], max_length=8)


# --- providers and settings ---------------------------------------------------------------------


class SaveKeyRequest(Strict):
    provider_id: str = Field(min_length=1, max_length=40)
    api_key: str = Field(min_length=4, max_length=4000)


class TestProviderRequest(Strict):
    provider_id: str = Field(min_length=1, max_length=40)
    model: str = Field(default="", max_length=200)


class ProviderOverrideRequest(Strict):
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=300)
    default_model: str | None = Field(default=None, max_length=200)
    daily_request_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    monthly_request_limit: int | None = Field(default=None, ge=0, le=10_000_000)


class GodotSettingsRequest(Strict):
    executable: str | None = Field(default=None, max_length=500)
    version: str | None = Field(default=None, max_length=60)
    verify_on_launch: bool | None = None
    prefer_headless: bool | None = None
    extra_args: list[str] | None = Field(default=None, max_length=12)


class GitSettingsRequest(Strict):
    user_name: str | None = Field(default=None, max_length=120)
    user_email: str | None = Field(default=None, max_length=200)
    auto_init: bool | None = None
    branch_per_phase: bool | None = None


class NotificationSettingsRequest(Strict):
    windows_toasts: bool | None = None
    telegram_enabled: bool | None = None
    telegram_chat_id: str | None = Field(default=None, max_length=40)
    notify_on: list[str] | None = Field(default=None, max_length=10)
    quiet_hours: str | None = Field(default=None, max_length=20)


class RuntimeSettingsRequest(Strict):
    max_parallel_agents: int | None = Field(default=None, ge=1, le=8)
    dispatch_interval_s: float | None = Field(default=None, ge=0.25, le=60.0)
    request_timeout_s: int | None = Field(default=None, ge=15, le=900)
    demo_mode: bool | None = None


class UISettingsRequest(Strict):
    theme: str | None = Field(default=None, max_length=40)
    show_agent_graph: bool | None = None
    sidebar_collapsed: bool | None = None
    activity_feed_limit: int | None = Field(default=None, ge=50, le=2000)


class SettingsRequest(Strict):
    """Any subset: the Settings screen saves one panel at a time, not the whole file."""

    projects_root: str | None = Field(default=None, max_length=500)
    godot: GodotSettingsRequest | None = None
    git: GitSettingsRequest | None = None
    notifications: NotificationSettingsRequest | None = None
    runtime: RuntimeSettingsRequest | None = None
    ui: UISettingsRequest | None = None


class SetupWizardRequest(Strict):
    projects_root: str = Field(max_length=500)
    godot_executable: str = Field(default="", max_length=500)
    git_name: str = Field(default="", max_length=120)
    git_email: str = Field(default="", max_length=200)
    configure_git_globally: bool = False
    keys: dict[str, str] = Field(default_factory=dict, description="Optional provider keys, provider id -> key.")
    notifications: NotificationSettingsRequest | None = None


# --- planning (/grillme) -------------------------------------------------------------------------


class IntakeRequest(Strict):
    concept: str = Field(min_length=10, max_length=8000)
    title: str = Field(default="", max_length=80)


class IntakeAnswerRequest(Strict):
    answers: str = Field(min_length=1, max_length=8000)


class IntakeConfirmRequest(Strict):
    confirmed_text: str = Field(default="", max_length=2000)


# --- assets, knowledge, git, preview ------------------------------------------------------------------


class IngestAssetsRequest(Strict):
    source_dir: str = Field(min_length=1, max_length=500)
    kind: str = Field(default="", max_length=20)


class AssetRequestCreate(Strict):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(default="sprite", max_length=20)
    description: str = Field(default="", max_length=600)
    needed_by_task: str = Field(default="", max_length=20)


class FulfilAssetRequest(Strict):
    path: str = Field(min_length=1, max_length=300, description="Project-relative path of the supplied file.")
    by: str = Field(default="human", max_length=40)


class RegenerateRequest(Strict):
    """Ask for another attempt at an asset: this creates a real task, it is not a rerun button."""

    asset_path: str = Field(min_length=1, max_length=300)
    reason: str = Field(default="", max_length=1000)
    agent: Literal["image_generator", "audio_curator"] = "image_generator"


class KnowledgeSearchRequest(Strict):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=20, ge=1, le=100)


class GitDiffRequest(Strict):
    first: str = Field(min_length=4, max_length=60)
    second: str = Field(min_length=4, max_length=60)
    path: str = Field(default="", max_length=300)


class PreviewToggleRequest(Strict):
    mode: Literal["graph", "game"] = "graph"


class WebExportRequest(Strict):
    force: bool = Field(default=False, description="Export even if no phase milestone was approved.")
