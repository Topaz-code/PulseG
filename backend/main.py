"""PulseG Studio's backend application.

Started by the Tauri shell as a sidecar on launch and killed when the window closes. It serves
four things:

1. the JSON API under ``/api``,
2. the event WebSocket at ``/ws/events``,
3. project media (screenshots, generated art) under ``/media``,
4. the built Godot web export under ``/preview/{project_id}`` - and, in a packaged build, the
   React app itself, so the desktop window loads from ``http://127.0.0.1:<port>/`` rather than
   from ``file://`` (which would break ES modules and fetch).

The app is import-safe: importing this module has no side effects, which is what lets the test
suite build a client against it without starting a server or touching the user's real files.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __app_name__, __version__
from .api.routers import ALL_ROUTERS
from .api.ws import router as ws_router
from .core import paths
from .core.config import load_config
from .core.events import bus, install_log_stream
from .core.index import index
from .orchestration import projects
from .runtime import runtime

log = logging.getLogger(__name__)

#: Where a packaged build puts the compiled frontend, relative to this file.
DIST_CANDIDATES = (
    Path(__file__).resolve().parents[1] / "dist",
    Path(__file__).resolve().parents[2] / "dist",
    Path(getattr(sys, "_MEIPASS", "")) / "dist" if getattr(sys, "_MEIPASS", "") else None,
)


def configure_logging(level: int = logging.INFO) -> None:
    """Console + rotating file logging, and the bridge that feeds the Logs view."""
    paths.ensure_studio_home()
    log_file = paths.studio_log_file()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        from logging.handlers import RotatingFileHandler

        handlers.append(RotatingFileHandler(log_file, maxBytes=4_000_000, backupCount=3, encoding="utf-8"))
    except OSError:  # pragma: no cover - an unwritable log folder must not stop the studio
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    install_log_stream(level=level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: logging, the event loop binding, crash recovery, and an index warm-up."""
    configure_logging()
    log.info("%s %s starting (pid %s)", __app_name__, __version__, os.getpid())
    bus.bind_loop(__import__("asyncio").get_running_loop())

    try:
        index.migrate() if hasattr(index, "migrate") else None
        count = index.rebuild(projects.list_projects())
        log.info("Index rebuilt from project files: %s", count)
    except Exception as exc:  # pragma: no cover - the index is a cache, never a blocker
        log.info("Index warm-up skipped: %s", exc)

    if load_config().first_run_complete:
        recovered = runtime.recover_in_flight()
        if recovered:
            log.info("Recovered %s task(s) left in flight by a previous run", len(recovered))

    bus.publish("backend_ready", {"version": __version__, "pid": os.getpid()})
    try:
        yield
    finally:
        log.info("Shutting down")
        runtime.stop(wait=True)
        bus.publish("backend_stopping", {"pid": os.getpid()})


