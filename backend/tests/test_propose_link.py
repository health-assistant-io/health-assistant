"""Tests for the modular link-proposal helpers (Phase 1 of the
``propose-link-hitl`` plan; see ``dev/plans/propose-link-hitl-2026-07-21.md``).

These tests pin three contracts:

1. ``LINK_SCHEMA`` integrity — every endpoint + relation references a valid
   enum value, so the matrix can never silently drift from the enums it
   references.
2. ``validate_relation_combo`` + the ``relations_for_*`` helpers — the pure
   functions consumed by the tool layer, the LLM discovery tool, the REST
   endpoint, and the frontend ``<LinksSection>``.
3. ``build_link_specs`` — the orchestrator that validates + snapshots the
   LLM-supplied ``links[]`` argument on every ``propose_*`` tool. Drop-and-report
   semantics for invalid combos; dedup badge when the primary already exists.
4. The ``GET /api/v1/concept-edges/schema`` REST endpoint.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from app.ai.tools import propose_link as pl
from app.models.enums import ConceptRelationType, EdgeEndpointType


# ---------------------------------------------------------------------------
# LINK_SCHEMA integrity
# ---------------------------------------------------------------------------


def test_link_schema_keys_are_valid_endpoint_pairs():
    """Every (src, dst) key references real EdgeEndpointType values."""
    for src, dst in pl.LINK_SCHEMA.keys():
        assert isinstance(src, EdgeEndpointType), (
            f"src {src!r} must be an EdgeEndpointType"
        )
        assert isinstance(dst, EdgeEndpointType), (
            f"dst {dst!r} must be an EdgeEndpointType"
        )


def test_link_schema_values_are_valid_relations():
    """Every relation in the matrix references a real ConceptRelationType."""
    for (src, dst), relations in pl.LINK_SCHEMA.items():
        assert relations, (
            f"empty relations list for {src.value}->{dst.value} — "
            "drop the entry instead"
        )
        for r in relations:
            assert isinstance(r, ConceptRelationType), (
                f"relation {r!r} for {src.value}->{dst.value} "
                "must be a ConceptRelationType"
            )


def test_link_schema_no_duplicate_relations_per_pair():
    """No accidental duplicates within a single (src, dst) entry."""
    for (src, dst), relations in pl.LINK_SCHEMA.items():
        assert len(relations) == len(set(relations)), (
            f"duplicate relations for {src.value}->{dst.value}: {relations}"
        )


def test_link_schema_covers_common_catalog_combos():
    """Sanity: the matrix covers the cases the plan promised."""
    med = EdgeEndpointType.MEDICATION
    conc = EdgeEndpointType.CONCEPT
    biom = EdgeEndpointType.BIOMARKER
    cet = EdgeEndpointType.CLINICAL_EVENT_TYPE
    ana = EdgeEndpointType.ANATOMY

    # Medication → concept includes TREATS + CONTRAINDICATES
    assert ConceptRelationType.TREATS in pl.LINK_SCHEMA[(med, conc)]
    assert ConceptRelationType.CONTRAINDICATES in pl.LINK_SCHEMA[(med, conc)]

    # Biomarker → concept includes MEMBER_OF (panel membership)
    assert ConceptRelationType.MEMBER_OF in pl.LINK_SCHEMA[(biom, conc)]

    # Clinical event type → biomarker includes MONITORS
    assert ConceptRelationType.MONITORS in pl.LINK_SCHEMA[(cet, biom)]

    # Anatomy hierarchy exists
    assert ConceptRelationType.BRANCH_OF in pl.LINK_SCHEMA[(ana, ana)]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_validate_relation_combo_accepts_valid():
    ok, reason = pl.validate_relation_combo(
        EdgeEndpointType.MEDICATION,
        ConceptRelationType.TREATS,
        EdgeEndpointType.CONCEPT,
    )
    assert ok is True
    assert reason == ""


def test_validate_relation_combo_rejects_wrong_relation_for_pair():
    """Same endpoint pair but relation not in the allowed list."""
    ok, reason = pl.validate_relation_combo(
        EdgeEndpointType.MEDICATION,
        ConceptRelationType.BRANCH_OF,  # anatomy-only relation
        EdgeEndpointType.CONCEPT,
    )
    assert ok is False
    assert "BRANCH_OF" in reason
    assert "medication -> concept" in reason
    # Hint lists the valid options
    assert "TREATS" in reason


def test_validate_relation_combo_rejects_unknown_pair():
    """Pair not in the matrix at all."""
    ok, reason = pl.validate_relation_combo(
        EdgeEndpointType.DOCTOR,
        ConceptRelationType.TREATS,
        EdgeEndpointType.ANATOMY,
    )
    assert ok is False
    assert "doctor -> anatomy" in reason


def test_relations_for_returns_strings():
    out = pl.relations_for(EdgeEndpointType.MEDICATION, EdgeEndpointType.CONCEPT)
    assert "TREATS" in out
    assert all(isinstance(r, str) for r in out)


def test_relations_for_unknown_pair_returns_empty():
    out = pl.relations_for(EdgeEndpointType.DOCTOR, EdgeEndpointType.ANATOMY)
    assert out == []


def test_relations_for_source_groups_by_destination():
    out = pl.relations_for_source(EdgeEndpointType.MEDICATION)
    # Destination keys are strings
    assert "concept" in out
    assert "TREATS" in out["concept"]
    # Destination types are a (small) set — not all endpoint types
    assert len(out) <= len(EdgeEndpointType)


def test_serialize_full_schema_is_jsonable():
    rows = pl.serialize_full_schema()
    assert rows, "schema must not be empty"
    for row in rows:
        assert set(row.keys()) == {"src_type", "dst_type", "relations"}
        assert isinstance(row["relations"], list)
        assert row["relations"]


# ---------------------------------------------------------------------------
# build_link_specs — the orchestrator called by every propose_* tool
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_dst_payload():
    """A resolved endpoint snapshot (matches ``concept_endpoint_resolver._payload``)."""
    return {
        "type": "concept",
        "id": str(uuid4()),
        "label": "Type 2 Diabetes",
        "icon": None,
        "color": None,
        "kind": "disease",
    }


@pytest.mark.asyncio
async def test_build_link_specs_empty_returns_empty(fake_dst_payload):
    out = await pl.build_link_specs(
        db=None,
        tenant_id=uuid4(),
        src_type=EdgeEndpointType.MEDICATION,
        raw_links=None,
    )
    assert out == {"kept": [], "dropped": []}

    out = await pl.build_link_specs(
        db=None,
        tenant_id=uuid4(),
        src_type=EdgeEndpointType.MEDICATION,
        raw_links=[],
    )
    assert out == {"kept": [], "dropped": []}


@pytest.mark.asyncio
async def test_build_link_specs_keeps_valid_and_snapshots(fake_dst_payload):
    """Valid link → kept with full snapshot of the destination."""
    tenant_id = uuid4()
    dst_id = fake_dst_payload["id"]

    with patch.object(
        pl, "resolve_endpoint", return_value=fake_dst_payload
    ) as mock_resolve:
        out = await pl.build_link_specs(
            db=object(),  # passed through to the mocked resolver
            tenant_id=tenant_id,
            src_type=EdgeEndpointType.MEDICATION,
            raw_links=[
                {
                    "dst_type": "concept",
                    "dst_id": dst_id,
                    "relation": "TREATS",
                    "properties": {"note": "first-line"},
                }
            ],
        )

    assert len(out["kept"]) == 1
    assert out["dropped"] == []
    kept = out["kept"][0]
    assert kept["dst"] == fake_dst_payload
    assert kept["relation"] == "TREATS"
    assert kept["properties"] == {"note": "first-line"}
    assert kept["duplicate_of"] is None
    # resolve_endpoint was awaited with (db, tenant_id, etype, identifier) positionally
    mock_resolve.assert_awaited_once()
    args, _ = mock_resolve.call_args
    assert args[1] == tenant_id
    assert args[2] == EdgeEndpointType.CONCEPT
    assert args[3] == dst_id


@pytest.mark.asyncio
async def test_build_link_specs_drops_invalid_relation(fake_dst_payload):
    """Invalid relation for the (src, dst) pair → dropped with a reason."""
    out = await pl.build_link_specs(
        db=None,
        tenant_id=uuid4(),
        src_type=EdgeEndpointType.MEDICATION,
        raw_links=[
            {
                "dst_type": "concept",
                "dst_id": fake_dst_payload["id"],
                "relation": "BRANCH_OF",  # anatomy-only
            }
        ],
    )
    assert out["kept"] == []
    assert len(out["dropped"]) == 1
    assert "BRANCH_OF" in out["dropped"][0]["reason"]
    assert "medication -> concept" in out["dropped"][0]["reason"]


@pytest.mark.asyncio
async def test_build_link_specs_drops_unknown_endpoint(fake_dst_payload):
    """Destination can't be resolved → dropped."""
    with patch.object(pl, "resolve_endpoint", return_value=None):
        out = await pl.build_link_specs(
            db=None,
            tenant_id=uuid4(),
            src_type=EdgeEndpointType.MEDICATION,
            raw_links=[
                {
                    "dst_type": "concept",
                    "dst_id": str(uuid4()),
                    "relation": "TREATS",
                }
            ],
        )
    assert out["kept"] == []
    assert len(out["dropped"]) == 1
    assert "not found" in out["dropped"][0]["reason"]


