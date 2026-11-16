"""Auto-create catalog writers persist ``scope=TENANT`` with the caller's tenant.

A NULL ``scope`` slips past the 0010 CHECKs (``NULL <> 'system'`` is
NULL-safe), so "no CHECK violation" proves nothing — each writer is asserted
on the persisted ``scope`` value and ``tenant_id`` itself (ADR 0055: writers
that stamp ``tenant_id`` stamp the tenant scope too). Covers the AI pipeline
(ontology biomarkers/medications, examination-category concept), the import
restore writers (concepts, event categories, medication/allergy catalogs);
the ``fhir_service.map_observations_to_biomarkers`` auto-create is asserted in
``test_biomarker_mapping_tenant_scope.py``.
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.ai.pipeline.ontology import process_unknown_biomarkers, process_unknown_medications
from app.ai.pipeline.service import MedicalProcessingService
from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept
from app.models.enums import CatalogScope, ConceptKind
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.tenant_model import TenantModel
from app.services.imports.service import ImportService


async def _new_tenant(db) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(TenantModel(id=tid, name="Writer Scope", slug=f"writer-scope-{tid.hex[:8]}"))
    await db.commit()
    return tid


class _FakeNlpExtractor:
    """The slice of the NLP extractor the ontology Pass-2 handlers consume."""

    def __init__(self, bio_slug: str, med_name: str):
        self.bio_slug = bio_slug
        self.med_name = med_name

    async def parse_document_pass_2_biomarkers(self, unknown_bios):
        return SimpleNamespace(
            definitions=[
                SimpleNamespace(
                    raw_name_match="Scope Bio",
                    proposed_slug=self.bio_slug,
                    name="Scope Bio",
                    proposed_coding_system="custom",
                    proposed_code=None,
                    category="",
                    suggested_aliases=[],
                    reference_range_min=None,
                    reference_range_max=None,
                    preferred_unit_symbol=None,
                    info=None,
                    is_telemetry=False,
                )
            ]
        )

    async def parse_document_pass_2_medications(self, unknown_meds):
        return SimpleNamespace(
            definitions=[
                SimpleNamespace(
                    raw_name_match=self.med_name,
                    name=self.med_name,
                    description=None,
                    indications=None,
                    side_effects=None,
                    contraindications=None,
                    dosage_info=None,
                )
            ]
        )


@pytest.mark.asyncio
async def test_ai_pipeline_biomarker_writer_stamps_tenant_scope():
    async with AsyncSessionLocal() as db:
        tid = await _new_tenant(db)
        bio_slug = f"scope-bio-{uuid.uuid4().hex[:8]}"
        slug_map: dict = {}
        await process_unknown_biomarkers(
            db, [{"text": "Scope Bio"}], _FakeNlpExtractor(bio_slug, "x"), tid, slug_map
        )
        await db.commit()
        assert slug_map["Scope Bio"] == bio_slug
        row = (
            await db.execute(
                select(BiomarkerDefinition).where(BiomarkerDefinition.slug == bio_slug)
            )
        ).scalar_one()
        assert row.scope == CatalogScope.TENANT
        assert row.tenant_id == tid


@pytest.mark.asyncio
async def test_ai_pipeline_medication_writer_stamps_tenant_scope():
    async with AsyncSessionLocal() as db:
        tid = await _new_tenant(db)
        med_name = f"Scope Med {uuid.uuid4().hex[:8]}"
        name_map: dict = {}
        await process_unknown_medications(
            db, [{"text": med_name}], _FakeNlpExtractor("x", med_name), tid, name_map
        )
        await db.commit()
        assert name_map[med_name] == med_name
        row = (
            await db.execute(select(MedicationCatalog).where(MedicationCatalog.name == med_name))
        ).scalar_one()
        assert row.scope == CatalogScope.TENANT
        assert row.tenant_id == tid


@pytest.mark.asyncio
async def test_medical_processing_category_writer_stamps_tenant_scope():
    async with AsyncSessionLocal() as db:
        tid = await _new_tenant(db)
        category_input = f"Sleep Quality {uuid.uuid4().hex[:8]}"
        svc = MedicalProcessingService(db)
        concept = await svc.resolve_category(category_input, tid)
        await db.commit()
        row = (await db.execute(select(Concept).where(Concept.id == concept.id))).scalar_one()
        assert row.scope == CatalogScope.TENANT
        assert row.tenant_id == tid


@pytest.mark.asyncio
async def test_import_concepts_writer_stamps_tenant_scope():
    async with AsyncSessionLocal() as db:
        tid = await _new_tenant(db)
        src_id = str(uuid.uuid4())
        payload = {
            "concepts": [
                {
                    "id": src_id,
                    "slug": f"scope-concept-{uuid.uuid4().hex[:8]}",
                    "name": "Scope Concept",
                    "kinds": [ConceptKind.EXAMINATION_CATEGORY.value],
                    "primary_kind": ConceptKind.EXAMINATION_CATEGORY.value,
                }
            ]
        }
        id_remap: dict[str, str] = {}
        count = await ImportService(db)._restore_concepts(payload, tid, id_remap)
        await db.commit()
        assert count == 1
        row = (
            await db.execute(select(Concept).where(Concept.id == uuid.UUID(id_remap[src_id])))
        ).scalar_one()
        assert row.scope == CatalogScope.TENANT
        assert row.tenant_id == tid


@pytest.mark.asyncio
async def test_sidecar_event_category_writer_stamps_tenant_scope():
    async with AsyncSessionLocal() as db:
        tid = await _new_tenant(db)
        slug = f"scope-event-cat-{uuid.uuid4().hex[:8]}"
        payload = {"categories": [{"slug": slug, "name": "Scope Event Cat"}], "types": []}
        count = await ImportService(db)._restore_clinical_event_types(payload, tid)
        await db.commit()
        assert count == 1
        row = (await db.execute(select(Concept).where(Concept.slug == slug))).scalar_one()
        assert row.scope == CatalogScope.TENANT
        assert row.tenant_id == tid


@pytest.mark.asyncio
async def test_import_medication_catalog_writer_stamps_tenant_scope():
    async with AsyncSessionLocal() as db:
        tid = await _new_tenant(db)
        name = f"Scope Restored Med {uuid.uuid4().hex[:8]}"
        count, errors = await ImportService(db)._restore_medication_catalog(
            {"medications": [{"name": name}]}, tid
        )
        assert errors == []
        await db.commit()
        assert count == 1
        row = (
            await db.execute(
                select(MedicationCatalog).where(
                    MedicationCatalog.name == name, MedicationCatalog.tenant_id == tid
                )
            )
        ).scalar_one()
        assert row.scope == CatalogScope.TENANT
        assert row.tenant_id == tid


@pytest.mark.asyncio
async def test_import_allergy_catalog_writer_stamps_tenant_scope():
    async with AsyncSessionLocal() as db:
        tid = await _new_tenant(db)
        name = f"Scope Restored Allergy {uuid.uuid4().hex[:8]}"
        count, errors = await ImportService(db)._restore_allergy_catalog(
            {"allergies": [{"name": name}]}, tid
        )
        assert errors == []
        await db.commit()
        assert count == 1
        row = (
            await db.execute(
                select(AllergyCatalog).where(
                    AllergyCatalog.name == name, AllergyCatalog.tenant_id == tid
                )
            )
        ).scalar_one()
        assert row.scope == CatalogScope.TENANT
        assert row.tenant_id == tid


@pytest.mark.asyncio
async def test_ai_pipeline_medication_dedupe_prefers_the_tenant_row():
    """F6: a system row and a tenant overlay sharing a name dedupe cleanly.

    The old ``scalar_one_or_none`` over ``(tenant OR system)`` raised
    ``MultipleResultsFound`` once both existed, aborting Pass-2 extraction.
    The tenant row now wins and the lookup is total.
    """
    async with AsyncSessionLocal() as db:
        tid = await _new_tenant(db)
        med_name = f"Shared Med {uuid.uuid4().hex[:8]}"
        db.add_all(
            [
                MedicationCatalog(
                    name=med_name,
                    description="system value",
                    tenant_id=None,
                    scope=CatalogScope.SYSTEM,
                ),
                MedicationCatalog(
                    name=med_name,
                    description="tenant value",
                    tenant_id=tid,
                    scope=CatalogScope.TENANT,
                ),
            ]
        )
        await db.commit()

        name_map: dict = {}
        await process_unknown_medications(
            db, [{"text": med_name}], _FakeNlpExtractor("x", med_name), tid, name_map
        )
        await db.commit()

        rows = (
            (await db.execute(select(MedicationCatalog).where(MedicationCatalog.name == med_name)))
            .scalars()
            .all()
        )
        assert len(rows) == 2, "dedupe must not create a third row"
        assert name_map[med_name] == med_name
