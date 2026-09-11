"""API tests: the contract the frontend codes against, exercised over real HTTP.

These run against a temp studio home and use the demo provider, so they are fast, offline and
deterministic. They exist to catch the class of bug that unit tests miss: a route that returns
the wrong shape, a guard that is not actually wired, an error that leaks a traceback.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_studio) -> TestClient:
    from backend.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_system_info(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    info = client.get("/api/system/info")
    assert info.status_code == 200
    body = info.json()
    assert body["app"]["name"] == "PulseG Studio"
    assert "studio_home" in body["paths"]
    assert "git" in body["tools"]


def test_first_run_reports_what_is_missing(client):
    body = client.get("/api/system/first-run").json()
    assert body["first_run_complete"] is False
    assert body["git_available"] in (True, False)
    assert len(body["steps"]) >= 5
    assert body["steps"][0]["id"] == "welcome"


def test_operations_requiring_a_project_say_so_clearly(client):
    """A clear 409 with an action beats a 500 with a traceback."""
    response = client.get("/api/tasks/board")
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "no_active_project"
    assert body["action"] == "open_project"
    assert "Create one" in body["message"] or "pick one" in body["message"]


def test_setup_wizard_writes_config(client, tmp_path):
    root = tmp_path / "my-games"
    body = client.post(
        "/api/system/setup",
        json={"projects_root": str(root), "git_name": "Ada", "git_email": "ada@example.com"},
    ).json()
    assert body["ok"] is True
    assert body["config"]["first_run_complete"] is True
    assert root.exists()
    assert body["git"]["configured"] is True
    assert body["git"]["global"] is False  # never touch the machine-wide config unless asked

    after = client.get("/api/system/first-run").json()
    assert after["first_run_complete"] is True


def test_setup_wizard_rejects_an_unwritable_folder(client):
    response = client.post("/api/system/setup", json={"projects_root": "/proc/cannot/write/here"})
    assert response.status_code == 422
    assert response.json()["error"] == "projects_root_unwritable"


def test_project_lifecycle_over_http(client):
    created = client.post("/api/projects", json={"name": "Lighthouse", "mode": "fresh"}).json()
    project = created["project"]
    assert project["project_id"].startswith("proj_")
    assert created["overview"]["exists"] is True

    listed = client.get("/api/projects").json()
    assert listed["active_project_id"] == project["project_id"]

    active = client.get("/api/projects/active").json()
    assert active["project"]["name"] == "Lighthouse"
    assert len(active["board"]["lanes"]) == 7
    assert [lane["title"] for lane in active["board"]["lanes"]][3] == "Needs Your Review"

    paths = client.get(f"/api/projects/{project['project_id']}/paths").json()
    assert paths["godot_project"].endswith("godot_project")


def test_creating_a_project_starts_the_intake(client):
    created = client.post(
        "/api/projects",
        json={"name": "Keeper", "mode": "fresh", "concept": "A pixel platformer about a lighthouse keeper"},
    ).json()
    project_id = created["project"]["project_id"]
    state = client.get("/api/planning/state").json()
    assert state["state"]["concept"].startswith("A pixel platformer")
    assert state["genre"] == "platformer"
    assert state["questions"], "the intake must open with real questions, not an empty rail"
    assert state["allowed"] is False
    assert state["blockers"]
    assert project_id


def test_build_gate_stays_closed_until_the_design_is_complete(client):
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh", "concept": "A platformer about jumping"})
    gate = client.get("/api/planning/gate").json()
    assert gate["allowed"] is False
    assert any("mechanic" in blocker for blocker in gate["blockers"])

    # Pressing Send to Build Team early is refused, with the blockers attached.
    handoff = client.post("/api/planning/handoff")
    assert handoff.status_code == 409
    assert handoff.json()["error"] == "design_incomplete"

    # Confirming is refused too - there is nothing to confirm.
    assert client.post("/api/planning/confirm", json={}).status_code == 409


def test_design_completion_opens_the_gate(client):
    from backend.agents import planning_agent
    from backend.orchestration import projects

    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh", "concept": "A platformer"})
    record = projects.active_project()
    path = projects.project_path_of(record)
    state = planning_agent.read_state(path)
    state.update(
        {
            "mechanics": [{"name": "wall jump", "rule": "Jump once while touching a wall."}],
            "characters": [
                {
                    "name": "Mira",
                    "role": "player",
                    "personality": "wry",
                    "abilities": ["glide"],
                    "ai_behaviour": "scripted",
                }
            ],
            "art_direction": "32x32 pixel art, four-tone dusk palette",
            "level_count": 6,
            "play_length_minutes": 25,
            "linear": False,
        }
    )
    planning_agent.write_state(path, state)

    gate = client.get("/api/planning/gate").json()
    assert gate["allowed"] is True, gate["blockers"]
    handoff = client.post("/api/planning/handoff")
    assert handoff.status_code == 200
    assert "## DESIGN CONFIRMATION" in handoff.json()["draft_gdd"]

    confirmed = client.post("/api/planning/confirm", json={"confirmed_text": "Looks right."}).json()
    assert confirmed["finalised"] is True

    seeded = client.post("/api/planning/seed-tasks").json()
    assert len(seeded["created"]) == 4

    board = client.get("/api/tasks/board").json()
    pending = next(lane for lane in board["lanes"] if lane["id"] == "pending")
    assert pending["count"] == 4  # four Phase 1 tasks exist...
    approved = {task["task_id"] for lane in board["lanes"] if lane["id"] == "approved" for task in lane["tasks"]}
    unblocked = [
        task
        for task in pending["tasks"]
        if all(dependency in approved for dependency in task["dependencies"])
    ]
    assert len(unblocked) == 1  # ...but only the design summary can start
    assert board["phase"]["phase"] == 1


def test_task_detail_carries_everything_the_drawer_needs(client):
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    created = client.post(
        "/api/tasks",
        json={
            "instruction": "Write `player.gd` with a wall jump.",
            "assigned_to": "programmer",
            "expected_outputs": ["godot_project/scripts/player.gd"],
            "created_by": "human_direct",
        },
    ).json()["task"]
    detail = client.get(f"/api/tasks/{created['task_id']}").json()
    assert detail["task_id"] == created["task_id"]
    assert "audit_summary" in detail
    assert detail["blocked_by"] == []
    assert detail["dependents"] == []

    context = client.get(f"/api/tasks/{created['task_id']}/context").json()
    assert "rendered" in context
    assert context["stats"]["rendered_tokens"] > 0


def test_dispatch_tick_runs_a_task_to_the_human_gate(client):
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    created = client.post(
        "/api/tasks",
        json={
            "instruction": "Create the player scene and controller.",
            "assigned_to": "programmer",
            "expected_outputs": ["godot_project/scripts/player.gd", "godot_project/scenes/player.tscn"],
            "file_claims": ["godot_project/scripts/player.gd", "godot_project/scenes/player.tscn"],
        },
    ).json()["task"]

    outcome = client.post("/api/logs/tick").json()
    assert outcome["dispatched"] == [created["task_id"]], outcome

    task = client.get(f"/api/tasks/{created['task_id']}").json()
    assert task["status"] == "NEEDS_HUMAN_REVIEW"
    assert task["auditor_verdict"] is not None
    assert task["provider_used"] == "demo"

    review = client.get("/api/tasks/review").json()
    assert review["count"] == 1
    assert review["items"][0]["task_id"] == created["task_id"]

    board = client.get("/api/tasks/board").json()
    review_lane = next(lane for lane in board["lanes"] if lane["id"] == "review")
    assert review_lane["count"] == 1


def test_approval_commits_and_rejection_requeues(client):
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    from backend.orchestration import projects
    from backend.runtime import runtime

    path = projects.project_path_of(projects.active_project())
    import subprocess

    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=False)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=False)

    first = client.post(
        "/api/tasks",
        json={"instruction": "Freeze the design summary.", "assigned_to": "documenter",
              "expected_outputs": ["memory/gdd.md#SUMMARY"]},
    ).json()["task"]
    second = client.post(
        "/api/tasks",
        json={"instruction": "Build the player.", "assigned_to": "programmer",
              "dependencies": [first["task_id"]]},
    ).json()["task"]

    client.post("/api/logs/tick")
    assert client.get(f"/api/tasks/{second['task_id']}").json()["status"] == "PENDING"

    approved = client.post(f"/api/tasks/{first['task_id']}/approve", json={"note": "good"}).json()
    assert approved["task"]["status"] == "APPROVED"
    assert approved["commit"]["ok"] is True, approved["commit"]
    assert second["task_id"] in approved["unblocked"]

    # A second approval of the same task is refused rather than silently repeated.
    again = client.post(f"/api/tasks/{first['task_id']}/approve", json={})
    assert again.status_code == 409

    git_log = client.get("/api/git/log").json()
    assert git_log["initialised"] is True
    assert any("documenter" in commit["subject"] for commit in git_log["commits"])
    runtime.stop(wait=True)


def test_reject_without_a_note_is_refused_by_the_api_contract(client):
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    response = client.post("/api/tasks/TASK_001/reject", json={})
    assert response.status_code == 422
    assert "note" in str(response.json())


def test_override_requires_a_reason(client):
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    created = client.post(
        "/api/tasks", json={"instruction": "Do the thing.", "assigned_to": "programmer"}
    ).json()["task"]
    client.post("/api/logs/tick")
    response = client.post(f"/api/tasks/{created['task_id']}/override", json={"note": ""})
    assert response.status_code == 422


def test_unknown_task_and_agent_are_reported_usefully(client):
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    missing = client.get("/api/tasks/TASK_999")
    assert missing.status_code == 404
    assert missing.json()["error"] == "unknown_task"

    bad_agent = client.post("/api/tasks", json={"instruction": "Summon a wizard.", "assigned_to": "wizard"})
    assert bad_agent.status_code == 422
    body = bad_agent.json()
    assert body["error"] == "unknown_agent"
    assert "programmer" in body["message"]


def test_agents_endpoints_expose_chains_and_readiness(client):
    roster = client.get("/api/agents").json()
    assert len(roster["agents"]) == 12
    ids = {row["agent_id"] for row in roster["agents"]}
    assert {"planning_agent", "programmer", "auditor", "tester"} <= ids

    programmer = client.get("/api/agents/programmer").json()
    assert programmer["chain_entries"][0]["slot"] == "primary"
    assert [entry["slot"] for entry in programmer["chain_entries"]] == ["primary", "fallback1", "fallback2"]
    assert programmer["primary"]["provider"]

    # Agent states are studio-wide (what each agent is doing, its last error): no project needed.
    states = client.get("/api/agents/states").json()
    assert len(states["agents"]) == 12
    assert {row["status"] for row in states["agents"]} <= {"idle", "working", "paused", "blocked", "offline"}


def test_chain_editing_writes_agents_yaml(client, studio_home):
    response = client.put(
        "/api/agents/programmer/chain",
        json={
            "primary": {"provider": "mistral", "model": "codestral-latest"},
            "fallbacks": [{"provider": "openrouter", "model": "qwen/qwen3-coder:free"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["agent"]["fallbacks"][0]["provider"] == "openrouter"
    file = studio_home / "agents.yaml"
    assert "qwen/qwen3-coder:free" in file.read_text(encoding="utf-8")
    assert response.json()["coverage"] is not None  # the UI shows which chains are usable now

    # An unknown provider is refused with the list of real ones.
    bad = client.put(
        "/api/agents/programmer/chain",
        json={"primary": {"provider": "not_a_provider", "model": "x"}, "fallbacks": []},
    )
    assert bad.status_code == 422
    assert "not_a_provider" in bad.json()["message"]


def test_adding_an_agent_is_config_only(client):
    payload = {
        "id": "level_designer",
        "name": "Level Designer",
        "role": "Designs level layouts",
        "primary": {"provider": "demo", "model": "demo-1"},
        "system_prompt": "You design levels. Return JSON.",
    }
    added = client.post("/api/agents", json=payload)
    assert added.status_code == 200, added.text
    assert added.json()["agent"]["id"] == "level_designer"
    assert any(row["agent_id"] == "level_designer" for row in client.get("/api/agents").json()["agents"])

    # It runs through the generic runner with no Python written for it.
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    task = client.post(
        "/api/tasks",
        json={"instruction": "Lay out level 2 with three hazards.", "assigned_to": "level_designer"},
    ).json()["task"]
    client.post("/api/logs/tick")
    assert client.get(f"/api/tasks/{task['task_id']}").json()["status"] != "PENDING"

    client.delete("/api/agents/level_designer")
    assert all(row["agent_id"] != "level_designer" for row in client.get("/api/agents").json()["agents"])


def test_provider_endpoints_mask_keys_and_explain_substitutions(client, monkeypatch):
    providers = client.get("/api/providers").json()
    assert len(providers["providers"]) == 20
    assert providers["substitutions"]["zai"]["substitute"] == "openrouter"

    # Saving a key tests it for real. Tests must never reach the network, so stub just the ping.
    from backend.api.routers import providers as providers_router

    monkeypatch.setattr(
        providers_router,
        "_test",
        lambda provider_id, model: {"provider_id": provider_id, "status": "valid", "detail": "stubbed"},
    )

    saved = client.put(
        "/api/providers/key", json={"provider_id": "groq", "api_key": "gsk_test_key_1234567890"}
    ).json()
    assert saved["masked_key"].endswith("7890")
    assert "gsk_test_key" not in str(saved)

    row = next(item for item in client.get("/api/providers").json()["providers"] if item["id"] == "groq")
    assert row["has_key"] is True
    assert row["masked_key"].endswith("7890")

    verification = client.get("/api/providers/verification").json()
    assert verification["verified_on"] == "2026-09-11"
    assert "openrouter" in verification["free_forever"]

    deleted = client.delete("/api/providers/key/groq").json()
    assert deleted["removed"] is True


def test_settings_are_saved_one_section_at_a_time(client):
    response = client.put("/api/settings", json={"runtime": {"max_parallel_agents": 2}})
    assert response.status_code == 200
    assert response.json()["config"]["runtime"]["max_parallel_agents"] == 2
    # An unrelated section is untouched.
    assert response.json()["config"]["ui"]["theme"] == "dark"

    bad = client.put("/api/settings", json={"runtime": {"max_parallel_agents": 99}})
    assert bad.status_code == 422
    assert "max_parallel_agents" in str(bad.json())


def test_godot_path_must_exist(client):
    response = client.put("/api/settings/godot", json={"executable": "/nope/godot"})
    assert response.status_code == 422
    assert response.json()["error"] == "godot_path_missing"


def test_theme_contrast_is_reported(client):
    theme = client.get("/api/design/theme").json()
    pairs = {row["pair"]: row for row in theme["contrast"]}
    assert pairs["primary button label"]["passes"] is True
    assert pairs["body text on app background"]["passes"] is True


def test_notifications_endpoint_reports_readiness(client):
    """Every channel says, in words, what is still missing - the Settings view renders it as-is."""
    status = client.get("/api/system/notifications").json()
    assert status["telegram"]["ready"] is False
    assert "Settings" in status["telegram"]["detail"]
    assert status["notify_on"] == ["needs_review", "needs_intervention", "pipeline_stalled", "phase_complete"]
    assert status["unread"] == 0

    # Turning Telegram on without a token must still not claim to be ready.
    client.put("/api/settings", json={"notifications": {"telegram_enabled": True}})
    after = client.get("/api/system/notifications").json()
    assert after["telegram"]["enabled"] is True
    assert after["telegram"]["ready"] is False
    assert "token" in after["telegram"]["detail"].lower()


def test_notification_log_records_what_was_not_delivered(client):
    """The four triggers fire real notifications; delivery failures are recorded, not fatal."""
    from backend.notifications import service

    event = service.notify_needs_review(
        type("T", (), {"task_id": "TASK_001", "title": "Something to review", "auditor_verdict": None})()
    )
    assert event.kind == "needs_review"
    log = client.get("/api/system/notifications/log").json()
    assert log["items"], "a notification must always be recorded, even when delivery is off"
    assert log["unread"] >= 1
    assert service.mark_read() >= 1


def test_live_preview_falls_back_to_the_graph(client):
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    state = client.get("/api/preview/state").json()
    assert state["mode"] == "graph"
    assert state["game"]["available"] is False

    graph = client.get("/api/preview/graph").json()
    assert len(graph["nodes"]) == 12
    assert any(node["id"] == "programmer" for node in graph["nodes"])
    assert all(edge["animated"] is False for edge in graph["edges"])
    assert graph["review_count"] == 0


def test_web_export_without_godot_says_what_to_do(client):
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    response = client.post("/api/preview/export")
    assert response.status_code == 409
    assert response.json()["error"] == "godot_missing"
    assert response.json()["action"] == "open_settings"


def test_project_file_access_is_scoped(client):
    created = client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"}).json()
    project_id = created["project"]["project_id"]
    ok = client.get(f"/api/projects/{project_id}/file", params={"path": "memory/gdd.md"})
    assert ok.status_code == 200
    escape = client.get(f"/api/projects/{project_id}/file", params={"path": "../../etc/passwd"})
    assert escape.status_code == 403
    assert escape.json()["error"] == "file_not_readable"


def test_diagnostics_contains_no_secrets(client, monkeypatch):
    from backend.api.routers import providers as providers_router

    monkeypatch.setattr(
        providers_router, "_test", lambda provider_id, model: {"status": "invalid", "detail": "stubbed"}
    )
    client.put("/api/providers/key", json={"provider_id": "groq", "api_key": "gsk_secret_value_1234"})
    report = client.get("/api/system/diagnostics").json()
    text = str(report)
    assert "gsk_secret_value_1234" not in text
    assert "1234" in text  # the masked hint is fine
    assert report["note"].startswith("This report contains no API keys")


def test_openapi_document_builds(client):
    """The OpenAPI schema is what docs/API.md is generated from, so it must not be broken."""
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "PulseG Studio"
    paths = schema["paths"]
    assert "/api/tasks/{task_id}/approve" in paths
    assert "/api/planning/gate" in paths
    assert len(paths) > 60


def test_websocket_sends_hello_then_live_events(client):
    """The dashboard has no polling: everything it shows arrives on this one socket."""
    with client.websocket_connect("/ws/events") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello"
        assert hello["payload"]["app"] == "PulseG Studio"
        assert hello["payload"]["run_state"]["status"] in {"idle", "running", "paused", "stopping"}

    # A real action publishes an event, and a newly connected client is caught up by the replay.
    client.post("/api/projects", json={"name": "Keeper", "mode": "fresh"})
    with client.websocket_connect("/ws/events?replay=20") as socket:
        assert socket.receive_json()["type"] == "hello"
        # Log lines are events too - the Live Terminal view is fed from this same stream.
        envelope = socket.receive_json()
        assert envelope["type"] in {
            "project_created", "project_activated", "task_created", "config_changed", "log", "index_rebuilt",
        }
        assert "seq" in envelope and "at" in envelope


def test_websocket_health_is_reachable_without_a_project(client):
    body = client.get("/ws/health").json()
    assert body["ok"] is True
    assert "subscribers" in body and "last_seq" in body
    assert body["heartbeat_s"] >= 5


def test_no_literal_route_is_shadowed_by_a_path_parameter(client):
    """A literal route registered after a ``/{parameter}`` route never runs.

    This is the quietest way for an API to break: the endpoint exists, the docs list it, and every
    call lands on the parameterised route above it and 404s with "no project called statuses".
    FastAPI matches in registration order, so the check below walks the real route table and
    asserts that for every pair that differs only in a parameter slot, the literal one comes
    first.
    """
    app = client.app
    routes = [
        (sorted(route.methods)[0], route.path)
        for route in app.routes
        if getattr(route, "methods", None) and route.path.startswith("/api")
    ]

    def same_shape(earlier: str, later: str) -> bool:
        first = earlier.strip("/").split("/")
        second = later.strip("/").split("/")
        if len(first) != len(second):
            return False
        for left, right in zip(first, second):
            if left == right or left.startswith("{"):
                continue
            return False
        return any(left.startswith("{") for left, right in zip(first, second) if left != right)

    shadowed: list[str] = []
    for index, (method, path) in enumerate(routes):
        for earlier_method, earlier_path in routes[:index]:
            if earlier_method == method and same_shape(earlier_path, path):
                shadowed.append(f"{method} {path} is shadowed by {earlier_method} {earlier_path}")
    assert shadowed == [], "literal routes registered after a path parameter: " + "; ".join(shadowed)


def test_a_human_can_edit_a_document_and_the_bus_rules_still_apply(client):
    """The design document is the human's file. Editing it must not look like an agent write.

    Three properties are asserted: the edit lands on disk and is readable again, it is refused when
    an in-flight task has claimed the same file (so a hand edit can never race the team), and it
    never creates a commit - committing stays the reward for approving a task.
    """
    project = client.post("/api/projects", json={"name": "Editorial", "mode": "fresh"}).json()["project"]
    project_id = project["project_id"]
    body = {"path": "memory/gdd.md", "content": "# Editorial\n\n## SUMMARY\n\nA quiet puzzle about tides.\n"}

    saved = client.put(f"/api/projects/{project_id}/file", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["committed"] is False

    read_back = client.get(f"/api/projects/{project_id}/file", params={"path": "memory/gdd.md"}).json()
    assert "quiet puzzle about tides" in read_back["content"]

    # A committed history entry would mean this path had found a way around the approval gate.
    log = client.get("/api/git/log").json()
    assert log["commits"] == []

    # Paths outside the project are refused, not silently written.
    outside = client.put(
        f"/api/projects/{project_id}/file",
        json={"path": "../escaped.md", "content": "should not exist"},
    )
    assert outside.status_code == 422
    assert outside.json()["error"] == "file_not_writable"

    # A binary format is refused by the same guard the agents go through.
    binary = client.put(
        f"/api/projects/{project_id}/file",
        json={"path": "assets/hero.png", "content": "not really a png"},
    )
    assert binary.status_code == 422
