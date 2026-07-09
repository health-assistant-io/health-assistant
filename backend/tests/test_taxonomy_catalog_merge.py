"""Tests for the taxonomy/catalog merge (plan ``taxonomy-catalog-merge-2026-07-09.md``).

Covers the r2 success criteria:
- **S1** multi-kind concept filter via the tag join (appears under every kind).
- **S2** concept write round-trip preserves multi-kind tags (via ``/concepts``).
- **S3** delete with edges → retired (bidirectional count — incoming edges count).
- **S4** delete without edges → soft-deleted (``deleted_at`` set, excluded from default list).
- **S8** audit closure: ``/concepts`` create produces a ``CatalogAuditLog`` row.
- **S9** USER cannot write concepts.
- **405** safety net: ``POST/PUT/DELETE /catalogs/concept`` returns 405.
- Bidirectional count helper (incoming-only concept counts > 0).
- Scope invariant: ``create_concept`` sets ``scope`` consistent with ``tenant_id``.
- ``restore_concept`` reverses a retire.
"""

import uuid

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.models.catalog_audit_model import CatalogAuditLog
from app.models.concept_model import Concept, ConceptKindTag
from app.models.enums import (
    CatalogScope,
    ConceptKind,
    ConceptStatus,
    EdgeApprovalStatus,
    EdgeEndpointType,
    ConceptRelationType,
)
from app.services.concept_service import ConceptService
from app.services.catalog_graph_service import count_relations_both_directions
from sqlalchemy import delete, select


@pytest_asyncio.fixture
async def clean_concept_namespace():
    """UUID-prefixed slug namespace so repeated runs don't collide."""
    return uuid.uuid4().hex[:8]


async def _make_concept(
    slug: str,
    name: str,
    kinds: list[ConceptKind],
    *,
    tenant_id=None,
    role="SYSTEM_ADMIN",
    parent_id=None,
    description=None,
):
    """Helper: create + commit a concept via ConceptService."""
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        concept = await svc.create_concept(
            slug=slug,
            name=name,
            kinds=kinds,
            tenant_id=tenant_id,
            role=role,
            parent_id=parent_id,
            description=description,
        )
        await session.commit()
        return concept.id


# ---------------------------------------------------------------------------
# S1 — multi-kind filter via the tag join
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_concept_list_multi_kind_filter(clean_concept_namespace):
    """A concept tagged with 3 kinds appears under each kind's filter, not just
    its ``primary_kind`` (the defect the tag-join fix closes)."""
    p = clean_concept_namespace
    cid = await _make_concept(
        slug=f"{p}-multi",
        name=f"Multi {p}",
        kinds=[
            ConceptKind.EXAMINATION_CATEGORY,
            ConceptKind.BIOMARKER_CLASS,
            ConceptKind.DOCUMENT_CATEGORY,
        ],
    )

    async with AsyncSessionLocal() as session:
        from app.catalogs.adapters import ConceptCatalogAdapter

        adapter = ConceptCatalogAdapter()
        for kind in (
            ConceptKind.EXAMINATION_CATEGORY,
            ConceptKind.BIOMARKER_CLASS,
            ConceptKind.DOCUMENT_CATEGORY,
        ):
            result = await adapter.list(session, None, kind=kind.value, limit=500)
            ids = {item["id"] for item in result["items"]}
            assert str(cid) in ids, (
                f"concept should appear under kind={kind.value}"
            )


