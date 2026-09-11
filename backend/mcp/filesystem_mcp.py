"""Filesystem access, scoped to one project folder.

Every agent that writes files goes through here. That is deliberate: the MCP filesystem
server is configured with the project folder as its only root, and this module enforces the
same boundary in Python so a path from a model can never escape the project even if the MCP
server is not running.

Rules enforced:
* every path is resolved and must sit inside the project root;
* writes are atomic;
* binary/large files are refused by extension and size;
* ``.git`` is never written to directly.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, Sequence

from ..core.atomic import atomic_write_bytes, atomic_write_text, read_text

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 4 * 1024 * 1024  # 4 MB: generous for code/text, refuses accidental binaries
TEXT_SUFFIXES = {
    ".gd", ".tscn", ".tres", ".godot", ".cfg", ".json", ".md", ".txt", ".yaml", ".yml",
    ".tres", ".shader", ".gdshader", ".csv", ".xml", ".svg", ".import", ".gitignore",
}
FORBIDDEN_PARTS = {".git", ".godot"}

FILE_BLOCK_RE = re.compile(r"```file:\s*([^\n]+?)\s*\n(.*?)```", re.DOTALL)
PLAIN_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+.-]*)\n(.*?)```", re.DOTALL)
#: Paths that look like a filename inside a plain fenced block header, e.g. ```gdscript src/x.gd
HEADER_PATH_RE = re.compile(r"^\s*(?:[\w+-]+\s+)?([\w./\\-]+\.[A-Za-z0-9]{1,8})\s*$")


class PathOutsideProject(PermissionError):
    """Raised when a model asks to write outside the project folder."""


class UnsupportedFileType(ValueError):
    pass


def resolve_in_project(project_path: Path, relative: str) -> Path:
    """Resolve ``relative`` inside ``project_path`` or raise. Blocks traversal and symlinks."""
    if not relative or relative.strip() in ("", ".", "/"):
        raise PathOutsideProject("Empty path is not a valid target.")
    cleaned = relative.strip().strip('"').strip("'").replace("\\", "/")
    # Models sometimes emit an absolute path or a res:// path; normalise both.
    cleaned = re.sub(r"^res://", "", cleaned)
    cleaned = re.sub(r"^/?godot_project/\.\./", "", cleaned)
    candidate = Path(cleaned)
    if candidate.is_absolute():
        # Absolute paths are accepted only when they already point inside the project.
        resolved = candidate.resolve()
    else:
        resolved = (project_path / candidate).resolve()
    root = project_path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathOutsideProject(
            f"Refusing to write outside the project folder: {relative!r} resolves to "
            f"{resolved}, which is not inside {root}."
        ) from exc
    if any(part in FORBIDDEN_PARTS for part in resolved.relative_to(root).parts):
        raise PathOutsideProject(
            f"Refusing to write into {'.git' if '.git' in resolved.parts else '.godot'} - "
            "that folder is managed by Git/Godot."
        )
    return resolved


def read_project_file(project_path: Path, relative: str, limit: int = 200_000) -> str:
    target = resolve_in_project(project_path, relative)
    return read_text(target)[:limit]


def write_project_file(
    project_path: Path, relative: str, content: str, *, require_text: bool = True
) -> Path:
    target = resolve_in_project(project_path, relative)
    if require_text and target.suffix.lower() not in TEXT_SUFFIXES and not target.name.startswith("."):
        raise UnsupportedFileType(
            f"{target.suffix or 'no extension'} is not a text format this agent may write. "
            "Use the image or audio tools for binary assets."
        )
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise UnsupportedFileType(
            f"{relative} is {len(encoded) // 1024} KB, over the {MAX_FILE_BYTES // 1024} KB "
            "limit for a single agent write. Split it into smaller files."
        )
    atomic_write_text(target, content)
    log.info("agent wrote %s (%d bytes)", relative, len(encoded))
    return target


def write_binary(project_path: Path, relative: str, data: bytes) -> Path:
    target = resolve_in_project(project_path, relative)
    if len(data) > MAX_FILE_BYTES * 4:
        raise UnsupportedFileType(f"Refusing to write {len(data) // 1024} KB of binary data.")
    atomic_write_bytes(target, data)
    return target


def list_project_files(project_path: Path, pattern: str = "**/*", limit: int = 2000) -> list[str]:
    root = project_path.resolve()
    out: list[str] = []
    for path in sorted(root.glob(pattern)):
        if path.is_file() and not any(part in FORBIDDEN_PARTS for part in path.parts):
            out.append(str(path.relative_to(root)))
        if len(out) >= limit:
            break
    return out


def extract_file_blocks(response_text: str) -> list[tuple[str, str]]:
    """Pull ``(path, contents)`` out of an agent response.

    Handles the specified ``file:`` form first, then plain fenced blocks whose info string
    is a real path, then a preceding "path:" line, because real models drift between
    formats and the payload should not be thrown away over formatting.
    """
    blocks: list[tuple[str, str]] = []
    for match in FILE_BLOCK_RE.finditer(response_text or ""):
        blocks.append((match.group(1).strip(), match.group(2)))
    if blocks:
        return blocks

    for match in PLAIN_BLOCK_RE.finditer(response_text or ""):
        info = (match.group(1) or "").strip()
        body = match.group(2)
        candidate = ""
        header_match = HEADER_PATH_RE.match(info)
        if header_match and "/" in info:
            candidate = header_match.group(1)
        if not candidate:
            # Look back one line for "path: x" or "**x.gd**".
            start = match.start()
            preceding = (response_text or "")[:start].rstrip().splitlines()[-2:]
            for line in reversed(preceding):
                found = re.search(r"(?:path|file)\s*[:=]\s*([\w./\\-]+\.[A-Za-z0-9]{1,8})", line, re.I)
                if found:
                    candidate = found.group(1)
                    break
                found = re.search(r"\*\*([\w./\\-]+\.[A-Za-z0-9]{1,8})\*\*", line)
                if found:
                    candidate = found.group(1)
                    break
        if candidate and _looks_like_engine_file(candidate):
            blocks.append((candidate, body))
    return blocks


def _looks_like_engine_file(path: str) -> bool:
    return Path(path).suffix.lower() in TEXT_SUFFIXES


def write_agent_files(
    project_path: Path, response_text: str, *, allowed: Sequence[str] | None = None
) -> tuple[list[str], list[str]]:
    """Write every fenced file block. Returns ``(written, problems)``.

    ``allowed`` (the task's file claims) is advisory: a file outside the claims is still
    written if it is inside the project, but it is reported so the Auditor can see the scope
    creep. Paths outside the project are refused outright.
    """
    written: list[str] = []
    problems: list[str] = []
    blocks = extract_file_blocks(response_text)
    if not blocks:
        problems.append(
            "No writeable file blocks found in the response. The task expected files, so "
            "nothing was written to disk."
        )
        return written, problems
    allowed_set = {item.split("#")[0].strip() for item in (allowed or [])}
    for path, content in blocks:
        try:
            target = write_project_file(project_path, path, content)
        except (PathOutsideProject, UnsupportedFileType) as exc:
            problems.append(str(exc))
            continue
        relative = str(target.relative_to(project_path.resolve()))
        written.append(relative)
        if allowed_set and relative not in allowed_set:
            problems.append(
                f"{relative} was written but was not in this task's expected outputs "
                f"({', '.join(sorted(allowed_set)) or 'none declared'}) - review for scope creep."
            )
    return written, problems


def summarise_written(written: Iterable[str]) -> str:
    items = list(written)
    if not items:
        return "no files written"
    return f"{len(items)} file(s): " + ", ".join(items[:6]) + (" ..." if len(items) > 6 else "")
