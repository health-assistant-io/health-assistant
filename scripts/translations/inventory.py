#!/usr/bin/env python3
"""Classify every catalogue key as static, server, dynamic or dead.

The family translation manager proves the dictionaries agree with each other.
It cannot tell whether a key is *reachable*: a key can be spelled correctly,
translated in every locale, and still never render (or render only through a
template the manager cannot see).

This is the repo-local reachability report. It reads the source instead of a
config because reachability is a property of this app, not of the family
contract.

    python3 scripts/translations/inventory.py            # report
    python3 scripts/translations/inventory.py --dead     # list dead keys
    python3 scripts/translations/inventory.py --check    # fail on NEW dead keys

``--check`` compares against ``inventory-baseline.json`` next to this file, so
cleanup is incremental: fix or delete a dead key, regenerate the baseline with
``--write-baseline``, and the gate keeps the rest from regressing.

Classification (first match wins):

* **static**  — a literal ``t('a.b')`` in TS/TSX
* **server**  — emitted by the backend: ``label_key``/``title_i18n_key``/
  ``description_key`` literals, ``setup.steps.*`` strings, and the
  ``events.{type,category}.<slug>.*`` families derived from the seed files
* **dynamic** — matches a ``t(`prefix${…}suffix`)`` shape from the source
* **dead**    — none of the above
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "frontend" / "src" / "locales"
SRC = ROOT / "frontend" / "src"
BACKEND = ROOT / "backend" / "app"
SEEDS = ROOT / "backend" / "data" / "seeds"
BASELINE = Path(__file__).resolve().with_name("inventory-baseline.json")

SKIP_DIRS = {"node_modules", "dist", "tests", "__tests__"}


def flatten(node, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else key))
    else:
        out[prefix] = str(node)
    return out


def source_files():
    for path in SRC.rglob("*"):
        if not path.suffix in (".ts", ".tsx"):
            continue
        if path.stem.endswith(".test") or path.suffix == ".d.ts":
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


STATIC = re.compile(r"""\bt\(\s*['"]([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)['"]""")
# Keys are also carried as data (`{ labelKey: 'admin.users' }`, nav tables,
# generated registries), so any literal that equals a catalogue key counts as
# referenced. Reachability is the question here, not call shape.
LITERAL = re.compile(r"""['"]([a-z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+)['"]""")
SHAPE = re.compile(r"""\bt\(\s*`([^`$]*)\$\{[^}]*\}([^`]*)`""")


def code_index() -> tuple[set[str], list[tuple[str, str]]]:
    static: set[str] = set()
    shapes: list[tuple[str, str]] = []
    for path in source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        static |= {m.group(1) for m in STATIC.finditer(text)}
        static |= {m.group(1) for m in LITERAL.finditer(text)}
        for m in SHAPE.finditer(text):
            shapes.append((m.group(1), m.group(2)))
    return static, list(dict.fromkeys(shapes))


SERVER_FIELD = re.compile(r"""(?:label_key|title_i18n_key|description_key)\s*=\s*(?:f)?['"]([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)['"]""")
SETUP_STEP = re.compile(r"""['"](setup\.steps\.[a-z0-9_.]+)['"]""")


def server_keys() -> set[str]:
    keys: set[str] = set()
    for path in BACKEND.rglob("*.py"):
        if "venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        keys |= {m.group(1) for m in SERVER_FIELD.finditer(text)}
        keys |= {m.group(1) for m in SETUP_STEP.finditer(text)}
    for seed_file, family in (
        ("clinical_event_types.json", "events.type"),
        ("concepts.json", "events.category"),
    ):
        path = SEEDS / seed_file
        if not path.is_file():
            continue
        items = json.loads(path.read_text(encoding="utf-8"))["items"]
        for item in items:
            if seed_file == "concepts.json" and "event_category" not in (item.get("kinds") or []):
                continue
            for field_name in ("name", "description"):
                keys.add(f"{family}.{item['slug']}.{field_name}")
    return keys


PLURAL = re.compile(r"_(zero|one|two|few|many|other)$")


def base_key(key: str) -> str:
    """`x_other` and `x_one` are the same catalogue entry as `x` for reachability."""
    return PLURAL.sub("", key)


def classify() -> tuple[dict[str, str], Counter]:
    source = flatten(json.loads((LOCALES / "en.json").read_text(encoding="utf-8")))
    static, shapes = code_index()
    server = server_keys()

    static_bases = {base_key(key) for key in static}
    server_bases = {base_key(key) for key in server}

    result: dict[str, str] = {}
    for key in source:
        base = base_key(key)
        if base in static_bases:
            result[key] = "static"
        elif base in server_bases:
            result[key] = "server"
        elif any(
            (key.startswith(p) and key.endswith(s) and len(key) > len(p) + len(s))
            or (base.startswith(p) and base.endswith(s) and len(base) > len(p) + len(s))
            for p, s in shapes
        ):
            result[key] = "dynamic"
        else:
            result[key] = "dead"
    return result, Counter(result.values())


def main(argv: list[str]) -> int:
    result, counts = classify()
    dead = sorted(key for key, kind in result.items() if kind == "dead")
    total = sum(counts.values())

    if "--dead" in argv:
        for key in dead:
            print(key)
        return 0

    if "--write-baseline" in argv:
        BASELINE.write_text(json.dumps(dead, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {len(dead)} dead key(s) to {BASELINE.name}")
        return 0

    if "--check" in argv:
        if not BASELINE.is_file():
            print(f"error: {BASELINE.name} missing — run --write-baseline once", file=sys.stderr)
            return 1
        known = set(json.loads(BASELINE.read_text(encoding="utf-8")))
        new = sorted(set(dead) - known)
        if new:
            print(f"❌ {len(new)} new unreachable key(s) (not in the baseline):")
            for key in new:
                print(f"   • {key}")
            return 1
        print(f"✅ no new unreachable keys ({len(dead)} known, {counts['static'] + counts['server'] + counts['dynamic']} reachable)")
        return 0

    print(f"catalogue: {total} keys")
    for kind in ("static", "server", "dynamic", "dead"):
        print(f"  {kind:<8} {counts[kind]:>5}  ({counts[kind] / total:.0%})")
    if dead:
        print(f"\n{len(dead)} unreachable key(s); run --dead to list them")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
