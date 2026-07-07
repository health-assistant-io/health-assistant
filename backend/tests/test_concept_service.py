"""Tests for ConceptService + search_concepts.

Real-DB integration tests covering CRUD, tenancy isolation, RBAC, edge
creation/validation, neighbor traversal, and the trigram search.
"""

import uuid

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.services.concept_service import ConceptService
from app.services.catalog_search_service import search_concepts
from app.models.enums import (
    ConceptKind,
    ConceptStatus,
    ConceptProvenance,
    EdgeApprovalStatus,
    EdgeEndpointType,
    ConceptRelationType,
)


@pytest_asyncio.fixture
async def tenant_with_data():
    """Create a tenant + seed a few concepts + edges for query tests.

    Returns ``(tenant_id, cardio_id, cvs_id, ecg_id, lipid_panel_id, prefix)``.
    Uses a UUID-prefixed slug namespace so repeated test runs don't collide
    with leftover data (no per-test transaction rollback in conftest).
    """
    from app.models.tenant_model import TenantModel

    tid = uuid.uuid4()
    p = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tid, name="Svc Test", slug=f"svc-{tid}"))
        await session.commit()
        svc = ConceptService(session)
        cardio = await svc.create_concept(
            slug=f"{p}-cardio",
            name="Cardiology",
            kind=ConceptKind.SPECIALTY,
            tenant_id=None,
            role="SYSTEM_ADMIN",
            coding_system="snomed",
            code="394579002",
            aliases=["cardiac", "heart"],
        )
        cvs = await svc.create_concept(
            slug=f"{p}-cvs",
            name="Cardiovascular System",
            kind=ConceptKind.BODY_SYSTEM,
            tenant_id=None,
            role="SYSTEM_ADMIN",
        )
        ecg = await svc.create_concept(
            slug=f"{p}-ecg",
            name="Electrocardiogram",
            kind=ConceptKind.EXAMINATION_CATEGORY,
            tenant_id=None,
            role="SYSTEM_ADMIN",
        )
        panel = await svc.create_concept(
            slug=f"{p}-lipid-panel",
            name="Lipid Panel",
            kind=ConceptKind.BIOMARKER_PANEL,
            tenant_id=None,
            role="SYSTEM_ADMIN",
            coding_system="loinc",
            code="LP39316-6",
        )
        await session.commit()
        await svc.create_edge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=cardio.id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=cvs.id,
            relation=ConceptRelationType.EXAMINES,
            tenant_id=None,
            role="SYSTEM_ADMIN",
            source=ConceptProvenance.SEED,
        )
        await svc.create_edge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=cardio.id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=ecg.id,
            relation=ConceptRelationType.PERFORMS,
            tenant_id=None,
            role="SYSTEM_ADMIN",
            source=ConceptProvenance.SEED,
        )
        await session.commit()
    return tid, cardio.id, cvs.id, ecg.id, panel.id, p


@pytest.mark.asyncio
async def test_list_concepts_by_kind(tenant_with_data):
    tid, cardio_id, cvs_id, ecg_id, panel_id, p = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        specialties = await svc.list_concepts(
            tid, kind=ConceptKind.SPECIALTY, limit=500
        )
        ids = {c.id for c in specialties}
        assert cardio_id in ids


@pytest.mark.asyncio
async def test_get_concept_tenancy(tenant_with_data):
    tid, cardio_id, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        found = await svc.get_concept(cardio_id, tid)
        assert found is not None
        assert found.name == "Cardiology"
        missing = await svc.get_concept(uuid.uuid4(), tid)
        assert missing is None


@pytest.mark.asyncio
async def test_rbac_user_cannot_create(tenant_with_data):
    tid, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        with pytest.raises(PermissionError):
            await svc.create_concept(
                slug="forbidden",
                name="Forbidden",
                kind=ConceptKind.DISEASE,
                tenant_id=tid,
                role="USER",
            )


@pytest.mark.asyncio
async def test_rbac_non_admin_cannot_create_global(tenant_with_data):
    tid, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        with pytest.raises(PermissionError):
            await svc.create_concept(
                slug="global-forbidden",
                name="Forbidden Global",
                kind=ConceptKind.DISEASE,
                tenant_id=None,
                role="ADMIN",
            )


