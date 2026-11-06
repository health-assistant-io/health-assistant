#!/usr/bin/env python3
"""Documented counts drift gate — the README/API.md numbers are facts.

Derives the real numbers from the source tree and checks every doc that states
one:

* FHIR facade resource types  ← ``app/facade/registry.py`` (``ResourceEntry``
  registrations) — README's "exposes **N resources** (…)" paragraph and
  ``docs/API.md``'s "N registered resource types" list.
* router modules + HTTP/WS handlers ← ``app/api/v1/endpoints/*.py`` —
  ``docs/API.md``'s "N HTTP/WS handlers across M router modules" line.

``backend/tests/test_docs_counts.py`` asserts the same ``check()`` result, so a
code change without the doc update fails both the gate and the suite.

Usage: python3 backend/scripts/check_docs_counts.py [repo-root]
Exit: 0 clean, 1 drift.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

HANDLER_DECORATOR = re.compile(
    r"^\s*@router\.(get|post|put|patch|delete|websocket|api_route)\b", re.MULTILINE
)
README_COUNT = re.compile(r"exposes \*\*(\d+) resources\*\* \(([^)]+)\)")
API_TYPES_MARKER = re.compile(r"\*\*(\d+) registered resource types:\*\*")
API_ROUTERS = re.compile(r"(\d+) HTTP/WS handlers across (\d+) router modules")


def fhir_resource_types(repo: Path) -> list[str]:
    tree = ast.parse(
        (repo / "backend" / "app" / "facade" / "registry.py").read_text(encoding="utf-8")
    )
    types: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ResourceEntry":
            for keyword in node.keywords:
                if keyword.arg == "resource_type" and isinstance(keyword.value, ast.Constant):
                    types.append(str(keyword.value.value))
    return types


def router_modules(repo: Path) -> list[Path]:
    endpoints = repo / "backend" / "app" / "api" / "v1" / "endpoints"
    return [
        path
        for path in sorted(endpoints.glob("*.py"))
        if path.name != "__init__.py" and "APIRouter(" in path.read_text(encoding="utf-8")
    ]


def handler_count(repo: Path) -> int:
    return sum(
        len(HANDLER_DECORATOR.findall(path.read_text(encoding="utf-8")))
        for path in router_modules(repo)
    )


def check(repo: Path) -> list[str]:
    """Every drift problem, as one human-readable line each."""
    problems: list[str] = []
    registry = fhir_resource_types(repo)

    if len(registry) != len(set(registry)):
        problems.append("facade registry registers a duplicate resource type")

    readme = (repo / "README.md").read_text(encoding="utf-8")
    readme_match = README_COUNT.search(readme)
    if not readme_match:
        problems.append("README no longer states the FHIR resource count")
    else:
        stated = int(readme_match.group(1))
        listed = [name.strip() for name in readme_match.group(2).split(",")]
        if stated != len(registry):
            problems.append(
                f"README says {stated} FHIR resources; the registry has {len(registry)}"
            )
        missing = sorted(set(registry) - set(listed))
        extra = sorted(set(listed) - set(registry))
        if missing or extra:
            problems.append(
                f"README resource list differs from the registry (missing={missing} extra={extra})"
            )

    api_doc = (repo / "docs" / "API.md").read_text(encoding="utf-8")
    types_match = API_TYPES_MARKER.search(api_doc)
    if not types_match:
        problems.append("docs/API.md no longer lists the registered resource types")
    else:
        if int(types_match.group(1)) != len(registry):
            problems.append(
                f"docs/API.md says {types_match.group(1)} resource types; "
                f"the registry has {len(registry)}"
            )
        section = api_doc[types_match.end() :].split("\n\n", 1)[0]
        for resource in registry:
            if not re.search(rf"\b{resource}\b", section):
                problems.append(f"docs/API.md's resource list omits {resource}")

    modules, handlers = len(router_modules(repo)), handler_count(repo)
    routers_match = API_ROUTERS.search(api_doc)
    if not routers_match:
        problems.append("docs/API.md no longer states the router/handler count")
    elif (int(routers_match.group(1)), int(routers_match.group(2))) != (handlers, modules):
        problems.append(
            f"docs/API.md says {routers_match.group(1)} handlers across "
            f"{routers_match.group(2)} router modules; the tree has {handlers} across {modules}"
        )

    return problems


def main(argv: list[str]) -> int:
    repo = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[2]
    problems = check(repo)
    if problems:
        for problem in problems:
            print(f"  ✗ {problem}", file=sys.stderr)
        print("doc counts: FAIL", file=sys.stderr)
        return 1
    print(
        f"doc counts: OK ({len(fhir_resource_types(repo))} FHIR resources, "
        f"{handler_count(repo)} handlers, {len(router_modules(repo))} router modules)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
