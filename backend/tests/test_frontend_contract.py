"""Every URL the dashboard calls must exist in the API, with the method it uses.

This test exists because of a real defect: the Setup Wizard called
`POST /api/settings/godot/detect` and the route is GET-only, so the API answered 405 and the button
did nothing at all - a silent failure in the first screen a new user ever sees. Nothing caught it.
The router tests exercised the route directly, the frontend tests did not exist yet, and the type
checker cannot see across an HTTP call.

So the two sides are compared directly here: the call sites are scraped out of `src/`, converted to
path patterns, and matched against the OpenAPI document the app itself publishes. A call to a route
that does not exist, or a verb the route does not accept, fails this test with the file and line.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "src"

#: `api.get<Thing>("/api/thing")` and the same with a template literal, in one pattern.
CALL = re.compile(
    r"""api\.(?P<method>get|post|put|patch|delete)\s*(?:<[^>]*>)?\s*\(\s*"""
    r"""(?P<quote>`|")(?P<path>[^`"]+)(?P=quote)""",
    re.VERBOSE,
)

def _interpolation_end(route: str, start: int) -> int:
    """Index just past the `}` that closes the interpolation opening at `start` (or end of string).

    Counting braces matters: `${sub ? `?sub=${x}` : ""}` nests a whole template literal inside the
    interpolation, and stopping at the first `}` leaves syntax behind that matches no route - which
    would quietly turn this whole test into a no-op.
    """
    depth = 0
    cursor = start + 1
    while cursor < len(route):
        if route[cursor] == "{":
            depth += 1
        elif route[cursor] == "}":
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return len(route)


def _without_query(route: str) -> str:
    """Drop the search string.

    A query string is built in one of two ways in this codebase: the `query({...})` helper, or an
    inline ternary that starts with `?sub=`. Both arrive as an interpolation, so anything with a `?`
    or a `query(` call in it ends the path - and everything before it is what has to match a route.
    """
    index = 0
    while True:
        start = route.find("${", index)
        if start < 0:
            break
        end = _interpolation_end(route, start)
        content = route[start + 2 : end]
        if "query(" in content or "?" in content:
            return route[:start].rstrip("?&")
        index = end
    return route.split("?")[0].rstrip("/")


def _placeholders(route: str) -> str:
    """Replace every `${...}` with a marker, using the same brace counting as above."""
    out: list[str] = []
    index = 0
    while index < len(route):
        if route.startswith("${", index):
            index = _interpolation_end(route, index)
            out.append("\x00")
        else:
            out.append(route[index])
            index += 1
    return "".join(out)


#: The API's own path parameters, as FastAPI writes them in the OpenAPI document.
API_PARAM = re.compile(r"\{[^}]*\}")


def _pattern(route: str) -> str:
    """Turn a path into a regex, treating `{param}` and `${expr}` as one segment each."""
    route = API_PARAM.sub("\x00", _placeholders(_without_query(route)))
    parts = [re.escape(part) for part in route.split("\x00")]
    return "^" + "[^/]+".join(parts) + "$"


def _calls() -> list[tuple[str, str, str]]:
    """Every (method, path, file:line) the frontend makes."""
    found: list[tuple[str, str, str]] = []
    sources = sorted(FRONTEND.rglob("*.ts")) + sorted(FRONTEND.rglob("*.tsx"))
    for path in sources:
        if "test" in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in CALL.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            route = match.group("path")
            # Template literals arrive with their `${}` already escaped by the toolchain; the raw
            # source has them, which is what the regex above expects.
            found.append((match.group("method").upper(), route, f"{path.relative_to(REPO_ROOT)}:{line}"))
    return found


@pytest.fixture(scope="module")
def api_index() -> dict[str, set[str]]:
    """method -> the path regexes the API accepts for it."""
    schema = create_app().openapi()
    index: dict[str, set[str]] = {}
    for route, methods in schema["paths"].items():
        for method in methods:
            index.setdefault(method.upper(), set()).add(_pattern(route))
    return index


def test_the_frontend_scanner_finds_the_calls_it_is_meant_to_find():
    """A scraper that silently finds nothing would make the test below pass for the wrong reason."""
    calls = _calls()
    assert len(calls) > 40, f"only found {len(calls)} frontend call sites"
    assert any(call[0] == "GET" and "/api/tasks/board" in call[1] for call in calls)


def _unmatched(calls: list[tuple[str, str, str]], api_index: dict[str, set[str]]) -> list[str]:
    """Every call whose shape the API does not serve, with a note when only the verb is wrong."""
    unmatched: list[str] = []
    for method, route, where in calls:
        pattern = _pattern(route)
        if pattern in api_index.get(method, set()):
            continue
        elsewhere = sorted(other for other, patterns in api_index.items() if pattern in patterns)
        hint = f" (the API serves it as {', '.join(elsewhere)})" if elsewhere else ""
        unmatched.append(f"{where}: {method} {route}{hint}")
    return unmatched


def test_every_frontend_call_matches_a_real_route(api_index):
    """Compare shapes, not spelling: both sides are normalised to the same pattern language first."""
    unmatched = _unmatched(_calls(), api_index)
    assert unmatched == [], "the dashboard calls routes the API does not serve:\n" + "\n".join(unmatched)


def test_the_contract_checker_rejects_a_wrong_verb_and_an_invented_route(api_index):
    """The checker above is only worth having if it fails on the bug that prompted it."""
    wrong_verb = _unmatched([("POST", "/api/settings/godot/detect", "made-up-file.ts:1")], api_index)
    assert wrong_verb, "a POST to a GET-only route must be reported"
    assert "GET" in wrong_verb[0], wrong_verb

    invented = _unmatched([("GET", "/api/nothing/like/this", "made-up-file.ts:2")], api_index)
    assert invented and "serves it as" not in invented[0], invented

    fine = _unmatched([("GET", "/api/tasks/board", "ok.ts:3")], api_index)
    assert fine == []
