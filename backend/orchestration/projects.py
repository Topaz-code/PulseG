"""Project lifecycle: create fresh, connect existing, index, switch (spec E.2, E.5).

A project is a self-contained folder that is also a git repo. Everything the agents need
lives inside it, so a project can be zipped, moved or handed to another machine - only the
global BYOK vault and the roster stay in ``~/.pulsegstudio``.
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.atomic import atomic_write_json, atomic_write_text, read_json, read_text
from ..core.config import load_config, load_projects_index, new_id, save_config, save_projects_index
from ..core.events import bus
from ..core.index import index
from ..core.models import AssetRequest, ProjectRecord, Status
from . import memory
from .git_ops import GitRepo, git_available
from .task_bus import TaskBus

log = logging.getLogger(__name__)

PHASES: list[dict[str, str]] = [
    {"id": "0", "name": "Planning", "goal": "Concept, /grillme intake, GDD signed off by the human"},
    {"id": "1", "name": "Playable core", "goal": "A controllable character on a real level that runs"},
    {"id": "2", "name": "Game systems", "goal": "Enemies, hazards, scoring or progression wired up"},
    {"id": "3", "name": "Content and audio", "goal": "Levels beyond the first, assets, BGM and SFX"},
    {"id": "4", "name": "UI and narrative", "goal": "Menus, HUD, dialogue and cutscene beats"},
    {"id": "5", "name": "Polish and ship", "goal": "Balance pass, performance, export build, credits"},
]

SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")
    return slug[:48] or "project"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- index of projects ----------------------------------------------------------------


def list_projects() -> list[dict[str, Any]]:
    return list(load_projects_index().get("projects") or [])


def get_project(project_id: str) -> dict[str, Any] | None:
    for record in list_projects():
        if record.get("project_id") == project_id:
            return record
    return None


def active_project() -> dict[str, Any] | None:
    index_data = load_projects_index()
    active_id = index_data.get("active_project_id") or load_config().active_project_id
    if active_id:
        record = get_project(active_id)
        if record:
            return record
    projects = list_projects()
    return projects[-1] if projects else None


def set_active(project_id: str) -> dict[str, Any] | None:
    record = get_project(project_id)
    if record is None:
        return None
    index_data = load_projects_index()
    index_data["active_project_id"] = project_id
    save_projects_index(index_data)
    save_config(load_config().model_copy(update={"active_project_id": project_id}))
    record["last_opened"] = utcnow()
    _update_record(record)
    return record


def _update_record(record: dict[str, Any]) -> None:
    index_data = load_projects_index()
    projects = index_data.get("projects") or []
    for position, existing in enumerate(projects):
        if existing.get("project_id") == record.get("project_id"):
            projects[position] = {**existing, **record}
            break
    index_data["projects"] = projects
    save_projects_index(index_data)
    try:
        index.upsert_project(record)
    except Exception as exc:  # pragma: no cover
        log.debug("Could not index project: %s", exc)


def project_path_of(record: dict[str, Any]) -> Path:
    return Path(record.get("path", "")).expanduser()


def bus_for(record: dict[str, Any] | None) -> TaskBus | None:
    if not record:
        return None
    path = project_path_of(record)
    if not path.exists():
        return None
    return TaskBus(path, record.get("project_id", path.name))


# --- creation -------------------------------------------------------------------------


def create_project(
    *,
    name: str,
    mode: str = "fresh",
    existing_path: str = "",
    genre: str = "auto",
    art_style: str = "auto",
    perspective: str = "auto",
    godot_version: str = "auto",
    parent_dir: str = "",
    git_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a project folder (fresh) or adopt an existing Godot project."""
    config = load_config()
    root = Path(parent_dir).expanduser() if parent_dir else Path(config.projects_root or paths.projects_root())
    if mode == "existing" and existing_path:
        project_dir = Path(existing_path).expanduser().resolve()
        if not project_dir.exists():
            raise FileNotFoundError(f"{project_dir} does not exist.")
    else:
        project_dir = (root / slugify(name)).resolve()
        if project_dir.exists() and any(project_dir.iterdir()):
            # Never overwrite an existing folder; pick the next free name.
            for suffix in range(2, 100):
                candidate = (root / f"{slugify(name)}-{suffix}").resolve()
                if not candidate.exists() or not any(candidate.iterdir()):
                    project_dir = candidate
                    break

    paths.ensure_project_tree(project_dir)

    record = ProjectRecord(
        project_id=new_id("proj"),
        name=name.strip() or project_dir.name,
        path=str(project_dir),
        mode="existing" if mode == "existing" else "fresh",
        genre=genre,
        art_style=art_style,
        perspective=perspective,
        godot_version=godot_version,
        phase=0,
        status="planning",
    ).model_dump(mode="json")

    # Memory seeds. progress.md starts with an explicit header so the first agent has context.
    atomic_write_text(
        project_dir / "memory" / "progress.md",
        f"# Progress log - {record['name']}\n"
        f"Created {record['created_at']} by PulseG Studio. Append-only.\n\n",
    )
    memory.append_progress(
        project_dir,
        f"project created ({record['mode']} mode) in {project_dir}",
        agent="system",
        status="INFO",
    )
    _seed_gdd(project_dir, record)
    atomic_write_json(project_dir / "memory" / "task_queue.json", [])
    atomic_write_json(project_dir / "memory" / "completed_tasks.json", [])
    atomic_write_json(project_dir / "memory" / "agent_states.json", {})
    atomic_write_json(project_dir / "memory" / "audit_history.json", [])
    atomic_write_json(project_dir / "knowledge" / "index.json", [])
    atomic_write_json(project_dir / "assets" / "asset_requests.json", [])
    atomic_write_json(project_dir / "planning" / "chat.json", [])

    if record["mode"] == "existing":
        _adopt_existing(project_dir, record)
    else:
        godot_dir = project_dir / "godot_project"
        if not (godot_dir / "project.godot").exists():
            scaffold_godot_project(godot_dir, project_name=record["name"], perspective=perspective)

    # Git
    if config.git.auto_init and git_available():
        repo = GitRepo(project_dir)
        identity = git_identity or {}
        try:
            repo.init(
                user_name=identity.get("name") or config.git.user_name,
                user_email=identity.get("email") or config.git.user_email,
            )
            record["git_initialised"] = True
        except Exception as exc:
            log.warning("Git init failed for %s: %s", project_dir, exc)
            record["git_initialised"] = False

    record["godot_project_path"] = str(project_dir / "godot_project")
    index_data = load_projects_index()
    index_data.setdefault("projects", []).append(record)
    index_data["active_project_id"] = record["project_id"]
    save_projects_index(index_data)
    save_config(config.model_copy(update={"active_project_id": record["project_id"]}))
    try:
        index.upsert_project(record)
    except Exception:  # pragma: no cover
        pass
    bus.publish("project_created", {"project": record})
    return record


