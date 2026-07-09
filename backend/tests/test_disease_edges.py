"""Phase 6 tests — cross-domain disease edges resolve via ``concept_edges``.

Covers:
- specialty (concept) EXAMINES disease (concept) — "which diseases does
  cardiology manage?"
- medication TREATS disease — "what treats type-2-diabetes?"
- medication CONTRAINDICATES disease — "when is this drug risky?"
- vaccine PREVENTS disease — "what does the MMR vaccine prevent?"
- All three endpoint types (concept, medication, vaccine) resolve correctly in
  the seed loader's polymorphic ``_resolve_endpoint``.
- The catalog graph ``traverse()`` follows medication→disease TREATS edges.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.concept_model import Concept, ConceptEdge
from app.models.enums import (
    ConceptRelationType,
    EdgeEndpointType,
)
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.vaccine import VaccineCatalog
from app.services.seed_service import SeedService


async def _seed_all_catalogs():
    """Run the seed stages the disease edges depend on, in pipeline order."""
    svc = SeedService()
    await svc.seed_medications()
    await svc.seed_vaccines()
    await svc.seed_body_parts()
    await svc.seed_concepts()
    await svc.seed_diseases()
    await svc.seed_concept_edges()


async def _concept_id(slug: str) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(Concept.id).where(
                    Concept.slug == slug, Concept.tenant_id.is_(None)
                )
            )
        ).scalar_one()
    return row


async def _medication_id(name: str) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import func

        row = (
            await db.execute(
                select(MedicationCatalog.id).where(
                    func.lower(MedicationCatalog.name) == name.lower(),
                    MedicationCatalog.tenant_id.is_(None),
                )
            )
        ).scalar_one()
    return row


async def _vaccine_id(slug: str) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(VaccineCatalog.id).where(
                    VaccineCatalog.slug == slug, VaccineCatalog.tenant_id.is_(None)
                )
            )
        ).scalar_one()
    return row


# ---------------------------------------------------------------------------
# specialty EXAMINES disease
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_specialty_examines_disease_edges_seeded():
    """Cardiology EXAMINES coronary-artery-disease + heart-failure."""
    await _seed_all_catalogs()
    cardio_id = await _concept_id("cardiology")
    cad_id = await _concept_id("coronary-artery-disease")

    async with AsyncSessionLocal() as db:
        edge = (
            await db.execute(
                select(ConceptEdge).where(
                    ConceptEdge.src_type == EdgeEndpointType.CONCEPT,
                    ConceptEdge.src_id == cardio_id,
                    ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                    ConceptEdge.dst_id == cad_id,
                    ConceptEdge.relation == ConceptRelationType.EXAMINES,
                    ConceptEdge.tenant_id.is_(None),
                )
            )
        ).scalar_one_or_none()
    assert edge is not None, "cardiology EXAMINES coronary-artery-disease missing"


@pytest.mark.asyncio
async def test_specialty_examines_multiple_diseases():
    """Endocrinology EXAMINES several diseases (diabetes, thyroid, metabolic)."""
    await _seed_all_catalogs()
    endo_id = await _concept_id("endocrinology")
    async with AsyncSessionLocal() as db:
        edges = (
            (
                await db.execute(
                    select(ConceptEdge.dst_id).where(
                        ConceptEdge.src_type == EdgeEndpointType.CONCEPT,
                        ConceptEdge.src_id == endo_id,
                        ConceptEdge.relation == ConceptRelationType.EXAMINES,
                        ConceptEdge.tenant_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    # Endocrinology EXAMINES body systems (endocrine-system) AND diseases. The
    # disease-targeted edges must include at least the diabetes + thyroid ones.
    t2dm = await _concept_id("type-2-diabetes")
    hypo = await _concept_id("hypothyroidism")
    assert t2dm in edges, "endocrinology should EXAMINES type-2-diabetes"
    assert hypo in edges, "endocrinology should EXAMINES hypothyroidism"


# ---------------------------------------------------------------------------
# medication TREATS disease
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_medication_treats_disease_edges_seeded():
    """Metformin TREATS type-2-diabetes (medication→concept edge)."""
    await _seed_all_catalogs()
    metformin_id = await _medication_id("Metformin")
    t2dm_id = await _concept_id("type-2-diabetes")

    async with AsyncSessionLocal() as db:
        edge = (
            await db.execute(
                select(ConceptEdge).where(
                    ConceptEdge.src_type == EdgeEndpointType.MEDICATION,
                    ConceptEdge.src_id == metformin_id,
                    ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                    ConceptEdge.dst_id == t2dm_id,
                    ConceptEdge.relation == ConceptRelationType.TREATS,
                    ConceptEdge.tenant_id.is_(None),
                )
            )
        ).scalar_one()
    assert edge.relation == ConceptRelationType.TREATS


@pytest.mark.asyncio
async def test_medication_treats_multiple_diseases():
    """Lisinopril TREATS both hypertension and heart failure."""
    await _seed_all_catalogs()
    lisinopril_id = await _medication_id("Lisinopril")
    htn_id = await _concept_id("hypertension")
    hf_id = await _concept_id("heart-failure")

    async with AsyncSessionLocal() as db:
        treated = set(
            (
                await db.execute(
                    select(ConceptEdge.dst_id).where(
                        ConceptEdge.src_type == EdgeEndpointType.MEDICATION,
                        ConceptEdge.src_id == lisinopril_id,
                        ConceptEdge.relation == ConceptRelationType.TREATS,
                        ConceptEdge.tenant_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert htn_id in treated
    assert hf_id in treated


@pytest.mark.asyncio
async def test_medication_contraindicates_disease_edges_seeded():
    """Aspirin CONTRAINDICATES peptic-ulcer."""
    await _seed_all_catalogs()
    aspirin_id = await _medication_id("Aspirin")
    ulcer_id = await _concept_id("peptic-ulcer")

    async with AsyncSessionLocal() as db:
        edge = (
            await db.execute(
                select(ConceptEdge).where(
                    ConceptEdge.src_type == EdgeEndpointType.MEDICATION,
                    ConceptEdge.src_id == aspirin_id,
                    ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                    ConceptEdge.dst_id == ulcer_id,
                    ConceptEdge.relation == ConceptRelationType.CONTRAINDICATES,
                    ConceptEdge.tenant_id.is_(None),
                )
            )
        ).scalar_one_or_none()
    assert edge is not None, "Aspirin CONTRAINDICATES peptic-ulcer missing"


# ---------------------------------------------------------------------------
# vaccine PREVENTS disease
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vaccine_prevents_disease_edges_seeded():
    """MMR PREVENTS measles, mumps, rubella (vaccine→concept edges)."""
    await _seed_all_catalogs()
    mmr_id = await _vaccine_id("mmr")
    measles_id = await _concept_id("measles")
    mumps_id = await _concept_id("mumps")
    rubella_id = await _concept_id("rubella")

    async with AsyncSessionLocal() as db:
        prevented = set(
            (
                await db.execute(
                    select(ConceptEdge.dst_id).where(
                        ConceptEdge.src_type == EdgeEndpointType.IMMUNIZATION,
                        ConceptEdge.src_id == mmr_id,
                        ConceptEdge.relation == ConceptRelationType.PREVENTS,
                        ConceptEdge.tenant_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert measles_id in prevented
    assert mumps_id in prevented
    assert rubella_id in prevented


@pytest.mark.asyncio
async def test_vaccine_prevents_cervical_cancer():
    """HPV vaccine PREVENTS both HPV infection and cervical cancer."""
    await _seed_all_catalogs()
    hpv_vac_id = await _vaccine_id("hpv")
    hpv_inf_id = await _concept_id("hpv-infection")
    cc_id = await _concept_id("cervical-cancer")

    async with AsyncSessionLocal() as db:
        prevented = set(
            (
                await db.execute(
                    select(ConceptEdge.dst_id).where(
                        ConceptEdge.src_type == EdgeEndpointType.IMMUNIZATION,
                        ConceptEdge.src_id == hpv_vac_id,
                        ConceptEdge.relation == ConceptRelationType.PREVENTS,
                        ConceptEdge.tenant_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert hpv_inf_id in prevented
    assert cc_id in prevented


# ---------------------------------------------------------------------------
# Graph traversal follows medication→disease TREATS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_traverse_follows_treats_edge():
    """``catalog_graph_service.traverse`` follows a medication→disease TREATS edge."""
    from app.services.catalog_graph_service import traverse

    await _seed_all_catalogs()
    metformin_id = await _medication_id("Metformin")

    async with AsyncSessionLocal() as db:
        graph = await traverse(
            db,
            start_type=EdgeEndpointType.MEDICATION,
            start_id=metformin_id,
            tenant_id=None,
            max_depth=1,
            relation_whitelist=(ConceptRelationType.TREATS,),
        )
    labels = {n.get("label") for n in graph.get("nodes", [])}
    assert any("diabetes" in (lbl or "").lower() for lbl in labels), (
        f"traverse from Metformin should reach type-2-diabetes; got {labels}"
    )