# ---------------------------------------------------------------------------
# S2 — write round-trip preserves kinds (via /concepts, not the catalog adapter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concept_update_preserves_kinds(clean_concept_namespace):
    """``PUT /concepts/{id}`` with ``kinds: [a,b,c]`` survives a round-trip —
    ``GET`` returns all three. This is the write path the catalog modal will
    dispatch to."""
    p = clean_concept_namespace
    cid = await _make_concept(
        slug=f"{p}-rt",
        name=f"RoundTrip {p}",
        kinds=[ConceptKind.SPECIALTY],
    )
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.update_concept(
            cid,
            None,
            "SYSTEM_ADMIN",
            kinds=[
                ConceptKind.EXAMINATION_CATEGORY,
                ConceptKind.BIOMARKER_CLASS,
                ConceptKind.DOCUMENT_CATEGORY,
            ],
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        concept = await svc.get_concept(cid, None)
        assert set(concept.kinds) == {
            ConceptKind.EXAMINATION_CATEGORY.value,
            ConceptKind.BIOMARKER_CLASS.value,
            ConceptKind.DOCUMENT_CATEGORY.value,
        }


# ---------------------------------------------------------------------------
# S3 — delete with edges → retired (bidirectional: incoming edges count)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concept_delete_with_incoming_edges_retires(clean_concept_namespace):
    """A concept that has only *incoming* edges (e.g. a biomarker MEMBER_OF it)
    is retired, not soft-deleted — the bidirectional count catches what an
    outgoing-only count would miss (the r1 bug)."""
    p = clean_concept_namespace
    parent_id = await _make_concept(
        slug=f"{p}-panel", name=f"Panel {p}", kinds=[ConceptKind.BIOMARKER_PANEL]
    )
    child_id = await _make_concept(
        slug=f"{p}-child", name=f"Child {p}", kinds=[ConceptKind.SPECIALTY]
    )

    # Edge: child → parent (so parent has only an INCOMING edge).
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.create_edge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=child_id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=parent_id,
            relation=ConceptRelationType.MEMBER_OF,
            tenant_id=None,
            role="SYSTEM_ADMIN",
        )
        await session.commit()

    # Delete the parent — it has an incoming edge, must be retired not deleted.
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.delete_concept(parent_id, None, "SYSTEM_ADMIN")
        await session.commit()

    async with AsyncSessionLocal() as session:
        concept = await session.get(Concept, parent_id)
        assert concept.status == ConceptStatus.RETIRED
        assert concept.deleted_at is None  # not soft-deleted — edges intact


@pytest.mark.asyncio
async def test_concept_delete_with_outgoing_edges_retires(clean_concept_namespace):
    """A concept with *outgoing* edges is also retired (symmetry check)."""
    p = clean_concept_namespace
    src_id = await _make_concept(
        slug=f"{p}-src", name=f"Src {p}", kinds=[ConceptKind.SPECIALTY]
    )
    dst_id = await _make_concept(
        slug=f"{p}-dst", name=f"Dst {p}", kinds=[ConceptKind.BODY_SYSTEM]
    )
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.create_edge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=src_id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=dst_id,
            relation=ConceptRelationType.EXAMINES,
            tenant_id=None,
            role="SYSTEM_ADMIN",
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.delete_concept(src_id, None, "SYSTEM_ADMIN")
        await session.commit()

    async with AsyncSessionLocal() as session:
        concept = await session.get(Concept, src_id)
        assert concept.status == ConceptStatus.RETIRED
        assert concept.deleted_at is None


# ---------------------------------------------------------------------------
# S4 — delete without edges → soft-deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concept_delete_without_edges_soft_deletes(clean_concept_namespace):
    """An edge-less concept is retired AND soft-deleted (``deleted_at`` set);
    excluded from default reads."""
    p = clean_concept_namespace
    cid = await _make_concept(
        slug=f"{p}-lonely", name=f"Lonely {p}", kinds=[ConceptKind.DISEASE]
    )
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.delete_concept(cid, None, "SYSTEM_ADMIN")
        await session.commit()

    async with AsyncSessionLocal() as session:
        concept = await session.get(Concept, cid)
        assert concept.status == ConceptStatus.RETIRED
        assert concept.deleted_at is not None
        # Excluded from default reads
        svc = ConceptService(session)
        visible = await svc.list_concepts(None, limit=500)
        assert cid not in {c.id for c in visible}


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concept_restore(clean_concept_namespace):
    """``restore_concept`` flips status back to active and clears ``deleted_at``."""
    p = clean_concept_namespace
    cid = await _make_concept(
        slug=f"{p}-restorable", name=f"Restorable {p}", kinds=[ConceptKind.DISEASE]
    )
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.delete_concept(cid, None, "SYSTEM_ADMIN")
        await session.commit()
        await svc.restore_concept(cid, None, "SYSTEM_ADMIN")
        await session.commit()

    async with AsyncSessionLocal() as session:
        concept = await session.get(Concept, cid)
        assert concept.status == ConceptStatus.ACTIVE
        assert concept.deleted_at is None