def _seed_gdd(project_dir: Path, record: dict[str, Any]) -> None:
    from ..skills import grillme

    scaffold = grillme.draft_gdd_scaffold(record.get("genre", "generic"), "", title=record["name"])
    atomic_write_text(project_dir / "memory" / "gdd.md", scaffold)


def _adopt_existing(project_dir: Path, record: dict[str, Any]) -> None:
    """Inventory an existing project so the agents read before they write (spec E.2).

    Writes ``reports/existing_project_scan.md`` and queues a reconciliation task. The
    Programmer and Documenter must fold this into the GDD before new work is queued.
    """
    godot_dir = project_dir / "godot_project"
    if not (godot_dir / "project.godot").exists():
        # The user pointed at a folder that may contain the Godot project deeper down.
        candidates = list(project_dir.rglob("project.godot"))[:1]
        if candidates:
            godot_dir = candidates[0].parent
            record["godot_project_path"] = str(godot_dir)

    scenes = sorted(str(path.relative_to(project_dir)) for path in godot_dir.rglob("*.tscn"))[:200]
    scripts = sorted(str(path.relative_to(project_dir)) for path in godot_dir.rglob("*.gd"))[:200]
    scene_bodies = [f"* `{path}`" for path in scenes[:60]]
    script_bodies = [f"* `{path}`" for path in scripts[:60]]
    assets = [
        str(path.relative_to(project_dir))
        for path in (project_dir / "assets").rglob("*")
        if path.is_file()
    ][:200]
    godot_version = "unknown"
    project_file = godot_dir / "project.godot"
    if project_file.exists():
        text = read_text(project_file)
        match = re.search(r'config/features=PackedStringArray\("([^"]+)"', text)
        if match:
            godot_version = match.group(1)
        record["godot_version"] = godot_version

    report = [
        f"# Existing project scan - {record['name']}",
        "",
        f"Scanned {utcnow()}.",
        f"Godot project folder: `{godot_dir}`",
        f"Detected Godot version: {godot_version}",
        "",
        f"## Scenes ({len(scenes)})",
        *(scene_bodies or ["* none found"]),
        "",
        f"## Scripts ({len(scripts)})",
        *(script_bodies or ["* none found"]),
        "",
        f"## Assets in the PulseG asset library ({len(assets)})",
        *(f"* `{path}`" for path in assets[:60] or ["* none yet"]),
        "",
        "## What happens next",
        "1. The Documenter reconciles this inventory into `memory/gdd.md`.",
        "2. The Programmer reads the existing scenes and scripts before writing anything.",
        "3. New tasks only start once the reconciliation task is approved by you.",
        "",
    ]
    atomic_write_text(project_dir / "reports" / "existing_project_scan.md", "\n".join(report))
    memory.append_progress(
        project_dir,
        f"existing project scanned: {len(scenes)} scenes, {len(scripts)} scripts, Godot {godot_version}",
        agent="system",
        status="INFO",
    )


