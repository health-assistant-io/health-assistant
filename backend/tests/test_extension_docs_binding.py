"""The per-extension docs binding (plan 34 wave 7 — the
``extensionDocsTree`` form, guidelines/extension-docs.md §10).

Covers the three seams the wave touched:

- the helpers (``app.extensions.docs``): the landing rule, the
  fence-aware raw-HTML neutralization (the markdown law, §10.7), and
  the wall-equivalent tree semantics;
- registration (``registry._load_manifests``): a manifest carrying the
  ``docs`` pointer must resolve to an existing, validating tree —
  an invalid tree self-disables the extension exactly like an invalid
  manifest (BOTH directions asserted: the new-seam law);
- the ``/{domain}/documentation`` endpoint: the landing rule picks the
  served page and raw HTML crosses neutralized;
- the shipped builtin trees + manifests: closed categories, pointers
  that resolve, webhook's honest pointer-free absence.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.extensions import contract
from app.extensions import docs as ext_docs
from app.extensions.registry import IntegrationRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"
BUILTIN_DIR = REPO_ROOT / "backend" / "app" / "extensions" / "builtin"

DOCS_TREE_SCHEMA = json.loads((CONTRACTS_DIR / "docs-tree.schema.json").read_text(encoding="utf-8"))

CLOSED_CATEGORIES = {
    "overview",
    "setup",
    "usage",
    "reference",
    "security",
    "changelog",
    "troubleshooting",
}


# ------------------------------------------------------------- the helpers


class TestStripRawHtml:
    def test_script_tags_become_inert_escaped_text(self):
        out = ext_docs.strip_raw_html("hello\n\n<script>alert(1)</script>\n\nbye")
        assert "<script>" not in out
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out

    def test_code_fences_are_exempt(self):
        md = "```\n<script>alert(1)</script>\n```\n"
        assert ext_docs.strip_raw_html(md) == md

    def test_inline_code_spans_are_exempt(self):
        md = "run `<script>` inline"
        assert ext_docs.strip_raw_html(md) == md

    def test_autolinks_and_comment_lines_survive(self):
        md = "<https://example.com>\n\n<!-- sync marker -->\n"
        assert ext_docs.strip_raw_html(md) == md

    def test_block_html_is_escaped_too(self):
        out = ext_docs.strip_raw_html("<details>\n<summary>x</summary>\n</details>")
        assert "<details>" not in out
        assert "&lt;details&gt;" in out


class TestLandingFile:
    def test_first_item_of_first_category_by_default(self):
        tree = {
            "version": 1,
            "categories": [
                {
                    "category": "overview",
                    "items": [
                        {"id": "a", "file": "overview.md", "title": "A", "description": "d"},
                        {"id": "b", "file": "extra.md", "title": "B", "description": "d"},
                    ],
                },
            ],
        }
        assert ext_docs.landing_file(tree) == "overview.md"

    def test_index_flag_overrides(self):
        tree = {
            "version": 1,
            "categories": [
                {
                    "category": "overview",
                    "items": [{"id": "a", "file": "overview.md", "title": "A", "description": "d"}],
                },
                {
                    "category": "usage",
                    "items": [
                        {
                            "id": "b",
                            "file": "tricks.md",
                            "title": "B",
                            "description": "d",
                            "index": True,
                        }
                    ],
                },
            ],
        }
        assert ext_docs.landing_file(tree) == "tricks.md"

    def test_empty_tree_lands_nowhere(self):
        assert ext_docs.landing_file({"version": 1, "categories": []}) is None


class TestTreeSemantics:
    def test_clean_tree_passes_with_files(self, tmp_path):
        (tmp_path / "overview.md").write_text("# Overview\n", encoding="utf-8")
        tree = {
            "version": 1,
            "categories": [
                {
                    "category": "overview",
                    "items": [
                        {
                            "id": "overview",
                            "file": "overview.md",
                            "title": "Overview",
                            "description": "d",
                        }
                    ],
                },
            ],
        }
        assert ext_docs.tree_semantics(tree, tmp_path) == []

    def test_free_form_category_is_rejected_by_the_schema_not_semantics(self, tmp_path):
        (tmp_path / "overview.md").write_text("# Overview\n", encoding="utf-8")
        tree = {
            "version": 1,
            "categories": [
                {
                    "category": "Getting Started",
                    "items": [
                        {
                            "id": "overview",
                            "file": "overview.md",
                            "title": "Overview",
                            "description": "d",
                        }
                    ],
                },
            ],
        }
        assert ext_docs.validate_tree(tree, tmp_path, DOCS_TREE_SCHEMA) is not None

    def test_duplicate_ids_categories_and_double_index_fail(self, tmp_path):
        for name in ("a.md", "b.md", "c.md", "d.md"):
            (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
        tree = {
            "version": 1,
            "categories": [
                {
                    "category": "overview",
                    "items": [
                        {
                            "id": "dup",
                            "file": "a.md",
                            "title": "A",
                            "description": "d",
                            "index": True,
                        }
                    ],
                },
                {
                    "category": "overview",
                    "items": [
                        {
                            "id": "dup",
                            "file": "b.md",
                            "title": "B",
                            "description": "d",
                            "index": True,
                        }
                    ],
                },
            ],
        }
        problems = ext_docs.tree_semantics(tree, tmp_path)
        assert any("duplicate item ids" in p for p in problems)
        assert any("duplicate category slugs" in p for p in problems)
        assert any("more than one item" in p for p in problems)

    def test_missing_and_escaping_files_fail(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (tmp_path / "secret.md").write_text("# Secret\n", encoding="utf-8")  # outside docs/
        tree = {
            "version": 1,
            "categories": [
                {
                    "category": "overview",
                    "items": [
                        {"id": "gone", "file": "gone.md", "title": "Gone", "description": "d"},
                        {
                            "id": "escape",
                            "file": "../secret.md",
                            "title": "Esc",
                            "description": "d",
                        },
                    ],
                },
            ],
        }
        problems = ext_docs.tree_semantics(tree, docs)
        assert any("does not exist" in p for p in problems)
        assert any("escapes the docs directory" in p for p in problems)


# ------------------------------------------------- registration (both ways)


def _bundle(
    tmp_path: Path, name: str, *, tree: dict | None, pointer: bool, page_text: str = "# Overview\n"
):
    ext = tmp_path / name
    (ext / "docs").mkdir(parents=True, exist_ok=True)
    (ext / "docs" / "overview.md").write_text(page_text, encoding="utf-8")
    if tree is not None:
        (ext / "docs" / "docs-tree.json").write_text(json.dumps(tree), encoding="utf-8")
    envelope = {
        "apiVersion": "extension/v1",
        "kindVersion": 1,
        "kind": "bridge",
        "id": name.replace("_", "-"),
        "version": "1.0.0",
        "capabilities": ["inbound-webhook"],
        "author": "Test",
        "license": "Apache-2.0",
        "i18n": {"defaultLocale": "en", "locales": {"en": {"title": "T"}}},
    }
    if pointer:
        envelope["docs"] = ext_docs.DOCS_POINTER
    (ext / "manifest.json").write_text(json.dumps(envelope), encoding="utf-8")
    return ext


GOOD_TREE = {
    "version": 1,
    "categories": [
        {
            "category": "overview",
            "items": [
                {
                    "id": "overview",
                    "file": "overview.md",
                    "title": "Overview",
                    "description": "The landing page.",
                    "audience": "user",
                }
            ],
        },
    ],
}


def test_registry_accepts_pointer_with_valid_tree(tmp_path):
    _bundle(tmp_path, "good_docs", tree=GOOD_TREE, pointer=True)
    registry = IntegrationRegistry()
    registry._base_path = str(tmp_path)
    registry._load_manifests()
    manifests = registry.get_all_manifests()
    assert len(manifests) == 1 and manifests[0]["id"] == "good-docs"


def test_registry_self_disables_on_invalid_docs_tree(tmp_path):
    """The red-check: a manifest pointing at a tree that does not
    validate (free-form category — the closed vocabulary is law) never
    enters the registry. Same self-disable path as an invalid manifest."""
    bad_tree = {
        "version": 1,
        "categories": [
            {
                "category": "Getting Started",
                "items": [
                    {
                        "id": "overview",
                        "file": "overview.md",
                        "title": "Overview",
                        "description": "d",
                    }
                ],
            },
        ],
    }
    _bundle(tmp_path, "bad_docs", tree=bad_tree, pointer=True)
    registry = IntegrationRegistry()
    registry._base_path = str(tmp_path)
    registry._load_manifests()
    assert registry.get_all_manifests() == []
    assert registry.get_provider("bad_docs") is None


def test_registry_self_disables_on_missing_tree(tmp_path):
    _bundle(tmp_path, "ghost_docs", tree=None, pointer=True)
    registry = IntegrationRegistry()
    registry._base_path = str(tmp_path)
    registry._load_manifests()
    assert registry.get_all_manifests() == []


def test_registry_allows_absent_pointer(tmp_path):
    """No pointer, no docs promise: the extension registers (docs are
    optional — honest absence, like the webhook builtin)."""
    _bundle(tmp_path, "no_docs", tree=None, pointer=False)
    registry = IntegrationRegistry()
    registry._base_path = str(tmp_path)
    registry._load_manifests()
    assert len(registry.get_all_manifests()) == 1


# ---------------------------------------------------------------- endpoint


@pytest.mark.asyncio
async def test_documentation_endpoint_applies_landing_rule_and_strips_html(monkeypatch, tmp_path):
    from app.api.v1.endpoints.integrations import get_integration_documentation
    from app.extensions import registry as registry_module

    ext = tmp_path / "demo_ext"
    (ext / "docs").mkdir(parents=True)
    (ext / "docs" / "overview.md").write_text(
        "# Overview\n\n<script>alert(1)</script>\n", encoding="utf-8"
    )
    (ext / "docs" / "hacking.md").write_text("# Hacking\n", encoding="utf-8")
    (ext / "docs" / "docs-tree.json").write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [
                    {
                        "category": "overview",
                        "items": [
                            {
                                "id": "overview",
                                "file": "overview.md",
                                "title": "Overview",
                                "description": "d",
                            }
                        ],
                    },
                    {
                        "category": "usage",
                        "items": [
                            {
                                "id": "hacking",
                                "file": "hacking.md",
                                "title": "Hacking",
                                "description": "d",
                                "audience": "dev",
                                "index": True,
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    fake = SimpleNamespace(
        _manifests={"demo_ext": {"id": "demo-ext"}},
        get_all_manifests=lambda: [{"id": "demo-ext"}],
        base_dir=lambda _domain: str(ext),
    )
    monkeypatch.setattr(registry_module, "integration_registry", fake)

    result = await get_integration_documentation(
        "demo_ext", None, current_user=SimpleNamespace(sub="u")
    )
    # landing rule: the index-flagged page, not the first
    assert result["markdown"].startswith("# Hacking")
    # tree crosses in the new shape
    assert result["tree"]["version"] == 1
    assert result["tree"]["categories"][0]["category"] == "overview"

    result = await get_integration_documentation(
        "demo_ext", "overview.md", current_user=SimpleNamespace(sub="u")
    )
    # markdown law: the script tag is neutralized (escaped, inert)
    assert "<script>" not in result["markdown"]
    assert "&lt;script&gt;" in result["markdown"]


# ------------------------------------------------------- the shipped trees


def test_builtin_trees_validate_the_extension_docs_form():
    trees = sorted(BUILTIN_DIR.glob("*/docs/docs-tree.json"))
    assert len(trees) == 4, "the four documented builtins (webhook ships no tree)"
    for tp in trees:
        doc = json.loads(tp.read_text(encoding="utf-8"))
        err = contract.validate(DOCS_TREE_SCHEMA, doc, DOCS_TREE_SCHEMA)
        assert err is None, f"{tp.parent.parent.name}: {err}"
        problems = ext_docs.tree_semantics(doc, tp.parent)
        assert problems == [], f"{tp.parent.parent.name}: {problems}"
        slugs = [c["category"] for c in doc["categories"]]
        assert set(slugs) <= CLOSED_CATEGORIES, f"{tp.parent.parent.name}: {slugs}"
        # the i18n convention when present: manifest-shaped
        if "i18n" in doc:
            assert doc["i18n"]["defaultLocale"] == "en"
            assert "en" in doc["i18n"]["locales"]


def test_builtin_manifests_pointer_iff_tree():
    for mp in sorted(BUILTIN_DIR.glob("*/manifest.json")):
        manifest = json.loads(mp.read_text(encoding="utf-8"))
        tree = (mp.parent / "docs" / "docs-tree.json").is_file()
        if mp.parent.name == "webhook":
            assert "docs" not in manifest and not tree, (
                "webhook ships no docs pointer (README-only, honest absence)"
            )
        else:
            assert manifest.get("docs") == ext_docs.DOCS_POINTER, mp.parent.name
            assert tree, f"{mp.parent.name}: pointer without a tree"