# ---------------------------------------------------------------------------
# Bidirectional count helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_relations_both_directions(clean_concept_namespace):
    """The shared helper counts edges in BOTH directions — an incoming-only
    concept must show > 0 (the r1 ``count_relations`` bug was outgoing-only)."""
    p = clean_concept_namespace
    target_id = await _make_concept(
        slug=f"{p}-target", name=f"Target {p}", kinds=[ConceptKind.BIOMARKER_PANEL]
    )
    source_id = await _make_concept(
        slug=f"{p}-source", name=f"Source {p}", kinds=[ConceptKind.SPECIALTY]
    )
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.create_edge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=source_id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=target_id,
            relation=ConceptRelationType.MEMBER_OF,
            tenant_id=None,
            role="SYSTEM_ADMIN",
        )
        await session.commit()

        # target has only an INCOMING edge
        counts = await count_relations_both_directions(
            session, EdgeEndpointType.CONCEPT, [target_id], tenant_id=None
        )
        assert counts[str(target_id)]["total"] >= 1


# ---------------------------------------------------------------------------
# Scope invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_concept_sets_scope_consistent_with_tenant(
    clean_concept_namespace, system_admin_headers
):
    """``create_concept`` derives ``scope`` from ``tenant_id`` so tenant-scoped
    concepts don't land with ``scope=SYSTEM`` (the pre-existing inconsistency
    the catalog read path would mis-filter on)."""
    from app.core.security import decode_access_token

    p = clean_concept_namespace
    token = system_admin_headers["Authorization"].removeprefix("Bearer ")
    tenant_id = uuid.UUID(decode_access_token(token)["tenant_id"])

    # Tenant-scoped concept → scope=TENANT
    tid_tenant = await _make_concept(
        slug=f"{p}-tenant",
        name=f"TenantScope {p}",
        kinds=[ConceptKind.DISEASE],
        tenant_id=tenant_id,
        role="SYSTEM_ADMIN",
    )
    # Global concept → scope=SYSTEM
    tid_global = await _make_concept(
        slug=f"{p}-global",
        name=f"GlobalScope {p}",
        kinds=[ConceptKind.DISEASE],
        tenant_id=None,
        role="SYSTEM_ADMIN",
    )

    async with AsyncSessionLocal() as session:
        tenant_concept = await session.get(Concept, tid_tenant)
        global_concept = await session.get(Concept, tid_global)
        assert tenant_concept.scope == CatalogScope.TENANT
        assert global_concept.scope == CatalogScope.SYSTEM


# ---------------------------------------------------------------------------
# S8 — audit closure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concept_create_audits(clean_concept_namespace):
    """``ConceptService.create_concept`` with an ``actor`` appends a
    ``CatalogAuditLog`` row (``catalog_type='concept'``, ``operation='create'``)."""
    p = clean_concept_namespace

    class FakeActor:
        tenant_id = None
        user_id = uuid.uuid4()
        sub = "audittest@test.local"

    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        concept = await svc.create_concept(
            slug=f"{p}-audited",
            name=f"Audited {p}",
            kinds=[ConceptKind.DISEASE],
            tenant_id=None,
            role="SYSTEM_ADMIN",
            actor=FakeActor(),
        )
        cid = concept.id
        await session.commit()

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(CatalogAuditLog)
                .where(
                    CatalogAuditLog.catalog_type == "concept",
                    CatalogAuditLog.item_id == cid,
                )
            )
        ).scalars().all()
        assert len(rows) >= 1
        assert rows[-1].operation == "create"
        assert rows[-1].user_email == "audittest@test.local"


