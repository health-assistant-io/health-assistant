"""Seed identity + upsert tests (ADR 0055, program Phase 5 part A).

Pins the seed/import identity contract:

- every curated seed item carries a valid, type-matching, unique qualified
  ``seed_key``;
- curated rows get the deterministic ``uuid5(SEED_KEY_NAMESPACE,
  "<type>:<seed_key>")`` id — no random ``uuid4``;
- upsert is by id, then ``seed_key`` (``tenant_id IS NULL``) — never by
  name/slug; a rename updates display data without moving the row;
- codes update only when provided and are never fabricated;
- an explicit item ``id`` wins at creation; an id owned by a different seed
  key is rejected;
- tenant overlays with the same name/seed_key survive a system reseed;
- ``reset_curated_system_rows`` replaces system rows deterministically,
  spares tenant rows, and is idempotent — refusing (typed
  ``CuratedResetBlocked``) while any surviving row, including a tenant
  biomarker or reference range pinning a curated unit, still references a
  system row.

Isolation mirrors the other seed tests: the shared test DB persists across
runs, so synthetic payloads use uuid-suffixed seed keys and go straight to
the ``_process_*`` methods.
"""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

from app.core.catalog_identity import SEED_KEY_NAMESPACE, seed_key_uuid, validate_seed_key
from app.core.database import AsyncSessionLocal
from app.services.seed.identity import (
    SeedIdentityConflict,
    find_identity_row,
    upsert_by_identity,
)
from app.services.seed.service import SeedService
from tests.seed_utils import seed_with_commit

_SEEDS_DIR = Path(__file__).resolve().parent.parent / "data" / "seeds"


def _usuffix(base: str) -> str:
    return f"{base}-{uuid.uuid4().hex[:10]}"


# ===========================================================================
# Seed JSON identity contract
# ===========================================================================


def _iter_seed_items():
    """(file, item) for every curated item of every seeded JSON file."""
    simple = [
        "medications.json",
        "allergies.json",
        "vaccines.json",
        "concepts.json",
        "diseases.json",
        "anatomy_structures.json",
        "clinical_event_types.json",
        "biomarker_states.json",
        "biomarker_panels.json",
    ]
    for name in simple:
        data = json.loads((_SEEDS_DIR / name).read_text())
        for item in data["items"]:
            yield name, item
    catalog = json.loads((_SEEDS_DIR / "default_catalog.json").read_text())
    for item in catalog["units"] + catalog["biomarkers"]:
        yield "default_catalog.json", item
    omb = json.loads((_SEEDS_DIR / "omb_race_ethnicity.json").read_text())
    for section in ("races", "ethnicities", "languages"):
        for item in omb[section]:
            yield "omb_race_ethnicity.json", item


def test_every_seed_item_carries_a_valid_unique_seed_key():
    """The seed_key is mandatory on every curated item, valid in the qualified
    form, and unique across the whole seed set (ids derive from it)."""
    seen: dict[str, str] = {}
    for filename, item in _iter_seed_items():
        key = item.get("seed_key")
        assert key, f"{filename}: item missing seed_key: {item}"
        validate_seed_key(key)
        owner = seen.get(key)
        assert owner is None, f"seed_key {key!r} duplicated between {owner} and {filename}"
        seen[key] = filename


def test_seed_keys_match_their_stage_type():
    """Each file's keys carry the stage's type prefix (the prefix is part of
    the uuid5 identity input, so a mixed-up prefix would cross-wire ids)."""
    expected = {
        "medications.json": "medication",
        "allergies.json": "allergy",
        "vaccines.json": "vaccine",
        "concepts.json": "concept",
        "diseases.json": "concept",
        "anatomy_structures.json": "anatomy",
        "clinical_event_types.json": "clinical-event-type",
        "biomarker_states.json": "biomarker-state",
        "biomarker_panels.json": "panel-membership",
    }
    for filename, item in _iter_seed_items():
        want = expected.get(filename)
        if want:
            assert item["seed_key"].startswith(f"{want}:"), (
                f"{filename}: {item['seed_key']!r} does not carry the {want!r} prefix"
            )


# ===========================================================================
# Deterministic ids + idempotency
# ===========================================================================


@pytest.mark.asyncio
async def test_medications_seed_with_deterministic_ids_is_idempotent():
    """First run creates every row under its uuid5 id; the second run adds
    nothing and rewrites nothing (all unchanged); row counts stay stable."""
    from app.models.fhir.medication import MedicationCatalog

    svc = SeedService()
    first = await seed_with_commit(svc.seed_medications)
    assert first["errors"] == 0, first
    # Warm shared DB: earlier tests may have seeded already — every shipped
    # row must be accounted for as added or unchanged.
    assert first["added"] + first["unchanged"] >= 7, first

    second = await seed_with_commit(svc.seed_medications)
    assert second["added"] == 0, second
    assert second["updated"] == 0, f"second run rewrote rows: {second}"
    assert second["unchanged"] >= 7, f"second run lost rows: {second}"
    assert second["errors"] == 0, second

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(MedicationCatalog))).scalars().all()
        seeded = [r for r in rows if r.seed_key == "medication:metformin"]
        assert len(seeded) == 1, f"expected exactly one metformin row, got {len(seeded)}"
        derived = seed_key_uuid("medication", "metformin")
        assert seeded[0].id == derived, f"row id {seeded[0].id} != uuid5 {derived}"
        assert seeded[0].tenant_id is None
        assert seeded[0].scope.value == "system"
        assert seeded[0].created_by is None
        # Real codes from the seed (ATC), never fabricated elsewhere.
        assert seeded[0].coding_system == "atc"
        assert seeded[0].code == "A10BA02"


