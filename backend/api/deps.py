"""Shared request helpers and error mapping for the API layer.

Two rules the whole API follows, enforced here rather than repeated in every router:

* **Errors are actionable.** A failure returns a message a person can act on, not a traceback.
  Anything the user cannot fix says so and points at the log.
* **The project is resolved in one place.** ``require_project`` and ``require_task`` raise
  ``HTTPException`` with a consistent shape, so the frontend has one error component.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException, status

from ..core.models import KANBAN_LANES, Status, Task
from ..orchestration import projects
from ..orchestration.git_ops import GitRepo
from ..orchestration.task_bus import TaskBus, TransitionError
from ..runtime import runtime

log = logging.getLogger(__name__)


@dataclass
class ProjectContext:
    record: dict[str, Any]
    path: Path
    bus: TaskBus

    @property
    def project_id(self) -> str:
        return str(self.record.get("project_id", ""))

    @property
    def name(self) -> str:
        return str(self.record.get("name", ""))

    @property
    def mode(self) -> str:
        return str(self.record.get("mode", "fresh"))

    def git(self) -> GitRepo:
        return GitRepo(self.path)

    def settings(self) -> dict[str, Any]:
        return dict(self.record.get("settings", {}) or {})


def require_project() -> ProjectContext:
    """The active project, or a 409 that tells the user what to do about it."""
    record = runtime.record
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "no_active_project",
                "message": "No project is open. Create one or pick one from the project menu.",
                "action": "open_project",
            },
        )
    path = projects.project_path_of(record)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "error": "project_folder_missing",
                "message": (
                    f"The folder for '{record.get('name')}' is gone ({path}). Open the project "
                    "menu to locate it again or remove it from the list."
                ),
                "action": "relocate_project",
            },
        )
    bus = runtime.bus()
    if bus is None:  # pragma: no cover - require_project implies a record
        raise HTTPException(status_code=500, detail="The task queue for this project could not be opened.")
    return ProjectContext(record=record, path=path, bus=bus)


def require_task(task_id: str) -> Task:
    context = require_project()
    task = context.bus.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "unknown_task", "message": f"Task {task_id} does not exist in this project."},
        )
    return task


def require_dispatcher():
    dispatcher = runtime.dispatcher()
    if dispatcher is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "no_active_project",
                "message": "No project is open, so there is nothing to dispatch.",
                "action": "open_project",
            },
        )
    return dispatcher


def _error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": code, "message": message, **extra})


def transition_to_http(exc: Exception) -> HTTPException:
    """Map orchestration errors onto status codes with useful messages.

    Every mapping here exists because the raw exception would otherwise surface as a 500 (or,
    worse, as a 200 with a failed operation inside). The frontend shows ``message`` verbatim and
    switches on ``error``; ``action`` tells it which screen to open.
    """
    # Imported lazily: the API layer must not make the orchestration layer import the API.
    from ..mcp.godot_mcp_client import GodotError
    from ..orchestration.approvals import ApprovalError
    from ..providers.router import ChainExhausted

    if isinstance(exc, TransitionError):
        return _error(status.HTTP_409_CONFLICT, "invalid_transition", str(exc))
    if isinstance(exc, ApprovalError):
        # The human gate refused the decision - a conflict with the task's real state, never a
        # server error. "Already approved" and "not at the review gate yet" both land here.
        return _error(status.HTTP_409_CONFLICT, "approval_refused", str(exc))
    if isinstance(exc, ChainExhausted):
        provider_ids = [getattr(slot, "provider", "") for slot in getattr(exc, "chain", []) or []]
        return _error(
            status.HTTP_409_CONFLICT,
            "chain_exhausted",
            str(exc) or "Every provider in this agent's chain failed.",
            action="open_settings",
            providers=[p for p in provider_ids if p],
            needs_key=bool(getattr(exc, "needs_key_from_human", False)),
        )
    if isinstance(exc, GodotError):
        return _error(status.HTTP_409_CONFLICT, "godot_error", str(exc), action="open_settings")
    if isinstance(exc, TimeoutError):
        return _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "timed_out",
            f"Timed out: {exc}. Nothing was lost - the task can be retried.",
        )
    if isinstance(exc, FileNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, "not_found", str(exc))
    if isinstance(exc, PermissionError):
        return _error(status.HTTP_403_FORBIDDEN, "not_allowed", str(exc))
    if isinstance(exc, KeyError):
        return _error(status.HTTP_404_NOT_FOUND, "not_found", str(exc).strip("'\""))
    if isinstance(exc, ValueError):
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_request", str(exc))
    log.exception("Unhandled API error")
    return _error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "Something went wrong inside the studio. The full error is in the Logs view.",
    )


def guarded(action: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: turn orchestration exceptions into HTTP errors with a plain-language prefix.

    One subtlety worth the extra code: FastAPI reads a route's annotations to decide what is a
    body and what is a query parameter. A plain ``functools.wraps`` wrapper keeps the *string*
    annotations (this codebase uses ``from __future__ import annotations``) but resolves them
    against the wrapper's own module globals - where the request models do not exist - so the
    model silently becomes a query parameter and every POST 422s. Resolving the hints here, in
    the decorated function's own namespace, and handing FastAPI real types avoids that.
    """
    import functools
    import inspect
    import typing

    def wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
        try:
            hints = typing.get_type_hints(fn)
        except Exception:  # pragma: no cover - a hint we cannot resolve is FastAPI's problem later
            hints = {}

        @functools.wraps(fn)
        def inner(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except HTTPException:
                raise
            except Exception as exc:
                log.info("%s failed: %s", action, exc)
                raise transition_to_http(exc) from exc

        try:
            signature = inspect.signature(fn)
            inner.__signature__ = signature.replace(  # type: ignore[attr-defined]
                parameters=[
                    parameter.replace(annotation=hints.get(name, parameter.annotation))
                    for name, parameter in signature.parameters.items()
                ],
                return_annotation=hints.get("return", signature.return_annotation),
            )
            inner.__annotations__ = {**fn.__annotations__, **hints}
        except (TypeError, ValueError):  # pragma: no cover - builtins and the like
            pass
        return inner

    return wrapper


def board_payload(context: ProjectContext) -> dict[str, Any]:
    """The Kanban board.

    Lanes come from :data:`backend.core.models.KANBAN_LANES` so the UI, the API and the task bus
    cannot disagree about what a lane contains. Two lanes need a little interpretation:

    * **Approved** lives in ``completed_tasks.json``, not the open queue.
    * **Declined / Retrying** is a task that was rejected. The bus requeues it to PENDING
      immediately (a rejection is feedback, not an ending), so the lane shows open tasks that
      carry a rejection on their record rather than an empty column.
    """
    payload: dict[str, Any] = {"lanes": [], "counts": context.bus.counts()}
    approved = context.bus.completed()
    for lane in KANBAN_LANES:
        statuses = list(lane["statuses"])
        if Status.APPROVED in statuses:
            tasks = approved
        elif Status.REJECTED in statuses:
            tasks = [task for task in context.bus.open_tasks() if task.human_rejections > 0]
        else:
            tasks = [task for status in statuses for task in context.bus.by_status(status)]
        payload["lanes"].append(
            {
                "id": lane["id"],
                "title": lane["title"],
                "statuses": [status.value for status in statuses],
                "count": len(tasks),
                "tasks": [task.model_dump(mode="json") for task in tasks],
            }
        )
    payload["phase"] = context.bus.phase_progress()
    payload["review_count"] = len(context.bus.by_status(Status.NEEDS_HUMAN_REVIEW))
    return payload


def project_path_or_404(path_text: str = "") -> Path:
    """Resolve a path inside the project folder, refusing anything outside it."""
    context = require_project()
    if not path_text:
        return context.path
    from ..mcp.filesystem_mcp import resolve_in_project

    try:
        return resolve_in_project(context.path, path_text)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "outside_project",
                "message": f"{exc} Only files inside the project folder can be opened from the studio.",
            },
        ) from exc
