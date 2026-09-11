# PulseG Studio API

Generated from the running application by `scripts/build_api_docs.py` - do not edit by
hand. The same document is served at `/openapi.json`, and `/docs` renders it.

- Application: PulseG Studio 0.9.0
- Routes: 148

## Error shape

Every failure - validation, guard, domain error, crash - returns the same JSON envelope:

```json
{"error": "no_active_project",
 "message": "No project is open. Create one or pick one from the project menu.",
 "action": "open_project"}
```

`error` is a stable machine code, `message` is written for a person, and `action` (when
present) names the screen the UI should open.

## System, setup and diagnostics

### `GET /api/system/diagnostics`

Diagnostics

### `GET /api/system/first-run`

First Run

### `GET /api/system/info`

System Info

### `GET /api/system/notifications`

Notifications Status

### `GET /api/system/notifications/log`

Notifications Log
- params: limit (query, integer)

### `POST /api/system/notifications/read`

Notifications Read
- params: event_id (query, string)

### `POST /api/system/notifications/test`

Notifications Test

### `POST /api/system/rebuild-index`

Rebuild Index

### `POST /api/system/setup`

Finish Setup

- body: `SetupWizardRequest {projects_root: string, godot_executable: string?, git_name: string?, git_email: string?, configure_git_globally: boolean?, keys: object?, notifications: NotificationSettingsRequest|null?}`

### `POST /api/system/shutdown`

Shutdown

### `GET /api/system/wizard-state`

Wizard State

### `POST /api/system/wizard-state`

Save Wizard State

- body: `object`

## Settings

### `GET /api/settings`

Get Settings

### `PUT /api/settings`

Update Settings

- body: `SettingsRequest {projects_root: string|null?, godot: GodotSettingsRequest|null?, git: GitSettingsRequest|null?, notifications: NotificationSettingsRequest|null?, runtime: RuntimeSettingsRequest|null?, ui: UISettingsRequest|null?}`

### `GET /api/settings/agents-file`

Agents File

### `PUT /api/settings/agents-file`

Write Agents File

- body: `object`

### `POST /api/settings/agents/reload`

Reload Agents

### `POST /api/settings/export`

Export Config

### `GET /api/settings/git`

Git Settings

### `PUT /api/settings/git`

Save Git

- body: `GitSettingsRequest {user_name: string|null?, user_email: string|null?, auto_init: boolean|null?, branch_per_phase: boolean|null?}`

### `POST /api/settings/git/global-identity`

Set Identity
- params: name (query, string), email (query, string)

### `GET /api/settings/godot`

Godot

### `PUT /api/settings/godot`

Save Godot

- body: `GodotSettingsRequest {executable: string|null?, version: string|null?, verify_on_launch: boolean|null?, prefer_headless: boolean|null?, extra_args: array|null?}`

### `GET /api/settings/godot/detect`

Godot Detect

### `GET /api/settings/notifications`

Notification Settings

### `GET /api/settings/providers-file`

Providers File

### `POST /api/settings/reset`

Reset
- params: section (query, string)

### `GET /api/settings/storage`

Storage

### `POST /api/settings/telegram/chat-id`

Telegram Chat Id

### `POST /api/settings/telegram/test`

Telegram Test

## Projects and their files

### `GET /api/projects`

List Projects

### `POST /api/projects`

Create Project

- body: `CreateProjectRequest {name: string, mode: string?, existing_path: string?, genre: string?, art_style: string?, perspective: string?, godot_version: string?, parent_dir: string?, concept: string?, git_identity: object?}`

### `GET /api/projects/active`

Active Project

### `DELETE /api/projects/{project_id}`

Remove Project
- params: project_id (path, string), delete_files (query, boolean)

### `GET /api/projects/{project_id}`

Get Project
- params: project_id (path, string)

### `PATCH /api/projects/{project_id}`

Update Project

- body: `UpdateProjectRequest {name: string|null?, genre: string|null?, art_style: string|null?, perspective: string|null?, notes: string|null?}`
- params: project_id (path, string)

### `POST /api/projects/{project_id}/activate`

Activate
- params: project_id (path, string)

### `GET /api/projects/{project_id}/activity`

Activity
- params: project_id (path, string), limit (query, integer)

### `GET /api/projects/{project_id}/file`

Read File
- params: project_id (path, string), path (query, string)

### `GET /api/projects/{project_id}/files`

Files
- params: project_id (path, string), pattern (query, string), limit (query, integer)

### `GET /api/projects/{project_id}/git`

Git Status
- params: project_id (path, string)

### `POST /api/projects/{project_id}/git/init`

Git Init
- params: project_id (path, string)

### `GET /api/projects/{project_id}/godot-status`

Godot Status
- params: project_id (path, string)

### `GET /api/projects/{project_id}/milestones`

Milestones
- params: project_id (path, string)

### `GET /api/projects/{project_id}/open-folder`

