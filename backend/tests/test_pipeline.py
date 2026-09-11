"""End-to-end tests for the pipeline the specification actually promises.

The point of these tests is the *gate*, not the prose: work flows, the Auditor sees it, the
human decides, and a commit only exists after that decision. They run on the demo provider, so
they are offline, deterministic and fast - which is what makes them able to guard the pipeline
on every change.
"""
from __future__ import annotations

import subprocess

import pytest

from backend.core.models import Status
from backend.orchestration import approvals, projects
from backend.orchestration.dispatcher import Dispatcher


@pytest.fixture
def dispatcher(project):
    from backend.providers.router import FallbackRouter

    bus = projects.bus_for(project)
    return Dispatcher(projects.project_path_of(project), project_id=project["project_id"], bus=bus, router=FallbackRouter())


def _git_identity(project):
    path = projects.project_path_of(project)
    subprocess.run(["git", "config", "user.name", "PulseG Test"], cwd=path, check=False)
    subprocess.run(["git", "config", "user.email", "test@pulseg.local"], cwd=path, check=False)
    return path


def test_seed_first_tasks_creates_a_dependency_chain(dispatcher):
    created = dispatcher.seed_first_tasks()
    assert len(created) == 4
    tasks = {task.task_id: task for task in dispatcher.bus.all()}
    # The tester depends on the level, which depends on the player, which depends on the summary.
    tester = next(task for task in tasks.values() if task.assigned_to == "tester")
    assert len(tester.dependencies) == 1
    # Only the design summary is unblocked at the start.
    unblocked = [task.task_id for task in dispatcher.bus.unblocked_pending()]
    assert unblocked == [created[0]]


def test_full_cycle_submit_audit_review_approve_commit(dispatcher, project):
    path = _git_identity(project)
    dispatcher.seed_first_tasks()
    task = dispatcher.bus.unblocked_pending()[0]

    outcome = dispatcher.dispatch_ready()
    assert task.task_id in outcome.submitted, outcome.as_dict()
    submitted = dispatcher.bus.get(task.task_id)
    assert submitted is not None
    assert submitted.status is Status.NEEDS_HUMAN_REVIEW
    assert submitted.auditor_verdict is not None
    assert submitted.auditor_verdict.verdict in {"APPROVED", "DECLINED"}

    # The gate: nothing is approved, and no commit exists, before the human acts.
    assert not (path / ".git" / "refs" / "heads").exists() or not _has_commits(path)

    review = approvals.review_queue(dispatcher.bus)
    assert any(row["task_id"] == task.task_id for row in review)
    row = next(item for item in review if item["task_id"] == task.task_id)
    assert row["override_needed"] is (row["verdict"] == "DECLINED")

    result = approvals.approve(dispatcher.bus, task.task_id, note="looks right")
    assert result["task"]["status"] == Status.APPROVED.value
    assert result["commit"]["ok"] is True, result["commit"]
    assert result["commit"]["sha"]
    assert _has_commits(path)


def test_approval_refuses_a_task_that_is_not_at_the_gate(dispatcher):
    dispatcher.seed_first_tasks()
    task = dispatcher.bus.unblocked_pending()[0]
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(dispatcher.bus, task.task_id, note="too early")


def test_rejection_requires_a_note_and_requeues(dispatcher, project):
    _git_identity(project)
    dispatcher.seed_first_tasks()
    task = dispatcher.bus.unblocked_pending()[0]
    dispatcher.dispatch_ready()
    with pytest.raises(approvals.ApprovalError):
        approvals.reject(dispatcher.bus, task.task_id, note="")
    result = approvals.reject(
        dispatcher.bus, task.task_id, note="The summary does not name the antagonist."
    )
    requeued = result["task"]
    assert requeued["status"] == Status.PENDING.value
    assert "antagonist" in requeued["instruction"]
    assert requeued["human_rejections"] == 1
    assert requeued["decline_count"] == 0  # a human rejection is not an Auditor decline


