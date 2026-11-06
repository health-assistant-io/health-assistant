"""The product capability-showcase wall (family contract
``product-capabilities``): ``docs/capabilities.json`` validates against
the vendored schema copy and passes the semantic rules the schema cannot
express — the project slug matches this repo, capability ids are unique
across the manifest, and every
capability ids are unique.

Docs truthfulness law: docs/capabilities.json must be updated in the
same change as a shipped feature (family contract:
contracts/product-capabilities.schema.json) — every failure below says
so, the same way the docs-tree wall does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extensions import contract

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "docs" / "capabilities.json"
SCHEMA_PATH = REPO_ROOT / "contracts" / "product-capabilities.schema.json"

SAME_CHANGE = (
    "docs/capabilities.json must be updated in the same change as a "
    "shipped feature (family contract: contracts/product-capabilities.schema.json)"
)


def _load_manifest() -> dict:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        pytest.fail(f"{SAME_CHANGE} — the manifest does not parse: {exc}")


# --------------------------------------------------------- the real manifest


def test_manifest_validates_against_the_vendored_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    doc = _load_manifest()
    err = contract.validate(schema, doc, schema)
    assert err is None, f"{SAME_CHANGE}: {err}"


def test_project_slug_matches_this_repo() -> None:
    doc = _load_manifest()
    assert doc.get("project") == "health", (
        f"{SAME_CHANGE}: project must be 'health' (the hub cross-checks it "
        f"against this repo), got {doc.get('project')!r}"
    )


def test_capability_ids_are_unique() -> None:
    doc = _load_manifest()
    ids = [cap.get("id") for cap in doc.get("capabilities", [])]
    dupes = sorted({cap_id for cap_id in ids if cap_id is not None and ids.count(cap_id) > 1})
    assert not dupes, f"{SAME_CHANGE}: duplicate capability ids {dupes}"


# ------------------------------------------------------- contract semantics


def _semantic_problems(doc: dict, expected_project: str) -> list[str]:
    problems: list[str] = []
    if doc.get("project") != expected_project:
        problems.append(f"project must be {expected_project!r}")
    ids = [cap.get("id") for cap in doc.get("capabilities", [])]
    problems += [f"duplicate capability id {i!r}" for i in sorted(set(ids)) if ids.count(i) > 1]
    for cap in doc.get("capabilities", []):
        if cap.get("status") != "shipped" and (cap.get("proofs") or cap.get("metric")):
            problems.append(
                f"capability {cap.get('id')!r} is {cap.get('status')!r} and must not claim "
                f"proofs or a metric (shipped only)"
            )
        visual = cap.get("visual")
        if isinstance(visual, str) and (
            visual.startswith("/") or "\\" in visual or ".." in visual.split("/")
        ):
            problems.append(
                f"capability {cap.get('id')!r} visual escapes the manifest directory: {visual!r}"
            )
    return problems


def test_semantics_hold_for_the_real_manifest() -> None:
    doc = _load_manifest()
    problems = _semantic_problems(doc, "health")
    assert not problems, f"{SAME_CHANGE}: " + "; ".join(problems)


def test_contract_fixtures_stay_honest() -> None:
    fixtures_path = (
        REPO_ROOT.parent.parent / "dev" / "contracts" / "product-capabilities.fixtures.json"
    )
    if not fixtures_path.is_file():
        pytest.skip("family dev repo not present beside this checkout")
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for case in fixtures["valid"]:
        assert contract.validate(schema, case["doc"], schema) is None, (
            f"fixture {case['name']!r} must stay valid against the contract"
        )
        assert not _semantic_problems(case["doc"], case["doc"].get("project", "")), (
            f"fixture {case['name']!r} must stay semantically clean"
        )
    for case in fixtures["invalid"]:
        rejected = contract.validate(schema, case["doc"], schema) is not None or bool(
            _semantic_problems(
                case["doc"], case.get("expectProject", case["doc"].get("project", ""))
            )
        )
        assert rejected, f"fixture {case['name']!r} must stay invalid against the contract"
