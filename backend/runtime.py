"""Studio runtime: the run state, the dispatch loop, and the active project.

The FastAPI app is thin; this is where the studio actually runs. One process, one runtime, so
there is exactly one place that decides what is being worked on right now - which matters
because two dispatchers on the same project folder would fight over the task queue files.

Responsibilities:

* own the active project (record, task bus, dispatcher) and switch between projects safely,
* run the background dispatch loop with start / pause / stop,
* expose the run state the dashboard's top bar renders,
* recover after a crash: anything left IN_PROGRESS by a previous process is released back to
  PENDING with a note, rather than being stuck forever.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core.config import load_config, update_config
from .core.events import bus
from .core.models import Status, Task
from .orchestration.dispatcher import DispatchOutcome, Dispatcher
from .orchestration.projects import bus_for, project_path_of
from .orchestration import memory, projects

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 0.25


@dataclass
class RunState:
    """What the top bar shows: is the studio working, and on what?"""

    status: str = "idle"          # idle | running | paused | stopping
    project_id: str = ""
    project_name: str = ""
    started_at: float = 0.0
    ticks: int = 0
    last_tick_at: float = 0.0
    last_outcome: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    interval_s: float = 2.0

    @property
    def uptime_s(self) -> int:
        return int(time.time() - self.started_at) if self.started_at else 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "ticks": self.ticks,
            "uptime_s": self.uptime_s,
            "last_tick_at": self.last_tick_at,
            "last_outcome": self.last_outcome,
            "last_error": self.last_error,
            "interval_s": self.interval_s,
        }


class StudioRuntime:
    """Process-wide studio state."""

    def __init__(self) -> None:
        self.state = RunState()
        self._dispatcher: Dispatcher | None = None
        self._record: dict[str, Any] | None = None
        self._loop_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._recovered = False

    # --- active project ------------------------------------------------------------------

    @property
    def record(self) -> dict[str, Any] | None:
        with self._lock:
            if self._record is None:
                self._record = projects.active_project()
                # Name the project on first read. The run state is published to the dashboard and
                # the top bar, and only set_active() used to stamp it - so after a restart with a
                # project already open, the studio ran the dispatch loop with a nameless run state.
                self._stamp_project(self._record)
            return self._record

    def _stamp_project(self, record: dict[str, Any] | None) -> None:
        """Record which project this runtime is working on, for anything that asks."""
        if not record:
            return
        self.state.project_id = str(record.get("project_id", ""))
        self.state.project_name = str(record.get("name", ""))

    @property
    def project_path(self) -> Path | None:
        record = self.record
        return project_path_of(record) if record else None

    def bus(self):
        record = self.record
        return bus_for(record) if record else None

    def set_active(self, project_id: str) -> dict[str, Any] | None:
        """Switch projects. Stops the run first: two projects must never be worked at once."""
        was_running = self.state.status == "running"
        self.stop(wait=True)
        record = projects.set_active(project_id)
        with self._lock:
            self._record = record
            self._dispatcher = None
        if record:
            self._stamp_project(record)
            update_config(active_project_id=str(record.get("project_id", "")))
            recovered = self.recover_in_flight()
            if recovered:
                log.info("Recovered %s in-flight task(s) after switching projects", len(recovered))
            if was_running:
                self.start()
        bus.publish("active_project_changed", {"project_id": project_id, "record": record or {}})
        return record

    def dispatcher(self) -> Dispatcher | None:
        record = self.record
        if record is None:
            return None
        with self._lock:
            if self._dispatcher is None:
                config = load_config()
                self._dispatcher = Dispatcher(
                    project_path_of(record),
                    project_id=str(record.get("project_id", "")),
                    max_parallel=max(1, int(config.runtime.max_parallel_agents or 3)),
                    godot_executable=config.godot.executable,
                )
                self.state.interval_s = max(MIN_INTERVAL_S, float(config.runtime.dispatch_interval_s or 2.0))
            return self._dispatcher

    def refresh_dispatcher(self) -> None:
        """Rebuild the dispatcher, e.g. after the Godot path or parallelism changed."""
        with self._lock:
            self._dispatcher = None
        if self.state.status == "running":
            self.wake()

    # --- crash recovery --------------------------------------------------------------------

    def recover_in_flight(self) -> list[str]:
        """Release tasks stuck IN_PROGRESS by a previous process.

        A hard crash mid-task leaves the queue file claiming work is happening when nothing is
        running. The task is released back to PENDING with the error history intact - never
        deleted, per the zero-abandonment rule.
        """
        bus_ = self.bus()
        if bus_ is None:
            return []
        released: list[str] = []
        for task in bus_.all():
            if task.status is not Status.IN_PROGRESS:
                continue
            try:
                bus_.fail_attempt(
                    task.task_id,
                    "The studio closed while this task was running. It was put back in the queue "
                    "so no work is lost.",
                )
                bus_.transition(task.task_id, Status.PENDING, reason="recovered after restart", crash_recovered=True)
                released.append(task.task_id)
                memory.append_progress(
                    bus_.path,
                    f"{task.task_id} recovered after a restart; requeued",
                    agent="runtime",
                    task_id=task.task_id,
                    status="RECOVERED",
                )
            except Exception as exc:  # pragma: no cover - defensive
                log.info("Could not release %s: %s", task.task_id, exc)
        if released:
            bus.publish("tasks_recovered", {"task_ids": released})
        self._recovered = True
        return released

    # --- the loop ----------------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state.status == "running":
                return self.state.as_dict()
            if self.record is None:
                self.state.last_error = "No project is open. Create or open one first."
                return self.state.as_dict()
            if self._loop_thread is not None and self._loop_thread.is_alive():
                # A paused loop is still alive; just unblock it.
                self.state.status = "running"
                self._wake.set()
                bus.publish("run_state", self.state.as_dict())
                return self.state.as_dict()
            if not self._recovered:
                self.recover_in_flight()
            self._stop_event.clear()
            self._wake.clear()
            self.state.status = "running"
            self.state.started_at = time.time()
            self.state.last_error = ""
            self._loop_thread = threading.Thread(target=self._loop, name="pulseg-dispatch", daemon=True)
            self._loop_thread.start()
        bus.publish("run_state", self.state.as_dict())
        log.info("Dispatch loop started for %s", self.state.project_name)
        return self.state.as_dict()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self.state.status == "running":
                self.state.status = "paused"
        bus.publish("run_state", self.state.as_dict())
        return self.state.as_dict()

    def stop(self, *, wait: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.state.status in ("idle", "stopping") and not (
                self._loop_thread and self._loop_thread.is_alive()
            ):
                self.state.status = "idle"
                return self.state.as_dict()
            self.state.status = "stopping"
            self._stop_event.set()
            self._wake.set()
            thread = self._loop_thread
            self._loop_thread = None
        if thread is not None and wait:
            thread.join(timeout=15)
        with self._lock:
            self.state.status = "idle"
            self.state.started_at = 0.0
        bus.publish("run_state", self.state.as_dict())
        log.info("Dispatch loop stopped")
        return self.state.as_dict()

    def wake(self) -> None:
        """Nudge the loop to tick now (used when a task is added by hand or a key is saved)."""
        self._wake.set()

    def _loop(self) -> None:
        log.info("Dispatch loop running (interval %.2fs)", self.state.interval_s)
        while not self._stop_event.is_set():
            if self.state.status == "running":
                self.tick_once()
            else:
                # Paused: wait for a wake-up or a short poll, and stay responsive.
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            interval = max(MIN_INTERVAL_S, self.state.interval_s)
            self._wake.wait(timeout=interval)
            self._wake.clear()
        log.info("Dispatch loop exited")

    def tick_once(self) -> DispatchOutcome:
        """One dispatch pass. Runs in the loop thread; safe to call directly from tests."""
        dispatcher = self.dispatcher()
        if dispatcher is None:
            return DispatchOutcome(errors=["No active project."])
        try:
            outcome = dispatcher.tick()
        except Exception as exc:  # pragma: no cover - the loop must survive anything
            log.exception("Dispatch tick raised")
            outcome = DispatchOutcome(errors=[f"{type(exc).__name__}: {exc}"])
        with self._lock:
            self.state.ticks += 1
            self.state.last_tick_at = time.time()
            self.state.last_outcome = outcome.as_dict()
            if outcome.errors:
                self.state.last_error = outcome.errors[0]
        return outcome

    def as_dict(self) -> dict[str, Any]:
        # Whoever asks for the run state wants to know which project it is about, so make sure the
        # name is on it even if nothing else has touched the runtime since startup.
        self._stamp_project(self.record)
        return self.state.as_dict()


#: Process-wide runtime.
runtime = StudioRuntime()


def task_counts() -> dict[str, int]:
    bus_ = runtime.bus()
    return bus_.counts() if bus_ else {}


def active_task(task_id: str) -> Task | None:
    bus_ = runtime.bus()
    return bus_.get(task_id) if bus_ else None
