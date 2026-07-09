"""Cross-catalog graph tests — Phase 2.

Covers: the new endpoint resolvers (medication/allergy/clinical_event_type),
the recursive-CTE ``catalog_graph_service.traverse`` (depth, cycles, tenant
scope, proposed-edge filter, relation whitelist, limit), and the
``GET /catalogs/{type}/{id}/relations`` meta-layer endpoint.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept, ConceptEdge
from app.models.enums import (
    ConceptProvenance,
    ConceptRelationType,
    ConceptStatus,
    EdgeApprovalStatus,
    EdgeEndpointType,
)
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.tenant_model import TenantModel
from app.services.catalog_graph_service import traverse
from app.services.concept_endpoint_resolver import resolve_endpoints


async def _tenant_and_headers(role="ADMIN"):
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="G", slug=f"g-{tenant_id}"))
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


async def _edge(
    db,
    *,
    src_type,
    src_id,
    dst_type,
    dst_id,
    relation,
    tenant_id=None,
    status=EdgeApprovalStatus.APPROVED,
):
    edge = ConceptEdge(
        src_type=src_type,
        src_id=src_id,
        dst_type=dst_type,
        dst_id=dst_id,
        relation=relation,
        tenant_id=tenant_id,
        status=status,
        source=ConceptProvenance.MANUAL,
    )
    db.add(edge)
    return edge


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_medications_and_allergies():
    async with AsyncSessionLocal() as db:
        med = MedicationCatalog(name="Resolver Med", tenant_id=None)
        alg = AllergyCatalog(name="Resolver Allergen", category="FOOD", tenant_id=None)
        db.add_all([med, alg])
        await db.commit()
        await db.refresh(med)
        await db.refresh(alg)

    async with AsyncSessionLocal() as db:
        out = await resolve_endpoints(
            db,
            [
                (EdgeEndpointType.MEDICATION, med.id),
                (EdgeEndpointType.ALLERGY, alg.id),
            ],
        )
    assert out[med.id]["type"] == "medication"
    assert out[med.id]["label"] == "Resolver Med"
    assert out[alg.id]["type"] == "allergy"
    assert out[alg.id]["label"] == "Resolver Allergen"


@pytest.mark.asyncio
async def test_resolve_fallback_for_unknown_id():
    """An unknown id still yields a fallback payload (graph never breaks)."""
    fake = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        out = await resolve_endpoints(db, [(EdgeEndpointType.MEDICATION, fake)])
    assert out[fake]["label"] == f"medication:{str(fake)[:8]}"
    assert out[fake]["kind"] is None


# ---------------------------------------------------------------------------
# catalog_graph_service.traverse
# ---------------------------------------------------------------------------


async def _build_biomarker_chain():
    """ALT (biomarker) --AFFECTS--> liver (anatomy) --...--> (no further).

    Returns (tenant_id, alt_id, liver_id, med_id, disease_concept_id).
    """
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Chain", slug=f"chain-{tenant_id}"))
        await db.commit()

    async with AsyncSessionLocal() as db:
        alt = BiomarkerDefinition(
            slug=f"alt-{uuid.uuid4().hex[:6]}", name="ALT", tenant_id=None
        )
        liver = AnatomyStructure(slug=f"liver-{uuid.uuid4().hex[:6]}", name="Liver")
        med = MedicationCatalog(name="Hepatoprotector", tenant_id=None)
        disease = Concept(
            slug=f"hepatitis-{uuid.uuid4().hex[:6]}",
            name="Hepatitis",
            primary_kind=None,
            status=ConceptStatus.ACTIVE,
            tenant_id=None,
        )
        db.add_all([alt, liver, med, disease])
        await db.commit()
        await db.refresh(alt)
        await db.refresh(liver)
        await db.refresh(med)
        await db.refresh(disease)

    async with AsyncSessionLocal() as db:
        await _edge(
            db,
            src_type=EdgeEndpointType.BIOMARKER,
            src_id=alt.id,
            dst_type=EdgeEndpointType.ANATOMY,
            dst_id=liver.id,
            relation=ConceptRelationType.AFFECTS,
        )
        await _edge(
            db,
            src_type=EdgeEndpointType.CONCEPT,
            src_id=disease.id,
            dst_type=EdgeEndpointType.ANATOMY,
            dst_id=liver.id,
            relation=ConceptRelationType.CORRELATES_WITH,
        )
        await _edge(
            db,
            src_type=EdgeEndpointType.MEDICATION,
            src_id=med.id,
            dst_type=EdgeEndpointType.CONCEPT,
            dst_id=disease.id,
            relation=ConceptRelationType.TREATS,
        )
        await db.commit()

    return tenant_id, alt.id, liver.id, med.id, disease.id


@pytest.mark.asyncio
async def test_traverse_one_hop(async_client):
    tenant_id, alt_id, liver_id, *_ = await _build_biomarker_chain()
    async with AsyncSessionLocal() as db:
        result = await traverse(
            db,
            EdgeEndpointType.BIOMARKER,
            alt_id,
            tenant_id=tenant_id,
            max_depth=1,
        )
    relations = {e["relation"] for e in result["edges"]}
    assert "AFFECTS" in relations
    node_types = {n["type"] for n in result["nodes"]}
    assert "biomarker" in node_types
    assert "anatomy" in node_types
    # depth 1 from ALT should NOT yet reach the medication (2 hops away)
    assert "medication" not in node_types


@pytest.mark.asyncio
async def test_traverse_multi_hop_reaches_medication(async_client):
    tenant_id, alt_id, liver_id, med_id, disease_id = await _build_biomarker_chain()
    async with AsyncSessionLocal() as db:
        result = await traverse(
            db,
            EdgeEndpointType.BIOMARKER,
            alt_id,
            tenant_id=tenant_id,
            max_depth=3,
        )
    node_types = {n["type"] for n in result["nodes"]}
    assert {"biomarker", "anatomy", "concept", "medication"} <= node_types
    relations = {e["relation"] for e in result["edges"]}
    assert {"AFFECTS", "CORRELATES_WITH", "TREATS"} <= relations


@pytest.mark.asyncio
async def test_traverse_relation_whitelist(async_client):
    tenant_id, alt_id, *_ = await _build_biomarker_chain()
    async with AsyncSessionLocal() as db:
        result = await traverse(
            db,
            EdgeEndpointType.BIOMARKER,
            alt_id,
            tenant_id=tenant_id,
            max_depth=3,
            relation_whitelist=(ConceptRelationType.TREATS,),
        )
    # Only TREATS edges survive the whitelist.
    assert all(e["relation"] == "TREATS" for e in result["edges"])


@pytest.mark.asyncio
async def test_traverse_cycle_safe(async_client):
    """A↔B self-referential cycle does not infinite-loop."""
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Cyc", slug=f"cyc-{tenant_id}"))
        await db.commit()
    async with AsyncSessionLocal() as db:
        a = BiomarkerDefinition(
            slug=f"cyc-a-{uuid.uuid4().hex[:6]}", name="A", tenant_id=None
        )
        b = BiomarkerDefinition(
            slug=f"cyc-b-{uuid.uuid4().hex[:6]}", name="B", tenant_id=None
        )
        db.add_all([a, b])
        await db.commit()
        await db.refresh(a)
        await db.refresh(b)
    async with AsyncSessionLocal() as db:
        await _edge(
            db,
            src_type=EdgeEndpointType.BIOMARKER,
            src_id=a.id,
            dst_type=EdgeEndpointType.BIOMARKER,
            dst_id=b.id,
            relation=ConceptRelationType.CORRELATES_WITH,
        )
        await _edge(
            db,
            src_type=EdgeEndpointType.BIOMARKER,
            src_id=b.id,
            dst_type=EdgeEndpointType.BIOMARKER,
            dst_id=a.id,
            relation=ConceptRelationType.CORRELATES_WITH,
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        result = await traverse(
            db,
            EdgeEndpointType.BIOMARKER,
            a.id,
            tenant_id=tenant_id,
            max_depth=3,
        )
    # Terminates; edges are deduped.
    edge_ids = {e["id"] for e in result["edges"]}
    assert len(edge_ids) == len(result["edges"])


@pytest.mark.asyncio
async def test_traverse_proposed_edges_hidden_by_default(async_client):
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="P", slug=f"p-{tenant_id}"))
        await db.commit()
    async with AsyncSessionLocal() as db:
        a = BiomarkerDefinition(
            slug=f"pa-{uuid.uuid4().hex[:6]}", name="PA", tenant_id=None
        )
        b = AnatomyStructure(slug=f"pb-{uuid.uuid4().hex[:6]}", name="PB")
        db.add_all([a, b])
        await db.commit()
        await db.refresh(a)
        await db.refresh(b)
    async with AsyncSessionLocal() as db:
        await _edge(
            db,
            src_type=EdgeEndpointType.BIOMARKER,
            src_id=a.id,
            dst_type=EdgeEndpointType.ANATOMY,
            dst_id=b.id,
            relation=ConceptRelationType.AFFECTS,
            status=EdgeApprovalStatus.PROPOSED,
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        default = await traverse(
            db, EdgeEndpointType.BIOMARKER, a.id, tenant_id=tenant_id
        )
        with_proposed = await traverse(
            db,
            EdgeEndpointType.BIOMARKER,
            a.id,
            tenant_id=tenant_id,
            include_proposed=True,
        )
    assert default["edges"] == []
    assert len(with_proposed["edges"]) == 1


@pytest.mark.asyncio
async def test_traverse_rejects_bad_depth(async_client):
    async with AsyncSessionLocal() as db:
        with pytest.raises(ValueError):
            await traverse(
                db,
                EdgeEndpointType.BIOMARKER,
                uuid.uuid4(),
                tenant_id=None,
                max_depth=0,
            )
        with pytest.raises(ValueError):
            await traverse(
                db,
                EdgeEndpointType.BIOMARKER,
                uuid.uuid4(),
                tenant_id=None,
                max_depth=9,
            )


# ---------------------------------------------------------------------------
# GET /catalogs/{type}/{id}/relations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relations_endpoint(async_client):
    tenant_id, headers = await _tenant_and_headers("ADMIN")
    _, alt_id, *_ = await _build_biomarker_chain()
    resp = await async_client.get(
        f"/api/v1/catalogs/biomarker/{alt_id}/relations?depth=2",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["start"]["type"] == "biomarker"
    relations = {e["relation"] for e in body["edges"]}
    assert "AFFECTS" in relations


@pytest.mark.asyncio
async def test_relations_endpoint_relation_whitelist(async_client):
    tenant_id, headers = await _tenant_and_headers("ADMIN")
    _, alt_id, *_ = await _build_biomarker_chain()
    resp = await async_client.get(
        f"/api/v1/catalogs/biomarker/{alt_id}/relations?depth=3&relation=TREATS",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert all(e["relation"] == "TREATS" for e in resp.json()["edges"])


@pytest.mark.asyncio
async def test_relations_endpoint_invalid_relation_400(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    alt_id = uuid.uuid4()
    resp = await async_client.get(
        f"/api/v1/catalogs/biomarker/{alt_id}/relations?relation=BOGUS",
        headers=headers,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Migration: class_concept_id columns present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_class_concept_id_columns_exist():
    """The Phase 2 migration added class_concept_id to both catalog tables."""
    from sqlalchemy import inspect as sa_inspect

    async with AsyncSessionLocal() as db:
        conn = await db.connection()
        med_cols = await conn.run_sync(
            lambda sync_conn: {
                c["name"]
                for c in sa_inspect(sync_conn).get_columns("medication_catalog")
            }
        )
        alg_cols = await conn.run_sync(
            lambda sync_conn: {
                c["name"] for c in sa_inspect(sync_conn).get_columns("allergy_catalog")
            }
        )
    assert "class_concept_id" in med_cols
    assert "class_concept_id" in alg_cols


@pytest.mark.asyncio
async def test_affects_relation_persistable():
    """An AFFECTS edge round-trips through the DB (enum value exists)."""
    tenant_id, alt_id, liver_id, *_ = await _build_biomarker_chain()
    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.relation == ConceptRelationType.AFFECTS
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any(e.src_id == alt_id and e.dst_id == liver_id for e in rows)