Open Folder
- params: project_id (path, string), sub (query, string)

### `GET /api/projects/{project_id}/overview`

Overview
- params: project_id (path, string)

### `GET /api/projects/{project_id}/paths`

Project Paths
- params: project_id (path, string)

### `GET /api/projects/{project_id}/phases`

Phases
- params: project_id (path, string)

### `GET /api/projects/{project_id}/search`

Search
- params: project_id (path, string), q (query, string), limit (query, integer)

## Task board and the human gate

### `GET /api/tasks`

List Tasks
- params: status (query, string), agent (query, string), phase (query, integer), limit (query, integer)

### `POST /api/tasks`

Create Task

- body: `CreateTaskRequest {instruction: string, title: string?, assigned_to: string?, kind: string?, phase: integer?, dependencies: string[]?, context_files: string[]?, expected_outputs: string[]?, file_claims: string[]?, priority: integer?, created_by: string?}`

### `GET /api/tasks/board`

Board

### `POST /api/tasks/bulk-approve`

Bulk Approve

- body: `BulkApproveRequest {task_ids: string[], note: string?}`

### `GET /api/tasks/review`

Review Queue

### `GET /api/tasks/statuses`

Statuses

### `GET /api/tasks/{task_id}`

Get Task
- params: task_id (path, string)

### `POST /api/tasks/{task_id}/approve`

Approve

- body: `ApproveRequest {note: string?, override: boolean?}`
- params: task_id (path, string)

### `GET /api/tasks/{task_id}/attempts`

Attempts
- params: task_id (path, string)

### `GET /api/tasks/{task_id}/audit-report`

Audit Report
- params: task_id (path, string)

### `POST /api/tasks/{task_id}/comment`

Comment

- body: `CommentRequest {note: string}`
- params: task_id (path, string)

### `GET /api/tasks/{task_id}/context`

Context Preview
- params: task_id (path, string)

### `GET /api/tasks/{task_id}/dependencies`

Dependencies
- params: task_id (path, string)

### `GET /api/tasks/{task_id}/output`

Output
- params: task_id (path, string), limit (query, integer)

### `POST /api/tasks/{task_id}/override`

Override

- body: `OverrideRequest {note: string}`
- params: task_id (path, string)

### `POST /api/tasks/{task_id}/reject`

Reject

- body: `RejectRequest {note: string}`
- params: task_id (path, string)

### `POST /api/tasks/{task_id}/retry`

Retry
- params: task_id (path, string)

### `GET /api/tasks/{task_id}/screenshots`

Screenshots
- params: task_id (path, string)

## Agents and model chains

### `GET /api/agents`

List Agents

### `POST /api/agents`

Add Agent

- body: `AddAgentRequest {id: string, name: string, role: string?, description: string?, primary: ModelRefRequest, fallbacks: ModelRefRequest[]?, system_prompt: string?, temperature: number?, max_tokens: integer?, capabilities: string[]?}`

### `POST /api/agents/reset-all`

Reset All

### `GET /api/agents/states`

States

### `GET /api/agents/summary`

Summary

### `DELETE /api/agents/{agent_id}`

Remove Agent
- params: agent_id (path, string)

### `GET /api/agents/{agent_id}`

Get Agent
- params: agent_id (path, string)

### `PATCH /api/agents/{agent_id}`

Update Agent

- body: `UpdateAgentRequest {name: string|null?, description: string|null?, system_prompt: string|null?, enabled: boolean|null?, temperature: number|null?, max_tokens: integer|null?, timeout_s: integer|null?, max_decline_rotations: integer|null?, colours: object|null?}`
- params: agent_id (path, string)

### `PUT /api/agents/{agent_id}/chain`

Update Chain

- body: `UpdateChainRequest {primary: ModelRefRequest, fallbacks: ModelRefRequest[]?}`
- params: agent_id (path, string)

### `GET /api/agents/{agent_id}/context-preview`

Context Preview
- params: agent_id (path, string), task_id (query, string)

### `GET /api/agents/{agent_id}/history`

History
- params: agent_id (path, string), limit (query, integer)

### `GET /api/agents/{agent_id}/prompt-preview`

Prompt Preview
- params: agent_id (path, string), task_id (query, string)

### `POST /api/agents/{agent_id}/reset`

Reset Agent
- params: agent_id (path, string)

### `GET /api/agents/{agent_id}/usage`

Usage
- params: agent_id (path, string)

## Providers, keys and quota

### `GET /api/providers`

List Providers

### `PUT /api/providers/key`

Save Key

- body: `SaveKeyRequest {provider_id: string, api_key: string}`

### `DELETE /api/providers/key/{provider_id}`

Delete Key
- params: provider_id (path, string)

### `GET /api/providers/quota`

Quota Snapshot

### `POST /api/providers/test`

Test Provider

- body: `TestProviderRequest {provider_id: string, model: string?}`

### `POST /api/providers/test-all`

Test All