@pytest.mark.asyncio
async def test_build_link_specs_drops_malformed_entries():
    """Missing fields, wrong types, bad enum values all drop cleanly."""
    out = await pl.build_link_specs(
        db=None,
        tenant_id=uuid4(),
        src_type=EdgeEndpointType.MEDICATION,
        raw_links=[
            "not a dict",  # wrong type
            {"dst_type": "concept"},  # missing dst_id + relation
            {
                "dst_type": "concept",
                "dst_id": str(uuid4()),
                "relation": "NOT_A_REAL_RELATION",
            },  # bad enum
            {
                "dst_type": "invented_type",
                "dst_id": "x",
                "relation": "TREATS",
            },  # bad endpoint type
        ],
    )
    assert out["kept"] == []
    assert len(out["dropped"]) == 4
    reasons = " ".join(d["reason"] for d in out["dropped"])
    assert "missing required field" in reasons
    assert "invalid enum" in reasons.lower() or "invalid" in reasons.lower()


@pytest.mark.asyncio
async def test_build_link_specs_dedup_when_primary_exists(fake_dst_payload):
    """When primary_existing_id is provided, an existing edge surfaces as
    duplicate_of on the kept spec (NOT dropped — the form shows a badge)."""
    tenant_id = uuid4()
    dst_id = uuid4()
    fake_dst_payload["id"] = str(dst_id)
    existing_edge_id = str(uuid4())

    with (
        patch.object(pl, "resolve_endpoint", return_value=fake_dst_payload),
        patch.object(
            pl,
            "check_existing_edge",
            return_value={"id": existing_edge_id, "status": "approved"},
        ) as mock_check,
    ):
        out = await pl.build_link_specs(
            db=None,
            tenant_id=tenant_id,
            src_type=EdgeEndpointType.MEDICATION,
            raw_links=[
                {
                    "dst_type": "concept",
                    "dst_id": str(dst_id),
                    "relation": "TREATS",
                }
            ],
            primary_existing_id=uuid4(),
        )

    assert len(out["kept"]) == 1
    assert out["kept"][0]["duplicate_of"] == existing_edge_id
    mock_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_link_specs_skips_dedup_when_primary_new(fake_dst_payload):
    """When primary_existing_id is None (new primary being created), dedup
    is skipped entirely — there can't be a pre-existing edge to a not-yet-row."""
    with (
        patch.object(pl, "resolve_endpoint", return_value=fake_dst_payload),
        patch.object(
            pl, "check_existing_edge", side_effect=AssertionError("must not be called")
        ),
    ):
        out = await pl.build_link_specs(
            db=None,
            tenant_id=uuid4(),
            src_type=EdgeEndpointType.MEDICATION,
            raw_links=[
                {
                    "dst_type": "concept",
                    "dst_id": fake_dst_payload["id"],
                    "relation": "TREATS",
                }
            ],
            # primary_existing_id omitted
        )
    assert len(out["kept"]) == 1
    assert out["kept"][0]["duplicate_of"] is None