@pytest.mark.asyncio
async def test_tenant_admin_can_create_tenant_scoped(tenant_with_data):
    tid, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        concept = await svc.create_concept(
            slug="tenant-only",
            name="Tenant Only",
            kind=ConceptKind.DISEASE,
            tenant_id=tid,
            role="ADMIN",
        )
        await session.commit()
        assert concept.tenant_id == tid
        assert concept.is_global is False


@pytest.mark.asyncio
async def test_create_duplicate_concept_raises(tenant_with_data):
    tid, cardio_id, _, _, _, p = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        # Reuse the fixture's cardio slug (global) -> duplicate.
        with pytest.raises(ValueError, match="already exists"):
            await svc.create_concept(
                slug=f"{p}-cardio",
                name="Dup",
                kind=ConceptKind.SPECIALTY,
                tenant_id=None,
                role="SYSTEM_ADMIN",
            )


@pytest.mark.asyncio
async def test_update_concept(tenant_with_data):
    tid, cardio_id, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        updated = await svc.update_concept(
            cardio_id,
            tid,
            "SYSTEM_ADMIN",
            description="Heart specialists",
            color="#ff0000",
        )
        await session.commit()
        assert updated.description == "Heart specialists"
        assert updated.color == "#ff0000"


@pytest.mark.asyncio
async def test_update_concept_add_and_remove_kinds(tenant_with_data):
    """Editing kinds must diff, not replace — keeping a kind that's already
    present must not violate the (concept_id, kind) unique constraint."""
    tid, cardio_id, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        # cardio starts as [specialty]; add examination_category (keep specialty).
        updated = await svc.update_concept(
            cardio_id, tid, "SYSTEM_ADMIN",
            kinds=["specialty", "examination_category"],
        )
        await session.commit()
        await session.refresh(updated, ["kind_tags"])
        assert set(updated.kinds) == {"specialty", "examination_category"}

        # Now drop specialty — only examination_category remains.
        updated = await svc.update_concept(
            cardio_id, tid, "SYSTEM_ADMIN", kinds=["examination_category"],
        )
        await session.commit()
        await session.refresh(updated, ["kind_tags"])
        assert updated.kinds == ["examination_category"]
        assert updated.primary_kind == ConceptKind.EXAMINATION_CATEGORY

        # Reject empty kinds.
        with pytest.raises(ValueError, match="at least one kind"):
            await svc.update_concept(cardio_id, tid, "SYSTEM_ADMIN", kinds=[])


@pytest.mark.asyncio
async def test_update_concept_non_admin_blocked(tenant_with_data):
    tid, cardio_id, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        with pytest.raises(PermissionError):
            await svc.update_concept(cardio_id, tid, "USER", description="hack")


@pytest.mark.asyncio
async def test_delete_concept_with_edges_retires(tenant_with_data):
    tid, cardio_id, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        await svc.delete_concept(cardio_id, tid, "SYSTEM_ADMIN")
        await session.commit()
        c = await svc.get_concept(cardio_id, tid)
        assert c is not None
        assert c.status == ConceptStatus.RETIRED
        assert c.deleted_at is None


@pytest.mark.asyncio
async def test_create_and_query_edge(tenant_with_data):
    tid, cardio_id, cvs_id, ecg_id, panel_id, p = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        edges = await svc.get_edges(
            tid,
            src_type=EdgeEndpointType.CONCEPT,
            src_id=cardio_id,
        )
        assert len(edges) >= 2
        rels = {e.relation for e in edges}
        assert ConceptRelationType.EXAMINES in rels
        assert ConceptRelationType.PERFORMS in rels


@pytest.mark.asyncio
async def test_get_neighbors(tenant_with_data):
    tid, cardio_id, cvs_id, ecg_id, panel_id, p = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        neighbors = await svc.get_neighbors(cardio_id, tid)
        neighbor_labels = {n["endpoint"]["label"] for n in neighbors if n["endpoint"]}
        assert "Cardiovascular System" in neighbor_labels
        assert "Electrocardiogram" in neighbor_labels