@pytest.mark.asyncio
async def test_seed_states_and_panels_get_deterministic_ids():
    """biomarker_states rows and panel membership edges carry uuid5 ids too."""
    from app.models.biomarker_model import BiomarkerState
    from app.models.concept_model import ConceptEdge
    from app.models.enums import ConceptRelationType

    svc = SeedService()
    states_stats = await seed_with_commit(svc.seed_biomarker_states)
    assert states_stats["errors"] == 0, states_stats

    async with AsyncSessionLocal() as session:
        positive = (
            await session.execute(select(BiomarkerState).where(BiomarkerState.slug == "positive"))
        ).scalar_one()
        assert positive.id == seed_key_uuid("biomarker-state", "positive")

    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_default_catalog)
    panels_stats = await seed_with_commit(svc.seed_biomarker_panels)
    assert panels_stats["errors"] == 0, panels_stats
    assert panels_stats["added"] + panels_stats["unchanged"] >= 7, panels_stats

    async with AsyncSessionLocal() as session:
        edges = (
            (
                await session.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.relation == ConceptRelationType.MEMBER_OF,
                        ConceptEdge.tenant_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert edges
        expected = seed_key_uuid("panel-membership", "lipid-panel/cholesterol-total")
        assert any(e.id == expected for e in edges), "panel edge id is not the uuid5 identity"


# ===========================================================================
# Upsert semantics (synthetic payloads against the process methods)
# ===========================================================================


@pytest.mark.asyncio
async def test_rename_keeps_id_and_seed_key_but_updates_name():
    """Changing a seed item's ``name`` (not its ``seed_key``) updates display
    data in place — the id and seed_key never move."""
    from app.models.fhir.medication import MedicationCatalog

    svc = SeedService()
    await seed_with_commit(svc.seed_medications)

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(MedicationCatalog).where(MedicationCatalog.seed_key == "medication:aspirin")
            )
        ).scalar_one()
        original_id = row.id

    renamed = {"seed_key": "medication:aspirin", "name": "Acetylsalicylic Acid"}
    async with AsyncSessionLocal() as session:
        stats = await svc._process_medications(session, {"items": [renamed]})
        await session.commit()

    assert stats["errors"] == 0, stats
    assert stats["added"] == 0, stats
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(MedicationCatalog).where(
                        MedicationCatalog.seed_key == "medication:aspirin"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, f"rename forked the row ({len(rows)} rows)"
        assert rows[0].id == original_id, "rename moved the row id"
        assert rows[0].name == "Acetylsalicylic Acid"

    # restore the shipped value
    async with AsyncSessionLocal() as session:
        await svc._process_medications(
            session, {"items": [{"seed_key": "medication:aspirin", "name": "Aspirin"}]}
        )
        await session.commit()


@pytest.mark.asyncio
async def test_codes_update_only_when_provided():
    """A seed item without coding fields leaves the row's codes untouched
    (no nulling, no fabrication); providing them updates both columns."""
    from app.models.fhir.medication import MedicationCatalog

    svc = SeedService()
    key = f"medication:{_usuffix('codeprobe')}"
    async with AsyncSessionLocal() as session:
        stats = await svc._process_medications(
            session,
            {
                "items": [
                    {
                        "seed_key": key,
                        "name": "Codeprobe",
                        "coding_system": "test",
                        "code": "CODEPROBE-1",
                    }
                ]
            },
        )
        await session.commit()
        assert stats["added"] == 1, stats

        # update without codes → existing codes preserved
        stats = await svc._process_medications(
            session, {"items": [{"seed_key": key, "name": "Codeprobe"}]}
        )
        await session.commit()
        row = (
            await session.execute(
                select(MedicationCatalog).where(MedicationCatalog.seed_key == key)
            )
        ).scalar_one()
        assert stats["added"] == 0 and stats["errors"] == 0, stats
        assert row.coding_system == "test" and row.code == "CODEPROBE-1"

        # update with a new code → both columns update
        stats = await svc._process_medications(
            session,
            {
                "items": [
                    {
                        "seed_key": key,
                        "name": "Codeprobe",
                        "coding_system": "test",
                        "code": "CODEPROBE-2",
                    }
                ]
            },
        )
        await session.commit()
        row = (
            await session.execute(
                select(MedicationCatalog).where(MedicationCatalog.seed_key == key)
            )
        ).scalar_one()
        assert row.code == "CODEPROBE-2"

    async with AsyncSessionLocal() as session:
        await session.execute(delete(MedicationCatalog).where(MedicationCatalog.seed_key == key))
        await session.commit()


@pytest.mark.asyncio
async def test_missing_seed_key_is_a_per_item_error():
    """A seed item without its mandatory seed_key fails that item (errors)
    without touching the rest of the batch."""
    from app.models.fhir.medication import MedicationCatalog

    svc = SeedService()
    name = f"Keyless {uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as session:
        stats = await svc._process_medications(
            session,
            {
                "items": [
                    {"name": name},
                    {"seed_key": "medication:metformin", "name": "Metformin"},
                ]
            },
        )
        await session.commit()
    assert stats["errors"] == 1, stats
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(MedicationCatalog)
            .where(MedicationCatalog.name == name)
        )
    assert count == 0, "an item without seed_key must not be written"