def queue_reconciliation_task(record: dict[str, Any]) -> dict[str, Any] | None:
    """After adopting an existing project, ask the Documenter to reconcile it into the GDD."""
    task_bus = bus_for(record)
    if task_bus is None:
        return None
    task = task_bus.create(
        assigned_to="documenter",
        created_by="system",
        phase=0,
        kind="research",
        title="Reconcile the existing project into the GDD",
        instruction=(
            "Read reports/existing_project_scan.md and the existing scenes and scripts under "
            "godot_project/. Write a faithful `## SUMMARY` for memory/gdd.md describing what "
            "already exists (mechanics actually implemented, characters present, art already "
            "in the project). Do not invent features. List what is missing for the concept the "
            "human described. This must be approved before any new build task is queued."
        ),
        expected_outputs=["memory/gdd.md#SUMMARY", "reports/existing_project_reconciliation.md"],
        file_claims=["memory/gdd.md", "reports/existing_project_reconciliation.md"],
        priority=10,
    )
    if task_bus.get(task.task_id):
        task_bus.transition(task.task_id, Status.IN_PROGRESS) if False else None
    return task.model_dump(mode="json")


# --- Godot scaffolding -----------------------------------------------------------------


GODOT_PROJECT_TEMPLATE = """; Engine configuration file.
; Generated by PulseG Studio. Safe to edit in the Godot editor.

config_version=5

[application]

config/name="{name}"
run/main_scene="res://scenes/main.tscn"
config/features=PackedStringArray("{godot_feature}")
config/icon="res://icon.svg"

[display]

window/size/viewport_width=640
window/size/viewport_height=360
window/stretch/mode="canvas_items"
window/stretch/aspect="keep"

[input]

move_left={{
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":65,"key_label":0,"unicode":97,"location":0,"echo":false,"script":null)
]
}}
move_right={{
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":68,"key_label":0,"unicode":100,"location":0,"echo":false,"script":null)
]
}}
move_up={{
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":87,"key_label":0,"unicode":119,"location":0,"echo":false,"script":null)
]
}}
move_down={{
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":83,"key_label":0,"unicode":115,"location":0,"echo":false,"script":null)
]
}}
jump={{
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":32,"key_label":0,"unicode":32,"location":0,"echo":false,"script":null)
]
}}
interact={{
"deadzone": 0.5,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":69,"key_label":0,"unicode":101,"location":0,"echo":false,"script":null)
]
}}

[input_devices]

pointing/emulate_touch_from_mouse=false

[layer_names]

2d_physics/layer_1="world"
2d_physics/layer_2="player"
2d_physics/layer_3="enemies"
2d_physics/layer_4="pickups"
2d_physics/layer_5="hazards"

[rendering]

textures/canvas_textures/default_texture_filter=0
"""

MAIN_SCENE_TEMPLATE = """[gd_scene load_steps=2 format=3 uid="uid://bpulsegmain01"]

[ext_resource type="Script" path="res://scripts/main.gd" id="1_main"]

[node name="Main" type="Node2D"]
script = ExtResource("1_main")
"""

MAIN_SCRIPT_TEMPLATE = '''extends Node2D
## Entry point scaffolded by PulseG Studio.
## The Programmer agent replaces this with the real game loop once Phase 1 is approved.

@onready var _label: Label = $HUD/Status


func _ready() -> void:
\t_label.text = "PulseG Studio scaffold - the build team is working."


func _process(_delta: float) -> void:
\tpass
'''