### `GET /api/providers/verification`

Verification

### `GET /api/providers/{provider_id}`

Get Provider
- params: provider_id (path, string)

### `GET /api/providers/{provider_id}/models`

Models
- params: provider_id (path, string)

### `PUT /api/providers/{provider_id}/override`

Set Override

- body: `ProviderOverrideRequest {enabled: boolean|null?, base_url: string|null?, default_model: string|null?, daily_request_limit: integer|null?, monthly_request_limit: integer|null?}`
- params: provider_id (path, string)

## Planning, intake and the GDD

### `POST /api/planning/answer`

Answer

- body: `IntakeAnswerRequest {answers: string}`

### `GET /api/planning/chat`

Chat
- params: limit (query, integer)

### `POST /api/planning/confirm`

Confirm

- body: `IntakeConfirmRequest {confirmed_text: string?}`

### `GET /api/planning/design-doc`

Design Doc

### `GET /api/planning/gate`

Gate

### `GET /api/planning/gdd`

Gdd

### `POST /api/planning/handoff`

Handoff

### `POST /api/planning/intake`

Intake

- body: `IntakeRequest {concept: string, title: string?}`

### `GET /api/planning/questions`

Questions

### `POST /api/planning/seed-tasks`

Seed Tasks

### `GET /api/planning/state`

State

### `GET /api/planning/templates`

Templates

## Asset library and asset requests

### `GET /api/assets`

List Assets
- params: kind (query, string), query (query, string)

### `POST /api/assets/ingest`

Ingest

- body: `IngestAssetsRequest {source_dir: string, kind: string?}`

### `POST /api/assets/regenerate`

Regenerate

- body: `RegenerateRequest {asset_path: string, reason: string?, agent: string?}`

### `POST /api/assets/request-recuration`

Recurate

- body: `RegenerateRequest {asset_path: string, reason: string?, agent: string?}`

### `GET /api/assets/requests`

Requests

### `POST /api/assets/requests`

Create Request

- body: `AssetRequestCreate {name: string, kind: string?, description: string?, needed_by_task: string?}`

### `POST /api/assets/requests/{request_id}/fulfil`

Fulfil

- body: `FulfilAssetRequest {path: string, by: string?}`
- params: request_id (path, string)

### `GET /api/assets/style-lock`

Style Lock

### `PUT /api/assets/style-lock`

Update Style Lock

- body: `object`

## Knowledge base and transcripts

### `GET /api/knowledge`

List Knowledge
- params: source_type (query, string), trust (query, string), limit (query, integer)

### `DELETE /api/knowledge/entry`

Forget
- params: path (query, string)

### `GET /api/knowledge/entry`

Entry
- params: path (query, string)

### `POST /api/knowledge/search`

Search

- body: `KnowledgeSearchRequest {query: string, limit: integer?}`

### `GET /api/knowledge/stats`

Stats

### `GET /api/knowledge/transcripts`

Transcripts

## Logs, run control and context stats

### `GET /api/logs`

Recent
- params: limit (query, integer), type (query, string), agent (query, string)

### `GET /api/logs/context-stats`

Context Stats

### `GET /api/logs/failures`

Failures
- params: limit (query, integer)

### `GET /api/logs/file`

Log File
- params: lines (query, integer)

### `GET /api/logs/run-state`

Run State

### `POST /api/logs/run-state/pause`

Pause

### `POST /api/logs/run-state/start`

Start

### `POST /api/logs/run-state/stop`

Stop

### `GET /api/logs/stream-info`

Stream Info

### `POST /api/logs/tick`

Tick

## Git history

### `GET /api/git/branches`

Branches

### `POST /api/git/diff`

Git Diff

- body: `GitDiffRequest {first: string, second: string, path: string?}`

### `GET /api/git/file-history`

File History
- params: path (query, string), limit (query, integer)

### `GET /api/git/log`

Git Log
- params: limit (query, integer)

### `POST /api/git/revert`

Revert
- params: sha (query, string)

### `GET /api/git/show`

Git Show
- params: sha (query, string), path (query, string)

### `GET /api/git/status`

Git Status

### `GET /api/git/task-commits`

Task Commits

## Design tokens and the taste audit

### `GET /api/design/audit`

Audit
- params: write (query, boolean)

### `GET /api/design/checklist`

Checklist

### `GET /api/design/report`

Report

### `GET /api/design/theme`

Theme

### `PUT /api/design/theme`

Update Theme

- body: `object`

## Live preview and the Godot web export

### `POST /api/preview/export`

Export Web
- params: force (query, boolean)

### `GET /api/preview/godot-window`

Godot Window

### `GET /api/preview/graph`

Graph

### `POST /api/preview/mode`

Set Mode

- body: `PreviewToggleRequest {mode: string?}`

### `GET /api/preview/state`

Preview State

## Live event stream

### `GET /ws/health`

Ws Health

## /api/health

### `GET /api/health`

Health