@pytest.mark.asyncio
async def test_get_neighbors_by_relation(tenant_with_data):
    tid, cardio_id, _, _, _, p = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        neighbors = await svc.get_neighbors(
            cardio_id,
            tid,
            relation=ConceptRelationType.EXAMINES,
        )
        assert len(neighbors) == 1
        assert neighbors[0]["endpoint"]["label"] == "Cardiovascular System"


@pytest.mark.asyncio
async def test_entity_to_concept_edge(tenant_with_data):
    tid, _, _, _, panel_id, p = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        biomarker_uuid = uuid.uuid4()
        await svc.create_edge(
            src_type=EdgeEndpointType.BIOMARKER,
            src_id=biomarker_uuid,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=panel_id,
            relation=ConceptRelationType.MEMBER_OF,
            tenant_id=None,
            role="SYSTEM_ADMIN",
            properties={"display_order": 1},
        )
        await session.commit()
        panels = await svc.get_entity_concepts(
            EdgeEndpointType.BIOMARKER,
            biomarker_uuid,
            tid,
            relation=ConceptRelationType.MEMBER_OF,
        )
        assert len(panels) == 1
        assert panels[0].slug == f"{p}-lipid-panel"


@pytest.mark.asyncio
async def test_edge_validates_concept_exists(tenant_with_data):
    tid, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        with pytest.raises(ValueError, match="not found"):
            await svc.create_edge(
                src_type=EdgeEndpointType.CONCEPT,
                src_id=uuid.uuid4(),
                dst_type=EdgeEndpointType.CONCEPT,
                dst_id=uuid.uuid4(),
                relation=ConceptRelationType.TREATS,
                tenant_id=None,
                role="SYSTEM_ADMIN",
            )


@pytest.mark.asyncio
async def test_ai_proposed_edge_lands_as_proposed(tenant_with_data):
    tid, cardio_id, _, _, panel_id, _ = tenant_with_data
    async with AsyncSessionLocal() as session:
        svc = ConceptService(session)
        edge = await svc.create_edge(
            src_type=EdgeEndpointType.CONCEPT,
            src_id=cardio_id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=panel_id,
            relation=ConceptRelationType.ORDERS,
            tenant_id=None,
            role="SYSTEM_ADMIN",
            source=ConceptProvenance.AI,
            status=EdgeApprovalStatus.PROPOSED,
        )
        await session.commit()
        assert edge.status == EdgeApprovalStatus.PROPOSED
        default_edges = await svc.get_edges(tid, src_id=cardio_id)
        proposed_hidden = all(e.id != edge.id for e in default_edges)
        assert proposed_hidden
        all_edges = await svc.get_edges(tid, src_id=cardio_id, include_proposed=True)
        assert any(e.id == edge.id for e in all_edges)
        approved = await svc.approve_edge(edge.id, "SYSTEM_ADMIN")
        assert approved.status == EdgeApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_search_concepts_by_name(tenant_with_data):
    tid, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        results = await search_concepts(session, tid, "cardio")
        names = {c.name for c in results}
        assert "Cardiology" in names


@pytest.mark.asyncio
async def test_search_concepts_by_alias(tenant_with_data):
    tid, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        results = await search_concepts(session, tid, "heart")
        names = {c.name for c in results}
        assert "Cardiology" in names


@pytest.mark.asyncio
async def test_search_concepts_filtered_by_kind(tenant_with_data):
    tid, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        results = await search_concepts(
            session,
            tid,
            "cardio",
            kind=ConceptKind.SPECIALTY,
        )
        assert all(ConceptKind.SPECIALTY.value in c.kinds for c in results)
        assert len(results) >= 1


@pytest.mark.asyncio
async def test_search_concepts_no_match(tenant_with_data):
    tid, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        results = await search_concepts(session, tid, "zzznomatchxyz")
        assert len(results) == 0


@pytest.mark.asyncio
async def test_search_concepts_empty_query_lists_active(tenant_with_data):
    tid, *_ = tenant_with_data
    async with AsyncSessionLocal() as session:
        results = await search_concepts(
            session,
            tid,
            None,
            kind=ConceptKind.SPECIALTY,
        )
        assert len(results) >= 1