GAME_STATE_TEMPLATE = '''extends Node
## Autoload holding run state. Kept deliberately small; systems read and write this.

signal health_changed(current: int, maximum: int)
signal score_changed(score: int)

@export var max_health: int = 3

var health: int = 3
var score: int = 0


func _ready() -> void:
\thealth = max_health


func damage(amount: int) -> void:
\thealth = maxi(0, health - amount)
\thealth_changed.emit(health, max_health)
\tif health == 0:
\t\tEvents.game_over.emit("health")


func add_score(amount: int) -> void:
\tscore += amount
\tscore_changed.emit(score)


func reset() -> void:
\thealth = max_health
\tscore = 0
\thealth_changed.emit(health, max_health)
\tscore_changed.emit(score)
'''

EVENTS_TEMPLATE = '''extends Node
## Global signal bus. Systems emit, UI listens. Avoids hard node references.

signal game_over(reason: String)
signal level_completed(level_id: String)
signal checkpoint_reached(checkpoint_id: String)
'''


def scaffold_godot_project(
    godot_dir: Path, *, project_name: str, perspective: str = "auto", godot_feature: str = "4.4"
) -> dict[str, Any]:
    """Write a minimal but genuinely valid Godot 4 project.

    It must open in the editor and run immediately - an empty folder that fails to open
    would waste the user's first minutes with the product.
    """
    (godot_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (godot_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (godot_dir / "autoload").mkdir(parents=True, exist_ok=True)
    (godot_dir / "assets").mkdir(parents=True, exist_ok=True)

    atomic_write_text(
        godot_dir / "project.godot",
        GODOT_PROJECT_TEMPLATE.format(name=project_name, godot_feature=godot_feature),
    )
    atomic_write_text(godot_dir / "scripts" / "main.gd", MAIN_SCRIPT_TEMPLATE)
    atomic_write_text(godot_dir / "autoload" / "game_state.gd", GAME_STATE_TEMPLATE)
    atomic_write_text(godot_dir / "autoload" / "events.gd", EVENTS_TEMPLATE)
    atomic_write_text(godot_dir / "scenes" / "main.tscn", MAIN_SCENE_TEMPLATE)

    hud_scene = godot_dir / "scenes" / "hud.tscn"
    atomic_write_text(
        hud_scene,
        '[gd_scene format=3 uid="uid://bpulseghud01"]\n\n'
        '[node name="HUD" type="CanvasLayer"]\n\n'
        '[node name="Status" type="Label" parent="."]\n'
        "offset_left = 8.0\n"
        "offset_top = 8.0\n"
        "offset_right = 400.0\n"
        "offset_bottom = 32.0\n"
        'text = "PulseG Studio"\n',
    )

    # icon.svg: a small, valid file so the editor does not warn on first open.
    atomic_write_text(
        godot_dir / "icon.svg",
        '<svg width="128" height="128" viewBox="0 0 128 128" xmlns="http://www.w3.org/2000/svg">'
        '<rect width="128" height="128" rx="20" fill="#1E1C21"/>'
        '<path d="M40 92V36h26a18 18 0 0 1 0 36H52" stroke="#FAF33E" stroke-width="10" '
        'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>\n',
    )

    # Autoloads must be declared for the scaffold scripts to exist at runtime.
    project_text = read_text(godot_dir / "project.godot")
    if "[autoload]" not in project_text:
        project_text += (
            "\n[autoload]\n\n"
            'GameState="*res://autoload/game_state.gd"\n'
            'Events="*res://autoload/events.gd"\n'
        )
        atomic_write_text(godot_dir / "project.godot", project_text)

    return {"path": str(godot_dir), "files": 6, "perspective": perspective}


# --- asset library ---------------------------------------------------------------------


ASSET_KINDS = {
    ".png": "sprite",
    ".jpg": "background",
    ".jpeg": "background",
    ".svg": "ui",
    ".ogg": "sfx",
    ".wav": "sfx",
    ".mp3": "bgm",
    ".txt": "reference",
    ".md": "reference",
}


def ingest_assets(record: dict[str, Any], source_dir: str, *, kind: str = "") -> dict[str, Any]:
    """Copy user assets into the project library and index them (spec E.2).

    Agents check this library before generating anything, so ingestion is what makes
    "reuse before regenerate" work.
    """
    project_dir = project_path_of(record)
    source = Path(source_dir).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"{source} does not exist.")
    target_root = project_dir / "assets"
    copied: list[dict[str, str]] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        resolved_kind = kind or ASSET_KINDS.get(suffix, "other")
        if resolved_kind == "other":
            continue
        sub = _subfolder_for(resolved_kind, suffix)
        target = target_root / sub / path.name
        counter = 1
        while target.exists():
            target = target_root / sub / f"{path.stem}_{counter}{suffix}"
            counter += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(
            {
                "path": str(target.relative_to(project_dir)),
                "kind": resolved_kind,
                "original": str(path),
            }
        )
    library_path = target_root / "library.json"
    library = read_json(library_path, default=[]) or []
    if not isinstance(library, list):
        library = []
    library.extend(copied)
    atomic_write_json(library_path, library)
    memory.append_progress(
        project_dir,
        f"ingested {len(copied)} user asset(s) into the asset library",
        agent="human",
        status="INFO",
    )
    bus.publish(
        "assets_ingested",
        {"project_id": record.get("project_id"), "count": len(copied), "target": str(target_root)},
    )
    return {"copied": copied, "total": len(library)}


def _subfolder_for(kind: str, suffix: str) -> str:
    if kind in ("sprite", "tile"):
        return "sprites" if kind == "sprite" else "tiles"
    if kind == "background":
        return "backgrounds"
    if kind == "ui":
        return "ui"
    if suffix == ".mp3":
        return "audio/bgm"
    if kind in ("sfx", "bgm"):
        return f"audio/{kind}"
    return "references"


def asset_library(record: dict[str, Any]) -> list[dict[str, Any]]:
    project_dir = project_path_of(record)
    library = read_json(project_dir / "assets" / "library.json", default=[]) or []
    entries: list[dict[str, Any]] = list(library) if isinstance(library, list) else []
    known = {entry.get("path") for entry in entries}
    for path in sorted((project_dir / "assets").rglob("*")):
        if not path.is_file() or path.name in ("library.json", "asset_requests.json"):
            continue
        relative = str(path.relative_to(project_dir))
        if relative in known:
            continue
        suffix = path.suffix.lower()
        entries.append(
            {
                "path": relative,
                "kind": ASSET_KINDS.get(suffix, "other"),
                "original": "generated",
                "size_bytes": path.stat().st_size,
            }
        )
    return entries


def asset_requests(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [request.model_dump(mode="json") for request in memory.read_asset_requests(project_path_of(record))]


def create_asset_request(
    record: dict[str, Any],
    *,
    name: str,
    kind: str,
    description: str,
    requested_by: str = "human",
    needed_by_task: str = "",
) -> dict[str, Any]:
    request = AssetRequest(
        request_id=new_id("asset"),
        project_id=record.get("project_id", ""),
        name=name,
        kind=kind,  # type: ignore[arg-type]
        description=description,
        requested_by=requested_by,
        needed_by_task=needed_by_task,
    )
    stored = memory.add_asset_request(project_path_of(record), request)
    bus.publish("asset_request", stored.model_dump(mode="json"))
    return stored.model_dump(mode="json")


# --- summaries ------------------------------------------------------------------------


def project_overview(record: dict[str, Any]) -> dict[str, Any]:
    """Everything the Overview screen needs about one project."""
    project_dir = project_path_of(record)
    stats = memory.project_stats(project_dir)
    task_bus = bus_for(record)
    repo = GitRepo(project_dir)
    return {
        **record,
        "exists": project_dir.exists(),
        "stats": stats,
        "phase": task_bus.phase_progress() if task_bus else {"phase": record.get("phase", 0)},
        "phases": PHASES,
        "git": repo.status() if repo.initialised else {"initialised": False},
        "gdd_summary": memory.gdd_summary(project_dir)[:1200],
        "memory_files": {
            "progress_lines": memory.progress_line_count(project_dir),
            "gdd_sections": memory.gdd_sections(project_dir),
        },
    }


def web_build_path(record: dict[str, Any]) -> Path:
    return project_path_of(record) / "web_build"


def forget_project(project_id: str, *, delete_files: bool = False) -> dict[str, Any]:
    """Remove a project from the studio. Files are kept unless explicitly deleted."""
    record = get_project(project_id)
    if record is None:
        return {"removed": False, "reason": "not found"}
    index_data = load_projects_index()
    index_data["projects"] = [
        item for item in index_data.get("projects", []) if item.get("project_id") != project_id
    ]
    if index_data.get("active_project_id") == project_id:
        index_data["active_project_id"] = ""
    save_projects_index(index_data)
    try:
        index.delete_project(project_id)
    except Exception:  # pragma: no cover
        pass
    deleted = False
    if delete_files:
        target = project_path_of(record)
        if target.exists() and str(target) not in ("/", str(Path.home())):
            shutil.rmtree(target, ignore_errors=True)
            deleted = True
    bus.publish("project_removed", {"project_id": project_id, "files_deleted": deleted})
    return {"removed": True, "files_deleted": deleted, "record": record}


def phases() -> list[dict[str, str]]:
    return PHASES