@pytest.mark.asyncio
async def test_explicit_id_wins_and_conflicting_id_is_rejected():
    """An explicit item ``id`` is used at creation; reusing an id that already
    belongs to a different seed_key is rejected, not silently adopted."""
    from app.models.fhir.medication import MedicationCatalog

    svc = SeedService()
    key = f"medication:{_usuffix('explicit')}"
    explicit = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        stats = await svc._process_medications(
            session,
            {"items": [{"seed_key": key, "name": "Explicit", "id": str(explicit)}]},
        )
        await session.commit()
        assert stats["added"] == 1 and stats["errors"] == 0, stats
        row = (
            await session.execute(
                select(MedicationCatalog).where(MedicationCatalog.seed_key == key)
            )
        ).scalar_one()
        assert row.id == explicit, "explicit id must win over the derived one"

        # a different seed key claiming the same explicit id is rejected
        other_key = f"medication:{_usuffix('conflict')}"
        stats = await svc._process_medications(
            session,
            {"items": [{"seed_key": other_key, "name": "Conflict", "id": str(explicit)}]},
        )
        await session.commit()
        assert stats["errors"] == 1, stats
        assert stats["added"] == 0, stats

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(MedicationCatalog).where(MedicationCatalog.seed_key.in_([key, other_key]))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_tenant_overlay_survives_system_reseed():
    """A tenant row sharing the system seed_key (and even the name) is never
    found, updated or deleted by a system seed / system-row reset."""
    from uuid import uuid4 as new_uuid

    from app.models.enums import CatalogScope
    from app.models.fhir.medication import MedicationCatalog
    from app.models.tenant_model import TenantModel

    svc = SeedService()
    await seed_with_commit(svc.seed_medications)

    tenant_id = new_uuid()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(id=tenant_id, name=f"overlay-{tenant_id}", slug=f"ovl-{tenant_id.hex[:8]}")
        )
        await session.flush()
        overlay = MedicationCatalog(
            name="Metformin",
            seed_key="medication:metformin",
            description="tenant overlay — dose guidance per local formulary",
            tenant_id=tenant_id,
            scope=CatalogScope.TENANT,
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    try:
        stats = await seed_with_commit(svc.seed_medications)
        assert stats["errors"] == 0, stats

        async with AsyncSessionLocal() as session:
            overlay = await session.get(MedicationCatalog, overlay_id)
            assert overlay is not None, "tenant overlay was deleted by the system seed"
            assert overlay.tenant_id == tenant_id
            assert overlay.description == "tenant overlay — dose guidance per local formulary"
            system_rows = (
                (
                    await session.execute(
                        select(MedicationCatalog).where(
                            MedicationCatalog.seed_key == "medication:metformin",
                            MedicationCatalog.tenant_id.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(system_rows) == 1, "system row missing alongside the overlay"
            assert system_rows[0].id != overlay_id
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(MedicationCatalog).where(MedicationCatalog.tenant_id == tenant_id)
            )
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
            await session.commit()


# ===========================================================================
# Hard reseed
# ===========================================================================


async def _clear_reseed_blockers() -> None:
    """Clear the shared test DB's leftover references into the curated
    system catalogs so a hard reseed can run.

    Other test files leave rows that would trip the reset's preflight —
    tenant catalog rows pinning system rows (RESTRICT) and instance/tenant
    rows referencing them through ``SET NULL``/``CASCADE`` FKs (catalog
    links, anatomy links, event types, unit pins, tenant concept parents,
    notification rules). They are leftover fixtures of already-finished
    tests, so clear exactly those references (CASCADE-referencing rows are
    deleted, SET NULL ones nulled — what the reset itself would do to
    them). Rows the reset deletes anyway (system catalog rows and their
    cascade children) keep their links, so the reset's generic FK walk is
    exercised against a realistic seeded install.
    """
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import update as sa_update

    from app.models.anatomy_model import AnatomyStructure
    from app.models.biomarker_model import (
        BiomarkerAllowedState,
        BiomarkerDefinition,
        BiomarkerReferenceRange,
    )
    from app.models.clinical_event import (
        ClinicalEvent,
        ClinicalEventOccurrence,
        ClinicalEventType,
        EventAnatomyLink,
    )
    from app.models.concept_model import Concept
    from app.models.doctor_model import DoctorModel
    from app.models.document_model import DocumentModel
    from app.models.examination_model import ExaminationModel
    from app.models.fhir.allergy import AllergyCatalog, AllergyIntolerance
    from app.models.fhir.medication import Medication, MedicationCatalog
    from app.models.fhir.patient import Observation
    from app.models.fhir.vaccine import PatientImmunization, VaccineCatalog
    from app.models.notification_rule import NotificationRule

    async with AsyncSessionLocal() as session:
        system_concepts = select(Concept.id).where(Concept.tenant_id.is_(None))
        system_meds = select(MedicationCatalog.id).where(MedicationCatalog.tenant_id.is_(None))
        system_allergies = select(AllergyCatalog.id).where(AllergyCatalog.tenant_id.is_(None))
        system_vaccines = select(VaccineCatalog.id).where(VaccineCatalog.tenant_id.is_(None))
        system_anatomy = select(AnatomyStructure.id).where(AnatomyStructure.tenant_id.is_(None))
        system_biomarkers = select(BiomarkerDefinition.id).where(
            BiomarkerDefinition.tenant_id.is_(None)
        )
        system_event_types = select(ClinicalEventType.id).where(
            ClinicalEventType.tenant_id.is_(None)
        )
        tenant_biomarkers = select(BiomarkerDefinition.id).where(
            BiomarkerDefinition.tenant_id.is_not(None)
        )

        # RESTRICT shapes: tenant catalog rows pinning system rows.
        await session.execute(
            sa_delete(ClinicalEventType).where(ClinicalEventType.tenant_id.is_not(None))
        )
        await session.execute(
            sa_delete(BiomarkerAllowedState).where(
                BiomarkerAllowedState.biomarker_id.in_(tenant_biomarkers)
            )
        )

        # SET NULL shapes: surviving rows referencing system rows the reset
        # deletes (units die wholesale, so any surviving unit pin dangles).
        await session.execute(
            sa_update(Medication)
            .where(Medication.medication_catalog_id.in_(system_meds))
            .values(medication_catalog_id=None)
        )
        await session.execute(
            sa_update(AllergyIntolerance)
            .where(AllergyIntolerance.allergy_catalog_id.in_(system_allergies))
            .values(allergy_catalog_id=None)
        )
        await session.execute(
            sa_update(PatientImmunization)
            .where(PatientImmunization.vaccine_catalog_id.in_(system_vaccines))
            .values(vaccine_catalog_id=None)
        )
        await session.execute(
            sa_update(ClinicalEventOccurrence)
            .where(ClinicalEventOccurrence.anatomy_id.in_(system_anatomy))
            .values(anatomy_id=None)
        )
        await session.execute(
            sa_update(ClinicalEvent)
            .where(ClinicalEvent.type_id.in_(system_event_types))
            .values(type_id=None)
        )
        await session.execute(
            sa_update(Observation)
            .where(Observation.raw_unit_id.is_not(None))
            .values(raw_unit_id=None)
        )
        await session.execute(
            sa_update(Observation)
            .where(Observation.biomarker_id.in_(system_biomarkers))
            .values(biomarker_id=None)
        )
        await session.execute(
            sa_update(DoctorModel)
            .where(DoctorModel.specialty_concept_id.in_(system_concepts))
            .values(specialty_concept_id=None)
        )
        await session.execute(
            sa_update(DocumentModel)
            .where(DocumentModel.category_concept_id.in_(system_concepts))
            .values(category_concept_id=None)
        )
        await session.execute(
            sa_update(ExaminationModel)
            .where(ExaminationModel.category_concept_id.in_(system_concepts))
            .values(category_concept_id=None)
        )
        await session.execute(
            sa_update(Concept)
            .where(
                Concept.tenant_id.is_not(None),
                Concept.parent_id.in_(system_concepts),
            )
            .values(parent_id=None)
        )
        await session.execute(
            sa_update(BiomarkerDefinition)
            .where(
                BiomarkerDefinition.tenant_id.is_not(None),
                BiomarkerDefinition.class_concept_id.in_(system_concepts),
            )
            .values(class_concept_id=None)
        )
        await session.execute(
            sa_update(BiomarkerDefinition)
            .where(
                BiomarkerDefinition.tenant_id.is_not(None),
                BiomarkerDefinition.preferred_unit_id.is_not(None),
            )
            .values(preferred_unit_id=None)
        )
        await session.execute(
            sa_update(BiomarkerReferenceRange)
            .where(
                BiomarkerReferenceRange.biomarker_id.in_(tenant_biomarkers),
                BiomarkerReferenceRange.unit_id.is_not(None),
            )
            .values(unit_id=None)
        )
        await session.execute(
            sa_update(AnatomyStructure)
            .where(
                AnatomyStructure.tenant_id.is_not(None),
                AnatomyStructure.class_concept_id.in_(system_concepts),
            )
            .values(class_concept_id=None)
        )
        for catalog in (MedicationCatalog, AllergyCatalog, VaccineCatalog):
            await session.execute(
                sa_update(catalog)
                .where(
                    catalog.tenant_id.is_not(None),
                    catalog.class_concept_id.in_(system_concepts),
                )
                .values(class_concept_id=None)
            )

        # CASCADE shapes: surviving rows the reset would destroy outright.
        await session.execute(
            sa_delete(EventAnatomyLink).where(EventAnatomyLink.anatomy_id.in_(system_anatomy))
        )
        await session.execute(
            sa_delete(NotificationRule).where(NotificationRule.biomarker_id.in_(system_biomarkers))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_hard_reseed_replaces_system_rows_and_spares_tenant_rows():
    """reset_curated_system_rows deletes only curated system rows, reseeds
    them under the same deterministic ids, and leaves tenant rows (including
    tenant edges) untouched; re-running is idempotent."""
    from uuid import uuid4 as new_uuid

    from app.models.anatomy_model import AnatomyStructure
    from app.models.biomarker_model import BiomarkerState
    from app.models.concept_model import ConceptEdge
    from app.models.enums import CatalogScope
    from app.models.fhir.medication import MedicationCatalog
    from app.models.tenant_model import TenantModel
    from app.services.seed.reset import reset_curated_system_rows

    svc = SeedService()
    # Deterministic preconditions (the shared test DB may or may not be warm):
    # seed the catalogs this test counts deletions for.
    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_medications)
    await seed_with_commit(svc.seed_allergies)
    await seed_with_commit(svc.seed_default_catalog)

    # Shared-DB hygiene: clear leftover references from already-finished
    # tests (see :func:`_clear_reseed_blockers`). The tenant rows created
    # below are the ones this test proves survive.
    await _clear_reseed_blockers()

    tenant_id = new_uuid()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(id=tenant_id, name=f"reseed-{tenant_id}", slug=f"rsd-{tenant_id.hex[:8]}")
        )
        await session.flush()
        session.add(
            MedicationCatalog(
                name="Tenant Only Drug",
                seed_key="medication:metformin",
                tenant_id=tenant_id,
                scope=CatalogScope.TENANT,
            )
        )
        session.add(
            AnatomyStructure(
                name="Tenant Organ",
                slug=f"tenant-organ-{tenant_id.hex[:8]}",
                tenant_id=tenant_id,
                scope=CatalogScope.TENANT,
            )
        )
        await session.commit()
        tenant_med_id = (
            await session.execute(
                select(MedicationCatalog.id).where(
                    MedicationCatalog.tenant_id == tenant_id,
                    MedicationCatalog.name == "Tenant Only Drug",
                )
            )
        ).scalar_one()
        tenant_organ_id = (
            await session.execute(
                select(AnatomyStructure.id).where(AnatomyStructure.tenant_id == tenant_id)
            )
        ).scalar_one()

    expected_metformin = seed_key_uuid("medication", "metformin")
    expected_positive = seed_key_uuid("biomarker-state", "positive")
    expected_mmr_edge = seed_key_uuid("vaccine-prevents", "mmr/measles")

    report = await reset_curated_system_rows()
    assert report["deleted"]["medication_catalog"] >= 7, report["deleted"]
    assert report["deleted"]["allergy_catalog"] >= 18, report["deleted"]
    assert report["deleted"]["units"] >= 7, report["deleted"]
    assert report["seeded"]["medications"]["errors"] == 0, report["seeded"]["medications"]
    assert report["seeded"]["vaccines"]["errors"] == 0, report["seeded"]["vaccines"]

    async with AsyncSessionLocal() as session:
        metformin = (
            await session.execute(
                select(MedicationCatalog).where(
                    MedicationCatalog.seed_key == "medication:metformin",
                    MedicationCatalog.tenant_id.is_(None),
                )
            )
        ).scalar_one()
        assert metformin.id == expected_metformin, "reseeded row is not the deterministic id"
        state = (
            await session.execute(select(BiomarkerState).where(BiomarkerState.slug == "positive"))
        ).scalar_one()
        assert state.id == expected_positive
        mmr_edge = (
            await session.execute(
                select(ConceptEdge).where(
                    ConceptEdge.id == expected_mmr_edge,
                    ConceptEdge.tenant_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        assert mmr_edge is not None, "the reseed recreates the vaccine PREVENTS edge"
        assert mmr_edge.source.value == "seed" and mmr_edge.status.value == "approved"

        tenant_med = await session.get(MedicationCatalog, tenant_med_id)
        assert tenant_med is not None, "tenant medication row was deleted by the reseed"
        tenant_organ = await session.get(AnatomyStructure, tenant_organ_id)
        assert tenant_organ is not None, "tenant anatomy row was deleted by the reseed"

    # Re-run: still deterministic, still idempotent.
    await reset_curated_system_rows()
    async with AsyncSessionLocal() as session:
        metformin = (
            await session.execute(
                select(MedicationCatalog).where(
                    MedicationCatalog.seed_key == "medication:metformin",
                    MedicationCatalog.tenant_id.is_(None),
                )
            )
        ).scalar_one()
        assert metformin.id == expected_metformin
        mmr_edge = (
            await session.execute(select(ConceptEdge).where(ConceptEdge.id == expected_mmr_edge))
        ).scalar_one_or_none()
        assert mmr_edge is not None, "the edge id is stable across reseeds"
        tenant_med = await session.get(MedicationCatalog, tenant_med_id)
        assert tenant_med is not None

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(MedicationCatalog).where(MedicationCatalog.tenant_id == tenant_id)
        )
        await session.execute(
            delete(AnatomyStructure).where(AnatomyStructure.tenant_id == tenant_id)
        )
        await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
        await session.commit()


@pytest.mark.asyncio
async def test_find_identity_row_rejects_an_id_owned_by_another_seed_key():
    """The helper-level contract: an id-matched row whose seed_key differs
    raises SeedIdentityConflict instead of being adopted."""
    from app.models.fhir.medication import MedicationCatalog

    key_a = f"medication:{_usuffix('owner')}"
    key_b = f"medication:{_usuffix('borrower')}"

    async with AsyncSessionLocal() as session:
        row, _ = await upsert_by_identity(session, MedicationCatalog, key_a, {"name": "Owner"})
        await session.flush()

        with pytest.raises(SeedIdentityConflict):
            await find_identity_row(session, MedicationCatalog, key_b, explicit_id=row.id)

    async with AsyncSessionLocal() as session:
        await session.execute(delete(MedicationCatalog).where(MedicationCatalog.seed_key == key_a))
        await session.commit()


@pytest.mark.asyncio
async def test_hard_reseed_refuses_when_tenant_rows_pin_system_rows():
    """A tenant clinical event type on a system category concept blocks the
    reset (typed error, nothing deleted) — tenant rows are never broken."""
    from uuid import uuid4 as new_uuid

    from sqlalchemy import delete as sa_delete

    from app.models.clinical_event import ClinicalEventType
    from app.models.concept_model import Concept, ConceptKindTag
    from app.models.enums import CatalogScope, ConceptKind, ConceptStatus
    from app.models.fhir.medication import MedicationCatalog
    from app.models.tenant_model import TenantModel
    from app.services.seed.reset import CuratedResetBlocked, reset_curated_system_rows

    await seed_with_commit(SeedService().seed_medications)

    tenant_id = new_uuid()
    concept_id = new_uuid()
    event_type_id = new_uuid()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(id=tenant_id, name=f"block-{tenant_id}", slug=f"blk-{tenant_id.hex[:8]}")
        )
        await session.flush()
        concept = Concept(
            id=concept_id,
            slug=f"event-category-{concept_id.hex[:8]}",
            name="Blocked Category",
            primary_kind=ConceptKind.EVENT_CATEGORY,
            status=ConceptStatus.ACTIVE,
            scope=CatalogScope.SYSTEM,
            aliases=[],
        )
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.EVENT_CATEGORY))
        session.add(concept)
        session.add(
            ClinicalEventType(
                id=event_type_id,
                name="Tenant Journey",
                slug=f"tenant-journey-{event_type_id.hex[:8]}",
                category_concept_id=concept_id,
                tenant_id=tenant_id,
            )
        )
        await session.commit()

    try:
        with pytest.raises(CuratedResetBlocked):
            await reset_curated_system_rows()

        # Nothing was deleted: the system catalogs are still intact.
        async with AsyncSessionLocal() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(MedicationCatalog)
                .where(MedicationCatalog.tenant_id.is_(None))
            )
            assert count >= 7, f"reset deleted rows despite refusing ({count})"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_delete(ClinicalEventType).where(ClinicalEventType.tenant_id == tenant_id)
            )
            await session.execute(sa_delete(Concept).where(Concept.id == concept_id))
            await session.execute(sa_delete(TenantModel).where(TenantModel.id == tenant_id))
            await session.commit()


