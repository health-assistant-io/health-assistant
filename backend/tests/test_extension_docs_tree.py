"""The integration-SDK doc-set tree wall (plan 34 wave 4): health's
chaptered set at ``docs/dev/sdk/`` validates against the vendored
docs-tree contract and passes the same semantic checks dev's wall 14
enforces — unique node ids, every referenced file present beside the
tree, declared chapters with matching node ids — plus the contract's own
fixture suite (valid accepted, invalid rejected) through the vendored
validator.

The product-level ``docs/docs-tree.json`` (guidelines/docs.md
audiences form) is deliberately NOT this contract's scope; a file named
``docs-tree.json`` is governed by its scope — and health's per-extension
builtin trees (``backend/app/extensions/builtin/*/docs/) stay the legacy
category-list form until the in-app reader learns the chaptered one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extensions import contract

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"
SDK_TREE = REPO_ROOT / "docs" / "dev" / "sdk" / "docs-tree.json"

DOCS_TREE_SCHEMA = json.loads((CONTRACTS_DIR / "docs-tree.schema.json").read_text(encoding="utf-8"))
DOCS_TREE_FIXTURES = json.loads(
    (CONTRACTS_DIR / "docs-tree.fixtures.json").read_text(encoding="utf-8")
)

EXPECTED_CHAPTERS = (
    "overview",
    "quickstart",
    "core-concepts",
    "installation-and-modes",
    "authoring-and-configuration",
    "examples-and-use-cases",
    "security-and-gotchas",
    "reference",
    "versioning-and-migration",
    "troubleshooting-and-faq",
)


def _walk_nodes(doc: dict) -> list[dict]:
    stack = list(doc.get("roots", []))
    nodes: list[dict] = []
    while stack:
        node = stack.pop(0)
        nodes.append(node)
        stack.extend(node.get("children", []))
    return nodes


# ------------------------------------------------- the vendored fixture wall


@pytest.mark.parametrize("case", DOCS_TREE_FIXTURES.get("valid", []), ids=lambda case: case["name"])
def test_docs_tree_fixtures_accept(case: dict) -> None:
    assert contract.validate(DOCS_TREE_SCHEMA, case["doc"], DOCS_TREE_SCHEMA) is None


@pytest.mark.parametrize(
    "case", DOCS_TREE_FIXTURES.get("invalid", []), ids=lambda case: case["name"]
)
def test_docs_tree_fixtures_reject(case: dict) -> None:
    err = contract.validate(DOCS_TREE_SCHEMA, case["doc"], DOCS_TREE_SCHEMA)
    assert err is not None, f"invalid case {case['name']} was accepted"


# ------------------------------------------------------- health's real tree


def test_sdk_docs_tree_validates_against_the_contract() -> None:
    doc = json.loads(SDK_TREE.read_text(encoding="utf-8"))
    err = contract.validate(DOCS_TREE_SCHEMA, doc, DOCS_TREE_SCHEMA)
    assert err is None, err


def test_sdk_docs_tree_declares_exactly_the_ten_chapters() -> None:
    doc = json.loads(SDK_TREE.read_text(encoding="utf-8"))
    assert tuple(doc["chapters"]) == EXPECTED_CHAPTERS


def test_sdk_docs_tree_unique_ids_files_exist_and_chapters_match_nodes() -> None:
    doc = json.loads(SDK_TREE.read_text(encoding="utf-8"))
    nodes = _walk_nodes(doc)
    ids = [node["id"] for node in nodes]

    dupes = sorted({node_id for node_id in ids if ids.count(node_id) > 1})
    assert not dupes, f"duplicate node ids {dupes}"

    for chapter in doc.get("chapters", []):
        assert chapter in ids, f"declared chapter {chapter!r} has no matching node id"

    for node in nodes:
        file_ref = node.get("file")
        if not file_ref:
            continue
        path = (SDK_TREE.parent / file_ref).resolve()
        assert path.is_file(), f"node {node['id']!r} references missing file {file_ref!r}"
        assert REPO_ROOT in path.parents, f"node {node['id']!r} escapes the repo: {file_ref!r}"


def test_sdk_docs_tree_audience_routing_matches_file_locations() -> None:
    """The audience tag is where the page lives: user nodes under
    docs/user/, dev nodes under the set's own directory."""
    doc = json.loads(SDK_TREE.read_text(encoding="utf-8"))
    for node in _walk_nodes(doc):
        file_ref = node.get("file")
        if not file_ref:
            continue
        resolved = (SDK_TREE.parent / file_ref).resolve()
        if node["audience"] == "user":
            assert resolved.parent == (REPO_ROOT / "docs" / "user").resolve(), (
                f"user node {node['id']!r} must live under docs/user/: {file_ref}"
            )
        else:
            assert resolved.parent == SDK_TREE.parent.resolve(), (
                f"dev node {node['id']!r} must live under docs/dev/sdk/: {file_ref}"
            )
