"""Atomic file writes and cross-platform advisory file locks.

The task bus is file-based by design (see PROCESS.md rule 1), and several agents can
write at the same moment, so every write in the orchestration layer must be atomic and
every read-modify-write cycle must be serialised. ``atomic_write_json`` plus
``file_lock`` cover both needs without pulling in a database or a message broker.
"""
from __future__ import annotations

import errno
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import orjson

LOCK_SUFFIX = ".lock"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + fsync + rename).

    A crash mid-write leaves either the old or the new file, never a truncated one.
    """
    _ensure_parent(path)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "nt" and path.exists():  # Windows rename cannot clobber
            os.replace(tmp, path)
        else:
            os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, payload: Any, *, indent: bool = True) -> None:
    """Serialize with orjson (fast, handles datetime/Path via default=str)."""
    option = orjson.OPT_INDENT_2 if indent else 0
    atomic_write_bytes(
        path,
        orjson.dumps(
            payload,
            option=option | orjson.OPT_SORT_KEYS,
            default=_json_default,
        ),
    )


def append_text(path: Path, text: str) -> None:
    """Append-only writer used by ``memory/progress.md`` (never rewritten in place)."""
    _ensure_parent(path)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def read_json(path: Path, default: Any = None) -> Any:
    """Tolerant JSON read. Returns ``default`` when the file is absent or mid-write."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return default
    if not raw.strip():
        return default
    try:
        return orjson.loads(raw)
    except orjson.JSONDecodeError:
        # A previous process may have been killed between mkstemp and rename on a
        # filesystem without atomic rename. Retry once after a short pause.
        time.sleep(0.05)
        try:
            return orjson.loads(path.read_bytes())
        except Exception:
            return default


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):  # Enum
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return str(value)


# --- Locking -------------------------------------------------------------------------


@contextmanager
def file_lock(path: Path, timeout: float = 20.0, poll: float = 0.05) -> Iterator[None]:
    """Advisory exclusive lock built on ``O_CREAT|O_EXCL`` of ``<path>.lock``.

    Works identically on Windows and POSIX, which matters because the same code runs in
    the packaged Windows app and in CI. Stale locks (older than 60s, e.g. after a hard
    kill) are reclaimed so the pipeline can never dead-lock permanently.
    """
    lock_path = path.with_name(path.name + LOCK_SUFFIX)
    _ensure_parent(lock_path)
    deadline = time.monotonic() + timeout
    handle: int | None = None
    while True:
        try:
            handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, f"{os.getpid()} {time.time():.3f}".encode())
            break
        except FileExistsError:
            if _lock_is_stale(lock_path):
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out after {timeout}s waiting for lock {lock_path}. "
                    "Another agent may be writing this file; retry the task."
                )
            time.sleep(poll)
        except OSError as exc:  # pragma: no cover - permissions/FS oddities
            if exc.errno in (errno.EACCES, errno.EPERM):
                # Cannot create the lock file (read-only share). Degrade to no-op
                # rather than failing the task; the atomic rename still prevents
                # torn writes and the dispatcher's file locks prevent lost updates.
                yield
                return
            raise
    try:
        yield
    finally:
        if handle is not None:
            os.close(handle)
        lock_path.unlink(missing_ok=True)


def _lock_is_stale(lock_path: Path, max_age: float = 60.0) -> bool:
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return False
    return age > max_age


@contextmanager
def locked_json(path: Path, default: Any = None) -> Iterator[dict]:
    """Read-modify-write helper: yields the parsed payload, writes it back on exit.

    Example::

        with locked_json(queue_path, default=[]) as queue:
            queue.append(new_task)
    """
    with file_lock(path):
        payload = read_json(path, default)
        if payload is None and default is not None:
            payload = default
        yield payload
        atomic_write_json(path, payload)


def write_text_if_changed(path: Path, text: str) -> bool:
    """Write only when content differs. Returns True when a write happened."""
    existing = read_text(path)
    if existing == text:
        return False
    atomic_write_text(path, text)
    return True


def slurp_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    """Read a JSON-lines file, optionally only the last ``limit`` records."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
