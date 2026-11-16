"""Catalog cleanup — the retired legacy surface stays retired (ADR 0055 D8).

Pins the Phase-4 removals: the biomarker ``category`` property and its
API/schema projection, the duplicate ``is_custom`` ownership flag (``scope`` +
``tenant_id`` is the one representation), the dead descriptor FHIR projector,
and the single biomarker serializer (one row shape from every entry point).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import ClinicalEventType
from app.models.concept_model import Concept
from app.models.enums import (
    ConceptKind,
    ConceptRelationType,
    EdgeApprovalStatus,
    EdgeEndpointType,
)


def test_biomarker_category_property_is_gone():
    assert not hasattr(BiomarkerDefinition, "category")


def test_biomarker_write_schemas_reject_category():
    from pydantic import ValidationError

    from app.schemas.biomarker import BiomarkerCreate, BiomarkerUpdate

    with pytest.raises(ValidationError):
        BiomarkerCreate(slug="x", name="X", category="blood_laboratory")
    with pytest.raises(ValidationError):
        BiomarkerUpdate(category="blood_laboratory")


def test_is_custom_is_not_a_model_attribute_anywhere():
    from app.models.anatomy_model import AnatomyStructure
    from app.models.fhir.allergy import AllergyCatalog
    from app.models.fhir.medication import MedicationCatalog
    from app.models.fhir.vaccine import VaccineCatalog

    for model in (MedicationCatalog, AllergyCatalog, VaccineCatalog, AnatomyStructure):
        assert not hasattr(model, "is_custom"), model.__name__


def test_descriptors_carry_no_duplicate_fhir_projector():
    """FHIR projection lives on the model (``to_fhir_dict``), consumed by the
    facade — the descriptor duplicated it and nothing read it."""
    from app.catalogs.descriptors import CatalogDescriptor

    assert "fhir_projector" not in CatalogDescriptor.__dataclass_fields__


@pytest.mark.asyncio
async def test_anatomy_is_custom_column_is_dropped():
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'anatomy_structures'"
                )
            )
        ).all()
    assert "is_custom" not in {row[0] for row in rows}


@pytest.mark.asyncio
async def test_catalog_responses_have_no_is_custom_key(system_admin_headers):
    from app.catalogs.registry import CatalogRegistry

    async with AsyncSessionLocal() as db:
        for catalog_type in ("medication", "allergy", "vaccine", "anatomy"):
            descriptor = CatalogRegistry.get(catalog_type)
            page = await descriptor.service.list(db, None, limit=1)
            for item in page["items"]:
                assert "is_custom" not in item, catalog_type
                assert "scope" in item, catalog_type


@pytest.mark.asyncio
async def test_clinical_event_biomarkers_use_the_one_serializer(async_client, system_admin_headers):
    """``GET /clinical-events/types/{id}/biomarkers`` no longer hand-builds a
    row dict: it emits the unified biomarker serializer's shape."""
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        category = Concept(
            slug=f"cleanup-event-cat-{suffix}",
            name="Cleanup Event Category",
            primary_kind=ConceptKind.EVENT_CATEGORY,
            scope="system",
        )
        cls = Concept(
            slug=f"cleanup-bio-class-{suffix}",
            name="Cleanup Bio Class",
            primary_kind=ConceptKind.BIOMARKER_CLASS,
            scope="system",
        )
        db.add_all([category, cls])
        await db.flush()
        event_type = ClinicalEventType(
            name="Cleanup Event",
            slug=f"cleanup-event-{suffix}",
            category_concept_id=category.id,
        )
        bio = BiomarkerDefinition(
            slug=f"cleanup-bio-{suffix}",
            name="Cleanup Biomarker",
            class_concept_id=cls.id,
            scope="system",
        )
        db.add_all([event_type, bio])
        await db.flush()
        from app.models.concept_model import ConceptEdge

        db.add(
            ConceptEdge(
                src_type=EdgeEndpointType.BIOMARKER,
                src_id=bio.id,
                dst_type=EdgeEndpointType.CLINICAL_EVENT_TYPE,
                dst_id=event_type.id,
                relation=ConceptRelationType.MONITORS,
                status=EdgeApprovalStatus.APPROVED,
            )
        )
        await db.commit()
        type_id, bio_id = event_type.id, bio.id

    resp = await async_client.get(
        f"/api/v1/clinical-events/types/{type_id}/biomarkers", headers=system_admin_headers
    )
    assert resp.status_code == 200, resp.text
    rows = {row["id"]: row for row in resp.json()}
    assert str(bio_id) in rows
    row = rows[str(bio_id)]
    assert row["class_concept_slug"] == f"cleanup-bio-class-{suffix}"
    assert "category" not in row
    assert row["coding_system"] == "loinc"


@pytest.mark.asyncio
async def test_biomarker_api_rejects_category_and_omits_it(async_client, system_admin_headers):
    suffix = uuid.uuid4().hex[:8]
    rejected = await async_client.post(
        "/api/v1/biomarkers/",
        json={"slug": f"cleanup-{suffix}", "name": "Cleanup", "category": "blood_laboratory"},
        headers=system_admin_headers,
    )
    assert rejected.status_code == 422

    created = await async_client.post(
        "/api/v1/biomarkers/",
        json={"slug": f"cleanup-{suffix}", "name": "Cleanup"},
        headers=system_admin_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert "category" not in body
    assert body["class_concept_slug"] is None
