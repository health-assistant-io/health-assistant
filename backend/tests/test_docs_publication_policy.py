"""Docs publication policy gate — public surfaces stay publication-clean.

2026-10-08 policy: ``docs/`` + ``README.md`` + ``AGENTS.md`` are published
(repo + website nav via ``docs-tree.json``). They carry product/developer
documentation only — no internal-workspace paths, family-governance
citations, internal plan/audit references, or machine/personal identifiers.
Plans, decisions, and maintainer instructions live in the local development
workspace, never in this repository.

Enforced invariants:
- banned-pattern scan over every public surface and the unreleased
  CHANGELOG slice (historical changelog entries are append-only and exempt)
- moved-out docs never reappear under docs/
- docs-tree.json references only existing files and no moved-out doc, and
  every docs/*.md is either in the nav or on the explicit repo-only
  allowlist — nothing accumulates silently
"""

import json
import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
DOCS_DIR = REPO_ROOT / "docs"
DOCS_TREE = DOCS_DIR / "docs-tree.json"

BANNED_PATTERNS: tuple[str, ...] = (
    r"dev/(plans|projects|audits|guidelines|decisions)",
    r"ADR-\d{4}",
    r"\(plan \d+",
    r"plan \d{1,2} [A-Z]\d",
    r"frozen \d{4}-\d{2}-\d{2}",
    r"identity-auth",
    r"ai-features",
    r"family-uniform|family-wide|family dev repo|family convergence",
    r"family-standard|family chat slot|family vocabulary|family min-10",
    r"`(?:backend|frontend|ai-pipeline|clinical-data|integrations|mobile|documentation|versioning|seeding|hitl-task-cards)` §",
    r"home\.arpa|/home/ilias",
)

MOVED_OUT_DOCS = ("RELEASE_PROCESS.md", "DEVELOPMENT_PLAN.md", "STATUS.md")

REPO_ONLY_DOCS = {"TASK_DEBUGGING.md", "UI_CAPTURE_PIPELINE.md"}


def _public_surfaces() -> list[Path]:
    surfaces = sorted(DOCS_DIR.glob("*.md"))
    surfaces += [REPO_ROOT / "README.md", REPO_ROOT / "AGENTS.md"]
    return [p for p in surfaces if p.is_file()]


def _unreleased_slice() -> tuple[str, int]:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.find("## [Unreleased]")
    if start == -1:
        return "", 0
    rest = changelog[start:]
    nxt = rest.find("\n## [", 1)
    return (rest if nxt == -1 else rest[:nxt]), start


def _nav_files() -> set[str]:
    content = DOCS_TREE.read_text(encoding="utf-8")
    return set(re.findall(r'"file"\s*:\s*"([^"]+)"', content))


def test_public_surfaces_contain_no_internal_references():
    failures: list[str] = []
    for path in _public_surfaces():
        text = path.read_text(encoding="utf-8")
        for pattern in BANNED_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line = text[: match.start()].count("\n") + 1
                failures.append(f"{path.relative_to(REPO_ROOT)}:{line}: /{pattern}/")
    slice_, start = _unreleased_slice()
    for pattern in BANNED_PATTERNS:
        for match in re.finditer(pattern, slice_, re.IGNORECASE):
            line = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")[
                : start + match.start()
            ].count("\n") + 1
            failures.append(f"CHANGELOG.md:{line}: /{pattern}/ (unreleased)")
    assert not failures, "publication-policy violations:\n" + "\n".join(failures)


def test_moved_out_docs_stay_out():
    for name in MOVED_OUT_DOCS:
        assert not (DOCS_DIR / name).is_file(), f"{name} must not return to docs/"


def test_docs_tree_references_existing_files_only():
    content = DOCS_TREE.read_text(encoding="utf-8")
    json.loads(content)
    files = _nav_files()
    assert files, "docs-tree.json exposes no files"
    for name in sorted(files):
        assert (DOCS_DIR / name).is_file(), f"docs-tree.json references missing {name}"
    for name in MOVED_OUT_DOCS:
        assert name not in files, f"docs-tree.json still references moved-out {name}"


def test_every_doc_is_in_nav_or_repo_only_allowlist():
    nav = _nav_files()
    for path in sorted(DOCS_DIR.glob("*.md")):
        assert path.name in nav or path.name in REPO_ONLY_DOCS, (
            f"{path.name} is neither in docs-tree.json nor on the repo-only allowlist"
        )
