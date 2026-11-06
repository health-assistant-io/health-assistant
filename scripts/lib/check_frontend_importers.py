#!/usr/bin/env python3
"""Zero-importer scan for ``frontend/src/components/**`` (plan 9 W6 / N1).

Builds the app's real module graph from the entry points (``main.tsx``,
``App.tsx``, ``sw.ts``, the vitest setup file, every ``*.test.*`` file) by
resolving relative import/export/dynamic-import specifiers, then reports every
component file nothing reaches. A component that only its own test imports
counts as dead: tests are roots, not evidence of product use.

Purely static and dependency-free — no knip/tsc install required in the gate.

Usage: python3 scripts/lib/check_frontend_importers.py [--root REPO]
Exit: 0 clean, 1 dead files found (paths on stdout, one per line).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from pathlib import Path

SPECIFIER = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]""",
    re.MULTILINE,
)
ENTRY_POINTS = ("main.tsx", "App.tsx", "sw.ts")
SETUP_ENTRY_POINTS = ("tests/setup.ts", "setupTests.ts")
SOURCE_SUFFIXES = (".ts", ".tsx")
RESOLVE_SUFFIXES = ("", ".ts", ".tsx", "/index.ts", "/index.tsx")


def is_source(path: Path) -> bool:
    return path.suffix in SOURCE_SUFFIXES and not path.name.endswith(".d.ts")


def resolve(base: Path, specifier: str) -> Path | None:
    if not specifier.startswith("."):
        return None
    for suffix in RESOLVE_SUFFIXES:
        candidate = (base / f"{specifier}{suffix}").resolve()
        if candidate.is_file():
            return candidate
    return None


def module_graph(src: Path) -> dict[Path, set[Path]]:
    graph: dict[Path, set[Path]] = {}
    for path in src.rglob("*"):
        if not path.is_file() or not is_source(path):
            continue
        resolved = path.resolve()
        edges: set[Path] = set()
        text = path.read_text(encoding="utf-8", errors="replace")
        for specifier in SPECIFIER.findall(text):
            target = resolve(path.parent, specifier)
            if target is not None:
                edges.add(target)
        graph[resolved] = edges
    return graph


def reachable(src: Path, graph: dict[Path, set[Path]]) -> set[Path]:
    roots = {**{name: None for name in ENTRY_POINTS}, **{name: None for name in SETUP_ENTRY_POINTS}}
    queue: deque[Path] = deque()
    for name in roots:
        path = (src / name).resolve()
        if path.is_file():
            queue.append(path)
    for path in src.rglob("*.test.*"):
        if path.is_file():
            queue.append(path.resolve())
    seen: set[Path] = set(queue)
    while queue:
        current = queue.popleft()
        for target in graph.get(current, ()):  # unresolved package specifiers never enter
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def dead_components(repo: Path) -> list[str]:
    src = (repo / "frontend" / "src").resolve()
    components = (src / "components").resolve()
    graph = module_graph(src)
    reached = reachable(src, graph)
    dead = [
        str(path.relative_to(repo))
        for path in graph
        if components in path.parents and path not in reached
    ]
    return sorted(dead)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", default=str(Path(__file__).resolve().parents[2]), help="repo root"
    )
    args = parser.parse_args(argv[1:])
    repo = Path(args.root).resolve()
    if not (repo / "frontend" / "src").is_dir():
        print(f"  ✗ no frontend/src under {repo}", file=sys.stderr)
        return 1
    dead = dead_components(repo)
    if dead:
        for path in dead:
            print(path)
        return 1
    print("  ✓ every frontend component has an importer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