@pytest.mark.asyncio
async def test_hard_reseed_refuses_when_instance_rows_reference_system_catalog():
    """An instance medication statement linked to a system catalog row blocks
    the reset (typed error naming the table, nothing deleted) — deleting the
    catalog row would SET NULL the user's link."""
    from uuid import uuid4 as new_uuid

    from sqlalchemy import delete as sa_delete

    from app.models.fhir.medication import Medication, MedicationCatalog
    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.services.seed.reset import CuratedResetBlocked, reset_curated_system_rows

    await seed_with_commit(SeedService().seed_medications)

    tenant_id, patient_id, med_row_id = new_uuid(), new_uuid(), new_uuid()
    async with AsyncSessionLocal() as session:
        metformin_id = (
            await session.execute(
                select(MedicationCatalog.id).where(
                    MedicationCatalog.seed_key == "medication:metformin",
                    MedicationCatalog.tenant_id.is_(None),
                )
            )
        ).scalar_one()
        session.add(
            TenantModel(id=tenant_id, name=f"inst-{tenant_id}", slug=f"inst-{tenant_id.hex[:8]}")
        )
        await session.flush()
        session.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Probe", "given": ["Reset"]},
                gender="UNKNOWN",
            )
        )
        await session.flush()
        session.add(
            Medication(
                id=med_row_id,
                patient_id=patient_id,
                tenant_id=tenant_id,
                code={"text": "Metformin", "catalog_id": str(metformin_id)},
                medication_catalog_id=metformin_id,
            )
        )
        await session.commit()

    try:
        with pytest.raises(CuratedResetBlocked) as excinfo:
            await reset_curated_system_rows()
        assert "fhir_medications" in str(excinfo.value)

        async with AsyncSessionLocal() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(MedicationCatalog)
                    .where(MedicationCatalog.tenant_id.is_(None))
                )
            ).scalar_one()
            assert count >= 7, f"reset deleted rows despite refusing ({count})"
            assert await session.get(Medication, med_row_id) is not None, (
                "the refusing reset must leave the instance row intact"
            )
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(sa_delete(Medication).where(Medication.id == med_row_id))
            await session.execute(sa_delete(Patient).where(Patient.id == patient_id))
            await session.execute(sa_delete(TenantModel).where(TenantModel.id == tenant_id))
            await session.commit()