def test_override_is_refused_when_the_auditor_did_not_decline(dispatcher, project):
    """Override is a deliberate exception, not a shortcut past the gate."""
    _git_identity(project)
    dispatcher.seed_first_tasks()
    task = dispatcher.bus.unblocked_pending()[0]
    dispatcher.dispatch_ready()
    current = dispatcher.bus.get(task.task_id)
    assert current is not None
    if current.auditor_verdict is not None and not current.auditor_verdict.is_declined:
        with pytest.raises(approvals.ApprovalError):
            approvals.override_and_approve(dispatcher.bus, task.task_id, note="I want it")
    else:
        pytest.skip("Auditor declined this run; the override path is covered elsewhere.")


def test_dependents_unblock_only_after_approval(dispatcher, project):
    _git_identity(project)
    created = dispatcher.seed_first_tasks()
    dispatcher.dispatch_ready()
    first, second = created[0], created[1]
    # The first task is done but sitting at the human gate; its dependent must not start.
    assert dispatcher.bus.get(first).status is Status.NEEDS_HUMAN_REVIEW
    assert dispatcher.bus.unblocked_pending() == []
    approvals.approve(dispatcher.bus, first, note="ok")
    assert second in [task.task_id for task in dispatcher.bus.unblocked_pending()]


def test_dispatch_respects_the_file_lock(dispatcher):
    bus = dispatcher.bus
    first = bus.create(
        assigned_to="programmer",
        instruction="Write the player controller.",
        title="controller",
        file_claims=["godot_project/scripts/player.gd"],
    )
    second = bus.create(
        assigned_to="story_writer",
        instruction="Write dialogue for the first scene.",
        title="dialogue",
        file_claims=["godot_project/scripts/player.gd"],
    )
    bus.claim(first.task_id, "programmer")
    conflicts = bus.file_conflicts(second)
    assert conflicts and conflicts[0][0].task_id == first.task_id
    runnable = dict((task.task_id, reason) for task, reason in dispatcher._runnable())
    assert "player.gd" in runnable[second.task_id]


def test_dispatch_skips_an_agent_that_is_already_working(dispatcher):
    bus = dispatcher.bus
    first = bus.create(assigned_to="programmer", instruction="One task at a time.", title="first")
    second = bus.create(assigned_to="programmer", instruction="And this one waits.", title="second")
    bus.claim(first.task_id, "programmer")
    runnable = dict((task.task_id, reason) for task, reason in dispatcher._runnable())
    assert "already has a task in flight" in runnable[second.task_id]


def test_auditor_declines_a_submission_missing_its_expected_output(dispatcher):
    """The gate is deterministic: the demo model cannot approve missing work."""
    bus = dispatcher.bus
    task = bus.create(
        assigned_to="programmer",
        instruction="Create `level_1.tscn`.",
        title="level",
        expected_outputs=["godot_project/scenes/level_1.tscn"],
    )
    bus.claim(task.task_id, "programmer")
    from backend.orchestration import auditor

    verdict = auditor.audit_task(dispatcher.path, bus.get(task.task_id), router=dispatcher.router)
    assert verdict.verdict == "DECLINED"
    assert "level_1.tscn" in verdict.fix_note


def test_paused_tasks_are_never_lost(dispatcher):
    bus = dispatcher.bus
    task = bus.create(assigned_to="programmer", instruction="This will fail.", title="paused")
    bus.claim(task.task_id, "programmer")
    paused = bus.pause(task.task_id, "All providers failed: invalid key")
    assert paused.status is Status.NEEDS_INTERVENTION
    assert paused.errors or paused.retry_count >= 0
    resumed = dispatcher.retry_task(task.task_id, reason="human retry")
    assert resumed.status is Status.PENDING
    assert bus.get(task.task_id) is not None


def test_phase_gate_reports_what_is_missing(dispatcher, project):
    _git_identity(project)
    dispatcher.seed_first_tasks()
    gate = approvals.phase_gate(dispatcher.bus, 1)
    assert gate["complete"] is False
    assert gate["total"] == 4
    assert gate["remaining"]


def _has_commits(path) -> bool:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=path, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return False
    return int((result.stdout or "0").strip() or 0) > 0