@pytest.mark.asyncio
async def test_concept_create_without_actor_does_not_audit(
    clean_concept_namespace,
):
    """``create_concept`` without ``actor`` skips audit (the default for any
    non-interactive caller; seeds bypass the service entirely)."""
    p = clean_concept_namespace
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        concept = await svc.create_concept(
            slug=f"{p}-noaudit",
            name=f"NoAudit {p}",
            kinds=[ConceptKind.DISEASE],
            tenant_id=None,
            role="SYSTEM_ADMIN",
        )
        cid = concept.id
        await session.commit()

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(CatalogAuditLog).where(
                    CatalogAuditLog.catalog_type == "concept",
                    CatalogAuditLog.item_id == cid,
                )
            )
        ).scalars().all()
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# S9 — USER cannot write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_cannot_create_concept(clean_concept_namespace):
    p = clean_concept_namespace
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        with pytest.raises(PermissionError):
            await svc.create_concept(
                slug=f"{p}-denied",
                name=f"Denied {p}",
                kinds=[ConceptKind.DISEASE],
                tenant_id=None,
                role="USER",
            )


# ---------------------------------------------------------------------------
# 405 safety net — concept writes via /catalogs/concept are rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_concept_write_returns_405(async_client, system_admin_headers):
    """The generic catalog write endpoints return 405 for ``type=concept`` —
    the read-only adapter contract is enforced server-side."""
    resp = await async_client.post(
        "/api/v1/catalogs/concept",
        json={"slug": "should-fail", "name": "Fail", "kinds": ["disease"]},
        headers=system_admin_headers,
    )
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_catalog_concept_update_returns_405(async_client, system_admin_headers):
    resp = await async_client.put(
        f"/api/v1/catalogs/concept/{uuid.uuid4()}",
        json={"name": "Fail"},
        headers=system_admin_headers,
    )
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_catalog_concept_delete_returns_405(async_client, system_admin_headers):
    resp = await async_client.delete(
        f"/api/v1/catalogs/concept/{uuid.uuid4()}",
        headers=system_admin_headers,
    )
    assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Concept reads via the catalog adapter still work (parent_slug, search)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_concept_get_has_parent_slug(clean_concept_namespace):
    """``GET /catalogs/concept/{id}`` attaches ``parent_slug`` for the parent picker."""
    p = clean_concept_namespace
    parent_id = await _make_concept(
        slug=f"{p}-parent", name=f"Parent {p}", kinds=[ConceptKind.BODY_SYSTEM]
    )
    child_id = await _make_concept(
        slug=f"{p}-child",
        name=f"Child {p}",
        kinds=[ConceptKind.ORGAN],
        parent_id=parent_id,
    )
    async with AsyncSessionLocal() as session:
        from app.catalogs.adapters import ConceptCatalogAdapter

        adapter = ConceptCatalogAdapter()
        item = await adapter.get(session, None, child_id)
        assert item is not None
        assert item["parent_slug"] == f"{p}-parent"


@pytest.mark.asyncio
async def test_catalog_concept_search_finds_by_description(clean_concept_namespace):
    """``search_columns`` now includes ``description`` for trigram search."""
    p = clean_concept_namespace
    await _make_concept(
        slug=f"{p}-desc",
        name=f"DescConcept {p}",
        kinds=[ConceptKind.DISEASE],
        description=f"veryuniquedescription {p}",
    )
    async with AsyncSessionLocal() as session:
        from app.catalogs.adapters import ConceptCatalogAdapter

        adapter = ConceptCatalogAdapter()
        hits = await adapter.search(session, None, f"veryuniquedescription {p}")
        assert any(h["id"] for h in hits)