@pytest.mark.asyncio
async def test_hard_reseed_refuses_when_tenant_concept_child_pins_system_parent():
    """A tenant concept whose parent is a system concept blocks the reset —
    the parent delete would SET NULL the child's parent link."""
    from uuid import uuid4 as new_uuid

    from sqlalchemy import delete as sa_delete

    from app.models.concept_model import Concept, ConceptKindTag
    from app.models.enums import CatalogScope, ConceptKind, ConceptStatus
    from app.models.fhir.medication import MedicationCatalog
    from app.models.tenant_model import TenantModel
    from app.services.seed.reset import CuratedResetBlocked, reset_curated_system_rows

    await seed_with_commit(SeedService().seed_concepts)

    tenant_id, child_id = new_uuid(), new_uuid()
    async with AsyncSessionLocal() as session:
        parent_id = (
            await session.execute(
                select(Concept.id).where(
                    Concept.seed_key == "concept:cardiology", Concept.tenant_id.is_(None)
                )
            )
        ).scalar_one()
        session.add(
            TenantModel(id=tenant_id, name=f"child-{tenant_id}", slug=f"chd-{tenant_id.hex[:8]}")
        )
        await session.flush()
        child = Concept(
            id=child_id,
            slug=f"tenant-cardio-child-{tenant_id.hex[:8]}",
            name="Tenant Cardio Child",
            primary_kind=ConceptKind.SPECIALTY,
            parent_id=parent_id,
            tenant_id=tenant_id,
            scope=CatalogScope.TENANT,
            status=ConceptStatus.ACTIVE,
            aliases=[],
        )
        child.kind_tags.append(ConceptKindTag(kind=ConceptKind.SPECIALTY))
        session.add(child)
        await session.commit()

    try:
        with pytest.raises(CuratedResetBlocked) as excinfo:
            await reset_curated_system_rows()
        assert "concepts.parent_id" in str(excinfo.value)

        async with AsyncSessionLocal() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(MedicationCatalog)
                    .where(MedicationCatalog.tenant_id.is_(None))
                )
            ).scalar_one()
            assert count >= 7, f"reset deleted rows despite refusing ({count})"
            assert await session.get(Concept, child_id) is not None
            assert (await session.get(Concept, child_id)).parent_id == parent_id, (
                "the refusing reset must leave the tenant parent link intact"
            )
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(sa_delete(Concept).where(Concept.id == child_id))
            await session.execute(sa_delete(TenantModel).where(TenantModel.id == tenant_id))
            await session.commit()