@pytest.mark.asyncio
async def test_build_link_specs_mixed_batch(fake_dst_payload):
    """One valid + one invalid + one unresolvable → 1 kept, 2 dropped."""
    with patch.object(pl, "resolve_endpoint", return_value=fake_dst_payload):
        out = await pl.build_link_specs(
            db=None,
            tenant_id=uuid4(),
            src_type=EdgeEndpointType.MEDICATION,
            raw_links=[
                {  # valid
                    "dst_type": "concept",
                    "dst_id": fake_dst_payload["id"],
                    "relation": "TREATS",
                },
                {  # invalid relation
                    "dst_type": "concept",
                    "dst_id": fake_dst_payload["id"],
                    "relation": "BRANCH_OF",
                },
                {  # missing relation
                    "dst_type": "concept",
                    "dst_id": fake_dst_payload["id"],
                },
            ],
        )
    assert len(out["kept"]) == 1
    assert len(out["dropped"]) == 2


# ---------------------------------------------------------------------------
# REST endpoint: GET /api/v1/concept-edges/schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_endpoint_full(async_client, system_admin_headers):
    resp = await async_client.get(
        "/api/v1/concept-edges/schema", headers=system_admin_headers
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert rows, "matrix must not be empty"
    sample = rows[0]
    assert set(sample.keys()) == {"src_type", "dst_type", "relations"}


@pytest.mark.asyncio
async def test_schema_endpoint_filter_by_src(async_client, system_admin_headers):
    resp = await async_client.get(
        "/api/v1/concept-edges/schema?src_type=medication",
        headers=system_admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert "concept" in body
    assert "TREATS" in body["concept"]


@pytest.mark.asyncio
async def test_schema_endpoint_filter_by_pair(async_client, system_admin_headers):
    resp = await async_client.get(
        "/api/v1/concept-edges/schema?src_type=medication&dst_type=concept",
        headers=system_admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"relations": body["relations"]}
    assert "TREATS" in body["relations"]


@pytest.mark.asyncio
async def test_schema_endpoint_rejects_bad_type(async_client, system_admin_headers):
    resp = await async_client.get(
        "/api/v1/concept-edges/schema?src_type=invented",
        headers=system_admin_headers,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Resolvers: confirm all 11 EdgeEndpointType values have a registered resolver
# ---------------------------------------------------------------------------


def test_all_endpoint_types_have_resolvers():
    """After the Phase 1 backfill, no EdgeEndpointType should fall through to
    the ``"{type}:{id-prefix}"`` fallback label."""
    from app.services.concept_endpoint_resolver import _RESOLVERS

    missing = [et for et in EdgeEndpointType if et not in _RESOLVERS]
    assert not missing, f"endpoint types without a resolver: {missing}"