# ---------------------------------------------------------------------------
# Phase 5 — whole_concept_graph (rootless graph endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whole_concept_graph_returns_nodes_and_edges(clean_concept_namespace):
    """The rootless graph loader returns concepts + edges between them."""
    p = clean_concept_namespace
    a_id = await _make_concept(
        slug=f"{p}-graph-a", name=f"GraphA {p}", kinds=[ConceptKind.DISEASE]
    )
    b_id = await _make_concept(
        slug=f"{p}-graph-b", name=f"GraphB {p}", kinds=[ConceptKind.SYMPTOM]
    )
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.create_edge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=a_id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=b_id,
            relation=ConceptRelationType.AFFECTS,
            tenant_id=None,
            role="SYSTEM_ADMIN",
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        from app.services.catalog_graph_service import whole_concept_graph

        # Use a kind filter to narrow the result set (the test DB accumulates
        # concepts across runs; the default limit_nodes=1000 would cut off
        # the test's concepts otherwise).
        result = await whole_concept_graph(
            session, tenant_id=None, kinds=["disease", "symptom"]
        )
        node_ids = {n["id"] for n in result["nodes"]}
        assert str(a_id) in node_ids
        assert str(b_id) in node_ids
        assert any(
            e["src"]["id"] == str(a_id) and e["dst"]["id"] == str(b_id)
            for e in result["edges"]
        )
        assert result["truncated"] is False


@pytest.mark.asyncio
async def test_whole_concept_graph_kind_filter_narrows(clean_concept_namespace):
    """The kind filter reduces both nodes and edges — only concepts carrying
    the requested kind appear, and only edges between them are returned."""
    p = clean_concept_namespace
    disease_id = await _make_concept(
        slug=f"{p}-dis", name=f"Disease {p}", kinds=[ConceptKind.DISEASE]
    )
    symptom_id = await _make_concept(
        slug=f"{p}-sym", name=f"Symptom {p}", kinds=[ConceptKind.SYMPTOM]
    )
    specialty_id = await _make_concept(
        slug=f"{p}-spec", name=f"Specialty {p}", kinds=[ConceptKind.SPECIALTY]
    )
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        # disease → symptom edge (should survive a disease+symptom filter)
        await svc.create_edge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=disease_id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=symptom_id,
            relation=ConceptRelationType.AFFECTS,
            tenant_id=None,
            role="SYSTEM_ADMIN",
        )
        # specialty → disease edge (should NOT survive — specialty excluded)
        await svc.create_edge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=specialty_id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=disease_id,
            relation=ConceptRelationType.TREATS,
            tenant_id=None,
            role="SYSTEM_ADMIN",
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        from app.services.catalog_graph_service import whole_concept_graph

        result = await whole_concept_graph(
            session, tenant_id=None, kinds=["disease", "symptom"]
        )
        node_ids = {n["id"] for n in result["nodes"]}
        assert str(disease_id) in node_ids
        assert str(symptom_id) in node_ids
        assert str(specialty_id) not in node_ids
        # The disease→symptom edge survives (both endpoints in the kind set).
        assert any(
            e["src"]["id"] == str(disease_id)
            and e["dst"]["id"] == str(symptom_id)
            for e in result["edges"]
        )
        # The specialty→disease edge does NOT survive (specialty excluded).
        assert not any(
            e["src"]["id"] == str(specialty_id) for e in result["edges"]
        )


@pytest.mark.asyncio
async def test_whole_concept_graph_endpoint_works(
    async_client, system_admin_headers, clean_concept_namespace
):
    """``GET /catalogs/concept/graph`` returns a valid payload via HTTP."""
    p = clean_concept_namespace
    await _make_concept(
        slug=f"{p}-ep", name=f"Endpoint {p}", kinds=[ConceptKind.ORGAN]
    )
    resp = await async_client.get(
        f"/api/v1/catalogs/concept/graph?kind=organ",
        headers=system_admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body
    assert "edges" in body
    assert "truncated" in body
    assert any(n["label"] == f"Endpoint {p}" for n in body["nodes"])