@pytest.mark.asyncio
async def test_hard_reseed_refuses_when_tenant_biomarker_pins_a_curated_unit():
    """A tenant biomarker whose ``preferred_unit_id`` points at a curated
    unit blocks the reset — the reset deletes every ``units`` row, which
    would SET NULL the tenant's unit pin (typed error, nothing deleted)."""
    from uuid import uuid4 as new_uuid

    from sqlalchemy import delete as sa_delete

    from app.models.biomarker_model import BiomarkerDefinition, Unit
    from app.models.enums import CatalogScope, QuantityType
    from app.models.tenant_model import TenantModel
    from app.services.seed.reset import CuratedResetBlocked, reset_curated_system_rows

    await seed_with_commit(SeedService().seed_default_catalog)

    tenant_id, unit_id, bio_id = new_uuid(), new_uuid(), new_uuid()
    async with AsyncSessionLocal() as session:
        units_before = (await session.execute(select(func.count()).select_from(Unit))).scalar_one()
        session.add(
            TenantModel(id=tenant_id, name=f"unit-{tenant_id}", slug=f"unt-{tenant_id.hex[:8]}")
        )
        session.add(
            Unit(
                id=unit_id,
                symbol=f"probe-unit-{unit_id.hex[:8]}",
                name="Probe Unit",
                quantity_type=QuantityType.OTHER,
            )
        )
        await session.flush()
        session.add(
            BiomarkerDefinition(
                id=bio_id,
                slug=f"tenant-pinned-bio-{tenant_id.hex[:8]}",
                name="Tenant Pinned Biomarker",
                tenant_id=tenant_id,
                scope=CatalogScope.TENANT,
                preferred_unit_id=unit_id,
            )
        )
        await session.commit()

    try:
        with pytest.raises(CuratedResetBlocked) as excinfo:
            await reset_curated_system_rows()
        assert "biomarker_definitions.preferred_unit_id" in str(excinfo.value)

        async with AsyncSessionLocal() as session:
            units_after = (
                await session.execute(select(func.count()).select_from(Unit))
            ).scalar_one()
            assert units_after == units_before + 1, (
                f"reset changed the units table despite refusing "
                f"({units_before}+probe → {units_after})"
            )
            assert await session.get(Unit, unit_id) is not None, (
                "reset deleted the pinned unit despite refusing"
            )
            bio = await session.get(BiomarkerDefinition, bio_id)
            assert bio is not None and bio.preferred_unit_id == unit_id, (
                "the refusing reset must leave the tenant unit pin intact"
            )
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_delete(BiomarkerDefinition).where(BiomarkerDefinition.id == bio_id)
            )
            await session.execute(sa_delete(Unit).where(Unit.id == unit_id))
            await session.execute(sa_delete(TenantModel).where(TenantModel.id == tenant_id))
            await session.commit()


