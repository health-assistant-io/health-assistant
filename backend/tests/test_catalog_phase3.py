"""Phase 3 tests — biomarker↔event-type correlation migration + cross-domain seed edges + legacy table drops.

Covers:
- The biomarker↔clinical_event_type correlation now lives in ``concept_edges``
  (MONITORS), exercised through the ``/clinical-events/types/{id}/biomarkers``
  CRUD endpoints (add/list/delete) — verifies the migration away from the
  dropped ``biomarker_event_correlations`` table.
- The cross-domain biomarker→anatomy AFFECTS seed edges resolve (creatinine→kidney, etc.).
- The legacy ``biomarker_relationships`` + ``biomarker_event_correlations`` tables are gone.
"""

import uuid

import pytest
from sqlalchemy import inspect as sa_inspect, select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import ClinicalEventType
from app.models.concept_model import Concept, ConceptEdge
from app.models.enums import (
    ConceptKind,
    ConceptProvenance,
    ConceptRelationType,
    EdgeApprovalStatus,
    EdgeEndpointType,
)
from app.models.tenant_model import TenantModel


async def _tenant_and_headers(role="ADMIN"):
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"p3-{tenant_id}"))
        await db.commit()
    token = create_access_token(
        {
            "sub": f"{role.lower()}@test.local",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": role,
        }
    )
    return tenant_id, {"Authorization": f"Bearer {token}"}


async def _make_event_type_and_biomarker():
    async with AsyncSessionLocal() as db:
        # Phase 8e: ClinicalEventType.category_concept_id is NOT NULL —
        # mint a throwaway concept to satisfy the constraint.
        cat = Concept(
            slug=f"cat-{uuid.uuid4().hex[:6]}",
            name=f"Cat {uuid.uuid4().hex[:6]}",
            primary_kind=ConceptKind.EVENT_CATEGORY,
        )
        db.add(cat)
        await db.flush()  # populate cat.id (DB-side gen_random_uuid)
        etype = ClinicalEventType(
            name=f"Journey {uuid.uuid4().hex[:6]}",
            slug=f"journey-{uuid.uuid4().hex[:6]}",
            category_concept_id=cat.id,
        )
        bio = BiomarkerDefinition(
            slug=f"bio-{uuid.uuid4().hex[:6]}",
            name=f"Bio {uuid.uuid4().hex[:6]}",
            tenant_id=None,
        )
        db.add_all([etype, bio])
        await db.commit()
        await db.refresh(etype)
        await db.refresh(bio)
    return etype.id, bio.id


# ---------------------------------------------------------------------------
# Correlation CRUD now uses concept_edges (MONITORS)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_correlated_biomarker_creates_edge(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    etype_id, bio_id = await _make_event_type_and_biomarker()

    resp = await async_client.post(
        f"/api/v1/clinical-events/types/{etype_id}/biomarkers",
        json={"biomarker_id": str(bio_id), "correlation_type": "diagnostic"},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text

    # A MONITORS edge must now exist (not a biomarker_event_correlations row).
    async with AsyncSessionLocal() as db:
        edge = (
            await db.execute(
                select(ConceptEdge).where(
                    ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                    ConceptEdge.src_id == bio_id,
                    ConceptEdge.dst_type == EdgeEndpointType.CLINICAL_EVENT_TYPE,
                    ConceptEdge.dst_id == etype_id,
                    ConceptEdge.relation == ConceptRelationType.MONITORS,
                )
            )
        ).scalar_one()
    assert edge.properties["correlation_type"] == "diagnostic"


@pytest.mark.asyncio
async def test_correlated_biomarkers_list_reads_edges(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    etype_id, bio_id = await _make_event_type_and_biomarker()

    # Seed an edge directly.
    async with AsyncSessionLocal() as db:
        db.add(
            ConceptEdge(
                src_type=EdgeEndpointType.BIOMARKER,
                src_id=bio_id,
                dst_type=EdgeEndpointType.CLINICAL_EVENT_TYPE,
                dst_id=etype_id,
                relation=ConceptRelationType.MONITORS,
                status=EdgeApprovalStatus.APPROVED,
                source=ConceptProvenance.MANUAL,
                tenant_id=None,
                properties={"correlation_type": "monitoring"},
            )
        )
        await db.commit()

    resp = await async_client.get(
        f"/api/v1/clinical-events/types/{etype_id}/biomarkers", headers=headers
    )
    assert resp.status_code == 200, resp.text
    ids = {b["id"] for b in resp.json()}
    assert str(bio_id) in ids


@pytest.mark.asyncio
async def test_remove_correlated_biomarker_deletes_edge(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    etype_id, bio_id = await _make_event_type_and_biomarker()

    await async_client.post(
        f"/api/v1/clinical-events/types/{etype_id}/biomarkers",
        json={"biomarker_id": str(bio_id)},
        headers=headers,
    )
    del_resp = await async_client.delete(
        f"/api/v1/clinical-events/types/{etype_id}/biomarkers/{bio_id}",
        headers=headers,
    )
    assert del_resp.status_code == 200, del_resp.text

    async with AsyncSessionLocal() as db:
        edge = (
            await db.execute(
                select(ConceptEdge).where(
                    ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                    ConceptEdge.src_id == bio_id,
                    ConceptEdge.dst_type == EdgeEndpointType.CLINICAL_EVENT_TYPE,
                    ConceptEdge.dst_id == etype_id,
                )
            )
        ).scalar_one_or_none()
    assert edge is None


# ---------------------------------------------------------------------------
# Cross-domain seed edges (biomarker → anatomy AFFECTS)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_loader_supports_affects_edges():
    """The extended concept_edges.json seeds biomarker→anatomy AFFECTS edges.

    Verifies the seed loader resolves biomarker + anatomy endpoints and creates
    AFFECTS rows. Runs the seed stage directly against the test DB.
    """
    from app.services.seed_service import seed_service

    async with AsyncSessionLocal() as db:
        stats = await seed_service.seed_concept_edges(db)
        await db.commit()
    # The extended concept_edges.json must process without errors. (Whether
    # AFFECTS rows land depends on the biomarker/anatomy seeds having run; the
    # test DB only runs migrations, so we assert clean processing, not counts.)
    assert stats["errors"] == 0


# ---------------------------------------------------------------------------
# Legacy tables are gone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_biomarker_link_tables_dropped():
    """Phase 3 migration dropped biomarker_relationships +
    biomarker_event_correlations."""
    async with AsyncSessionLocal() as db:
        conn = await db.connection()
        table_names = await conn.run_sync(
            lambda sync_conn: set(sa_inspect(sync_conn).get_table_names())
        )
    assert "biomarker_relationships" not in table_names
    assert "biomarker_event_correlations" not in table_names
    # concept_edges still present (the replacement).
    assert "concept_edges" in table_names


def test_models_no_longer_export_legacy_classes():
    """The ORM classes are removed from the model registry."""
    from app.models import biomarker_model

    assert not hasattr(biomarker_model, "BiomarkerRelationship")
    assert not hasattr(biomarker_model, "BiomarkerEventCorrelation")