def create_app() -> FastAPI:
    app = FastAPI(
        title=__app_name__,
        version=__version__,
        description=(
            "The local backend for PulseG Studio. It runs on this machine only, holds your API "
            "keys in an encrypted vault, and writes your projects as plain files you own."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        # The window loads from the sidecar's own origin; the dev server runs on Vite's port.
        # Anything else is refused, because a browser tab should not be able to drive a studio
        # that can run code.
        allow_origin_regex=r"^(http://(127\.0\.0\.1|localhost)(:\d+)?|tauri://localhost|https?://tauri\.localhost)$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in ALL_ROUTERS:
        app.include_router(router)
    app.include_router(ws_router)

    _mount_media(app)
    _mount_preview(app)
    _mount_frontend(app)
    _register_error_handlers(app)

    @app.get("/", include_in_schema=False)
    def root() -> Any:
        dist = _dist_dir()
        if dist is not None and (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return {
            "app": __app_name__,
            "version": __version__,
            "message": (
                "The backend is running. The interface is served from the built frontend when "
                "one is present; in development it runs on the Vite dev server."
            ),
            "docs": "/docs",
            "ws": "/ws/events",
        }

    return app


def _mount_media(app: FastAPI) -> None:
    """Screenshots and assets, scoped to the active project."""

    @app.get("/media/{path:path}", include_in_schema=False)
    def media(path: str) -> Any:
        record = runtime.record
        if record is None:
            return JSONResponse(status_code=409, content={"error": "no_active_project"})
        root = projects.project_path_of(record)
        from .mcp.filesystem_mcp import resolve_in_project

        try:
            target = resolve_in_project(root, path)
        except PermissionError:
            return JSONResponse(
                status_code=403,
                content={"error": "outside_project", "message": "Only project files are served."},
            )
        if not target.exists() or not target.is_file():
            return JSONResponse(status_code=404, content={"error": "missing", "path": path})
        return FileResponse(target)


def _mount_preview(app: FastAPI) -> None:
    """The Godot web export, served as a static site per project.

    Godot's web export needs its own origin-ish root because it fetches ``.wasm``, ``.pck`` and
    ``.js`` files by relative path. Serving the export folder at ``/preview/{project}/`` gives it
    that without a second server.
    """

    @app.get("/preview/{project_id}/{path:path}", include_in_schema=False)
    def preview_file(project_id: str, path: str) -> Any:
        record = projects.get_project(project_id)
        if record is None:
            return JSONResponse(status_code=404, content={"error": "unknown_project"})
        root = projects.web_build_path(record)
        target = (root / path).resolve() if path else (root / "index.html")
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return JSONResponse(status_code=403, content={"error": "outside_build"})
        if not target.exists() or not target.is_file():
            return JSONResponse(
                status_code=404,
                content={
                    "error": "no_web_build",
                    "message": (
                        "There is no web export for this project yet. It is generated after a "
                        "phase is approved, or on demand from the Live Preview pane."
                    ),
                },
            )
        # Godot's web export needs cross-origin isolation for threads in some builds; the
        # headers are harmless when it does not.
        headers = {
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp",
            "Cache-Control": "no-cache",
        }
        return FileResponse(target, headers=headers)


def _dist_dir() -> Path | None:
    for candidate in DIST_CANDIDATES:
        if candidate and candidate.exists() and (candidate / "index.html").exists():
            return candidate
    env = os.environ.get("PULSEG_DIST")
    if env and Path(env).exists():
        return Path(env)
    return None


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built React app, if there is one. SPA routes fall back to index.html."""
    dist = _dist_dir()
    if dist is None:
        log.info("No built frontend found; the API is available and /docs is open.")
        return
    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Any:
        # API and media routes are matched first by FastAPI's router order; anything left is a
        # client-side route and gets the shell.
        candidate = (dist / full_path).resolve()
        try:
            candidate.relative_to(dist.resolve())
        except ValueError:
            candidate = dist / "index.html"
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")

    log.info("Serving the built frontend from %s", dist)


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """One error envelope for the whole API: ``{error, message, ...extras}``.

        Routers raise ``HTTPException(detail={"error": ..., "message": ...})`` and the error
        mapper in ``api.deps`` builds the same shape, so the frontend needs exactly one error
        component. FastAPI's default ``{"detail": ...}`` only appears when a route raises a bare
        string detail, which is normalized here rather than left for the UI to guess at.
        """
        detail = exc.detail
        if isinstance(detail, dict):
            payload: dict[str, Any] = dict(detail)
            payload.setdefault("error", "request_failed")
            payload.setdefault("message", payload.get("error", "The request failed."))
        else:
            payload = {"error": "request_failed", "message": str(detail)}
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", []) if part != "body")
            problems.append(f"{location or 'request'}: {error.get('msg', 'invalid')}")
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_request",
                "message": "The request was not valid: " + "; ".join(problems[:5]),
                "problems": problems,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": (
                    "Something went wrong inside the studio. The full traceback is in the Logs "
                    "view and in the log file on disk."
                ),
                "path": request.url.path,
            },
        )


app = create_app()


def main() -> int:
    """Entry point for ``python -m backend.main`` and the packaged sidecar."""
    import uvicorn

    # Bind to loopback only. This process holds API keys and can run code; it must never be
    # reachable from the network.
    host = os.environ.get("PULSEG_HOST", "127.0.0.1")
    port = int(os.environ.get("PULSEG_PORT", "8787"))
    log_level = os.environ.get("PULSEG_LOG_LEVEL", "info")
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