@pytest.mark.asyncio
async def test_hard_reseed_refuses_when_tenant_reference_range_pins_a_curated_unit():
    """A reference range of a tenant biomarker pinning a curated unit blocks
    the reset — deleting the units table would SET NULL the range's unit
    (typed error, nothing deleted)."""
    from uuid import uuid4 as new_uuid

    from sqlalchemy import delete as sa_delete

    from app.models.biomarker_model import (
        BiomarkerDefinition,
        BiomarkerReferenceRange,
        Unit,
    )
    from app.models.enums import CatalogScope, QuantityType
    from app.models.tenant_model import TenantModel
    from app.services.seed.reset import CuratedResetBlocked, reset_curated_system_rows

    await seed_with_commit(SeedService().seed_default_catalog)

    tenant_id, unit_id, bio_id = new_uuid(), new_uuid(), new_uuid()
    async with AsyncSessionLocal() as session:
        units_before = (await session.execute(select(func.count()).select_from(Unit))).scalar_one()
        session.add(
            TenantModel(id=tenant_id, name=f"range-{tenant_id}", slug=f"rng-{tenant_id.hex[:8]}")
        )
        session.add(
            Unit(
                id=unit_id,
                symbol=f"probe-unit-{unit_id.hex[:8]}",
                name="Probe Unit",
                quantity_type=QuantityType.OTHER,
            )
        )
        await session.flush()
        session.add(
            BiomarkerDefinition(
                id=bio_id,
                slug=f"tenant-ranged-bio-{tenant_id.hex[:8]}",
                name="Tenant Ranged Biomarker",
                tenant_id=tenant_id,
                scope=CatalogScope.TENANT,
            )
        )
        await session.flush()
        session.add(
            BiomarkerReferenceRange(
                biomarker_id=bio_id,
                unit_id=unit_id,
                low=4.0,
                high=11.0,
            )
        )
        await session.commit()

    try:
        with pytest.raises(CuratedResetBlocked) as excinfo:
            await reset_curated_system_rows()
        assert "biomarker_reference_ranges.unit_id" in str(excinfo.value)

        async with AsyncSessionLocal() as session:
            units_after = (
                await session.execute(select(func.count()).select_from(Unit))
            ).scalar_one()
            assert units_after == units_before + 1, (
                f"reset changed the units table despite refusing "
                f"({units_before}+probe → {units_after})"
            )
            assert await session.get(Unit, unit_id) is not None, (
                "reset deleted the pinned unit despite refusing"
            )
            bio = await session.get(BiomarkerDefinition, bio_id)
            assert bio is not None, "the refusing reset must leave the tenant biomarker intact"
            assert bio.preferred_unit_id is None
            range_row = (
                await session.execute(
                    select(BiomarkerReferenceRange).where(
                        BiomarkerReferenceRange.biomarker_id == bio_id
                    )
                )
            ).scalar_one()
            assert range_row.unit_id == unit_id, (
                "the refusing reset must leave the range's unit pin intact"
            )
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_delete(BiomarkerReferenceRange).where(
                    BiomarkerReferenceRange.biomarker_id == bio_id
                )
            )
            await session.execute(
                sa_delete(BiomarkerDefinition).where(BiomarkerDefinition.id == bio_id)
            )
            await session.execute(sa_delete(Unit).where(Unit.id == unit_id))
            await session.execute(sa_delete(TenantModel).where(TenantModel.id == tenant_id))
            await session.commit()


