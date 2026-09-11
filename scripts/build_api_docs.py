#!/usr/bin/env python
"""Generate docs/API.md from the live FastAPI schema.

Why generate rather than hand-write: the frontend is written against these routes, and a
hand-written reference drifts the first time somebody renames a field. The app's own OpenAPI
document is the only description of the API that cannot be out of date.

    python scripts/build_api_docs.py

The script imports the app (no server needed) and writes docs/API.md. It also prints a count of
routes per prefix so an accidental duplicate or a shadowed route shows up in review.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUT = REPO_ROOT / "docs" / "API.md"

PREFIX_TITLES = {
    "/api/system": "System, setup and diagnostics",
    "/api/settings": "Settings",
    "/api/projects": "Projects and their files",
    "/api/tasks": "Task board and the human gate",
    "/api/agents": "Agents and model chains",
    "/api/providers": "Providers, keys and quota",
    "/api/planning": "Planning, intake and the GDD",
    "/api/assets": "Asset library and asset requests",
    "/api/knowledge": "Knowledge base and transcripts",
    "/api/logs": "Logs, run control and context stats",
    "/api/git": "Git history",
    "/api/design": "Design tokens and the taste audit",
    "/api/preview": "Live preview and the Godot web export",
    "/ws": "Live event stream",
}


def section_for(path: str) -> str:
    if path.startswith("/ws"):
        return "/ws"
    parts = path.split("/")
    return "/".join(parts[:3]) if len(parts) > 2 else path


def describe_schema(schema: dict, components: dict) -> str:
    if not schema:
        return "-"
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        model = components.get(name, {})
        fields = model.get("properties", {})
        required = set(model.get("required", []))
        parts = []
        for field, spec in fields.items():
            if "$ref" in spec:
                kind = spec["$ref"].split("/")[-1]
            elif spec.get("type") == "array":
                item = spec.get("items", {})
                inner = item.get("$ref", "").split("/")[-1] or item.get("type", "any")
                kind = f"{inner}[]"
            elif "anyOf" in spec:
                kind = "|".join(
                    option.get("type", option.get("$ref", "any").split("/")[-1])
                    for option in spec["anyOf"]
                )
            else:
                kind = spec.get("type", "any")
            parts.append(f"{field}: {kind}{'' if field in required else '?'}")
        return f"{name} {{{', '.join(parts)}}}" if parts else name
    return schema.get("type", "object")


def main() -> int:
    from backend.main import create_app

    app = create_app()
    schema = app.openapi()
    components = schema.get("components", {}).get("schemas", {})
    paths = schema["paths"]

    by_section: dict[str, list[tuple[str, str, dict]]] = {}
    for path, methods in paths.items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            by_section.setdefault(section_for(path), []).append((path, method.upper(), operation))

    lines = [
        "# PulseG Studio API",
        "",
        "Generated from the running application by `scripts/build_api_docs.py` - do not edit by",
        "hand. The same document is served at `/openapi.json`, and `/docs` renders it.",
        "",
        f"- Application: {schema['info']['title']} {schema['info']['version']}",
        f"- Routes: {sum(len(v) for v in by_section.values())}",
        "",
        "## Error shape",
        "",
        "Every failure - validation, guard, domain error, crash - returns the same JSON envelope:",
        "",
        "```json",
        '{"error": "no_active_project",',
        ' "message": "No project is open. Create one or pick one from the project menu.",',
        ' "action": "open_project"}',
        "```",
        "",
        "`error` is a stable machine code, `message` is written for a person, and `action` (when",
        "present) names the screen the UI should open.",
        "",
    ]

    for section in sorted(by_section, key=lambda key: list(PREFIX_TITLES).index(key) if key in PREFIX_TITLES else 99):
        routes = sorted(by_section[section])
        lines.append(f"## {PREFIX_TITLES.get(section, section)}")
        lines.append("")
        for path, method, operation in routes:
            summary = (operation.get("summary") or operation.get("description") or "").strip().split("\n")[0]
            lines.append(f"### `{method} {path}`")
            if summary:
                lines.append("")
                lines.append(summary)
            body = operation.get("requestBody")
            if body:
                content = body.get("content", {}).get("application/json", {})
                lines.append("")
                lines.append(f"- body: `{describe_schema(content.get('schema', {}), components)}`")
            params = [
                parameter
                for parameter in operation.get("parameters", [])
                if parameter.get("in") in {"query", "path"}
            ]
            if params:
                described = ", ".join(
                    f"{parameter['name']} ({parameter['in']}, {parameter.get('schema', {}).get('type', 'any')})"
                    for parameter in params
                )
                lines.append(f"- params: {described}")
            lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    counts = Counter(section_for(path) for path in paths)
    print(f"Wrote {OUT.relative_to(REPO_ROOT)} - {sum(counts.values())} paths")
    for section, count in sorted(counts.items()):
        print(f"  {section}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
