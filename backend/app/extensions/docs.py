"""Per-extension documentation helpers (plan 34 wave 7 — the
``extensionDocsTree`` form, guidelines/extension-docs.md §10).

Three small, shared-shape pieces every host needs at this seam:

- :func:`validate_tree` — the registration-time gate: the vendored
  schema PLUS the wall semantics the schema cannot express (unique
  ids, each closed category once, at-most-one ``index``, every file
  resolving beside the tree, no escapes). An invalid tree is a
  self-disable reason exactly like an invalid manifest.
- :func:`landing_file` — the landing rule: array order is render
  order; the first item of the first category lands, unless exactly
  one item carries ``index: true``.
- :func:`strip_raw_html` — the markdown law (§10.7): extension-doc
  pages are markdown only. Raw HTML outside code fences/spans is
  neutralized to escaped text — the SAME fence-aware shape as the
  health website's post-H3 posture (plan 32 §8 wave 6, H3: a committed
  ``<script>`` rendered into executable static HTML until ``rehypeRaw``
  went away); autolinks and full-line HTML comments stay (markdown,
  not HTML). Shared shape, not shared code — the site's gate lives in
  its build chain (scripts/check-docs-safety.mjs).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DOCS_POINTER = "docs/docs-tree.json"

_RAW_HTML = re.compile(r"<\s*/?[a-zA-Z!][^>\n]*>")
_AUTOLINK = re.compile(r"^<[a-zA-Z][a-zA-Z0-9+.\-]*(:|@)[^>\n]*>$")
_COMMENT_LINE = re.compile(r"^\s*<!--.*-->\s*$")
_FENCE = re.compile(r"^[\t ]*(`{3,}|~{3,})")


def _iter_items(tree: dict[str, Any]):
    for category in tree.get("categories", []):
        for item in category.get("items", []):
            yield category, item


def tree_semantics(tree: Any, docs_dir: Path | None = None) -> list[str]:
    """The wall-equivalent semantics beyond the schema (no filesystem
    checks unless ``docs_dir`` is given). Returns a list of problems
    (empty = clean)."""
    problems: list[str] = []
    if not isinstance(tree, dict) or "categories" not in tree:
        return ["tree is not an extensionDocsTree object (version + categories)"]
    ids: list[str] = []
    slugs: list[str] = []
    index_claims: list[str] = []
    for category in tree.get("categories", []):
        slugs.append(category.get("category"))
        for item in category.get("items", []):
            ids.append(item.get("id"))
            if item.get("index"):
                index_claims.append(str(item.get("id")))
            if docs_dir is not None and item.get("file"):
                resolved = (docs_dir / item["file"]).resolve()
                if not resolved.is_file():
                    problems.append(
                        f"item {item.get('id')!r}: file {item['file']!r} does not exist"
                        " beside the tree"
                    )
                elif docs_dir.resolve() not in resolved.parents:
                    problems.append(
                        f"item {item.get('id')!r}: file {item['file']!r} escapes the docs directory"
                    )
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate item ids {dupes}")
    dup_slugs = sorted({s for s in slugs if slugs.count(s) > 1})
    if dup_slugs:
        problems.append(
            f"duplicate category slugs {dup_slugs} (each closed category appears at most once)"
        )
    if len(index_claims) > 1:
        problems.append(
            f"index: true on more than one item {index_claims} "
            "(the landing rule allows exactly one)"
        )
    return problems


def validate_tree(
    tree: Any,
    docs_dir: Path,
    schema: dict[str, Any],
) -> str | None:
    """Full registration-time validation: the vendored docs-tree schema
    (root document — the schema's anyOf picks the form) plus the wall
    semantics. Returns None when valid, else a path-precise reason."""
    from app.extensions import contract

    err = contract.validate(schema, tree, schema)
    if err is not None:
        return err
    problems = tree_semantics(tree, docs_dir)
    if problems:
        return "; ".join(problems)
    return None


def load_tree(docs_dir: Path) -> dict[str, Any] | None:
    """Parse ``docs/docs-tree.json`` under ``docs_dir``; None when the
    bundle ships no tree."""
    path = docs_dir / "docs-tree.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def landing_file(tree: dict[str, Any]) -> str | None:
    """The landing rule: the FIRST item of the FIRST category, unless
    exactly one item carries ``index: true`` (schema + semantics have
    already pinned at-most-one)."""
    for _category, item in _iter_items(tree):
        if item.get("index"):
            return item.get("file")
    for _category, item in _iter_items(tree):
        return item.get("file")
    return None


def strip_raw_html(markdown: str) -> str:
    """The markdown law (§10.7): raw HTML outside code fences and
    inline code spans becomes escaped text — inert AND visible, the
    post-H3 shape. Autolinks (``<https://…>``, ``<user@host>``) and
    full-line HTML comments are markdown-adjacent exceptions, same as
    the website's gate."""
    out: list[str] = []
    in_fence = False
    for line in markdown.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        # protect inline code spans, then escape raw-HTML tags
        segments = re.split(r"(`[^`\n]*`)", line)
        rebuilt = ""
        for i, segment in enumerate(segments):
            if i % 2 == 1:  # inside backticks
                rebuilt += segment
                continue
            cursor = segment
            while True:
                match = _RAW_HTML.search(cursor)
                if not match:
                    rebuilt += cursor
                    break
                tag = match.group(0)
                if _AUTOLINK.match(tag.strip()) or _COMMENT_LINE.match(line):
                    rebuilt += cursor[: match.end()]
                else:
                    rebuilt += cursor[: match.start()]
                    rebuilt += tag.replace("<", "&lt;").replace(">", "&gt;")
                cursor = cursor[match.end() :]
        out.append(rebuilt)
    return "\n".join(out)