@pytest.mark.asyncio
async def test_hard_reseed_still_resets_a_no_reference_install():
    """The curated catalog's own unit pins don't block: seeded system
    biomarkers carry ``preferred_unit_id`` (and their seeded stratified
    ranges carry ``unit_id``), but those rows are deleted by the reset
    itself — only LIVE references block. A tenant biomarker and range
    without unit pins reset cleanly, and the units table is replaced."""
    from uuid import uuid4 as new_uuid

    from sqlalchemy import delete as sa_delete

    from app.models.biomarker_model import (
        BiomarkerDefinition,
        BiomarkerReferenceRange,
    )
    from app.models.enums import CatalogScope
    from app.models.tenant_model import TenantModel
    from app.services.seed.reset import reset_curated_system_rows

    svc = SeedService()
    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_default_catalog)

    await _clear_reseed_blockers()

    tenant_id, bio_id = new_uuid(), new_uuid()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(id=tenant_id, name=f"clean-{tenant_id}", slug=f"cln-{tenant_id.hex[:8]}")
        )
        await session.flush()
        session.add(
            BiomarkerDefinition(
                id=bio_id,
                slug=f"tenant-unpinned-bio-{tenant_id.hex[:8]}",
                name="Tenant Unpinned Biomarker",
                tenant_id=tenant_id,
                scope=CatalogScope.TENANT,
            )
        )
        await session.flush()
        session.add(
            BiomarkerReferenceRange(
                biomarker_id=bio_id,
                low=1.0,
                high=2.0,
            )
        )
        await session.commit()

    try:
        report = await reset_curated_system_rows()
        assert report["deleted"]["units"] >= 7, report["deleted"]
        assert report["seeded"]["default_catalog"]["errors"] == 0, report["seeded"]

        async with AsyncSessionLocal() as session:
            survivor = await session.get(BiomarkerDefinition, bio_id)
            assert survivor is not None, "the reset deleted a tenant biomarker"
            assert survivor.preferred_unit_id is None
            range_row = (
                await session.execute(
                    select(BiomarkerReferenceRange).where(
                        BiomarkerReferenceRange.biomarker_id == bio_id
                    )
                )
            ).scalar_one()
            assert range_row is not None, "the reset deleted a tenant reference range"
            assert range_row.unit_id is None
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_delete(BiomarkerReferenceRange).where(
                    BiomarkerReferenceRange.biomarker_id == bio_id
                )
            )
            await session.execute(
                sa_delete(BiomarkerDefinition).where(BiomarkerDefinition.id == bio_id)
            )
            await session.execute(sa_delete(TenantModel).where(TenantModel.id == tenant_id))
            await session.commit()


@pytest.mark.asyncio
async def test_concept_seed_foreign_explicit_id_is_a_typed_conflict():
    """A concept item whose explicit ``id`` is already owned by a different
    seed key fails that item as a typed conflict (``SeedIdentityConflict``) —
    never a raw PK ``IntegrityError``."""

    from app.models.concept_model import Concept

    svc = SeedService()
    key_owner = f"concept:{_usuffix('idowner')}"
    key_clash = f"concept:{_usuffix('idclash')}"
    explicit = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        stats = await svc._process_concepts(
            session,
            {
                "items": [
                    {
                        "seed_key": key_owner,
                        "slug": _usuffix("id-owner"),
                        "name": "Id Owner",
                        "kinds": ["specialty"],
                        "id": str(explicit),
                    },
                    {
                        "seed_key": key_clash,
                        "slug": _usuffix("id-clash"),
                        "name": "Id Clash",
                        "kinds": ["specialty"],
                        "id": str(explicit),
                    },
                ]
            },
        )
        await session.commit()

    assert stats["added"] == 1 and stats["errors"] == 1, stats
    async with AsyncSessionLocal() as session:
        owners = (
            await session.execute(
                select(func.count()).select_from(Concept).where(Concept.id == explicit)
            )
        ).scalar_one()
        assert owners == 1, "the clashing item must not be written"
        clashes = (
            await session.execute(
                select(func.count()).select_from(Concept).where(Concept.seed_key == key_clash)
            )
        ).scalar_one()
        assert clashes == 0
        await session.execute(delete(Concept).where(Concept.seed_key == key_owner))
        await session.commit()


@pytest.mark.asyncio
async def test_seed_key_uuid_matches_frozen_namespace():
    """The uuid5 recipe itself: uuid5(SEED_KEY_NAMESPACE, "<type>:<key>")."""
    derived = seed_key_uuid("biomarker", "tsh")
    assert derived == uuid.uuid5(SEED_KEY_NAMESPACE, "biomarker:tsh")
    derived2 = seed_key_uuid("concept", "diabetes")
    assert derived2 == uuid.uuid5(SEED_KEY_NAMESPACE, "concept:diabetes")
