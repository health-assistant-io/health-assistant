"""The ``user_modified`` wiring — every human write path flips the flag,
seed/applier writes never do (ADR 0056 assumption 5).

Covered doors:

* the unified catalog write service (medication create + update — the same
  functions the generic ``/catalogs/{type}`` meta-layer and the domain
  routes both call), including the anatomy structure PATCH (the atlas
  marker path) through ``anatomy_service``,
* the anatomy graph import,
* the HITL integration proposal apply (biomarker + concept creates),
* the biomarker units create (``POST /biomarkers/units`` — a human write
  onto the pack-seeded unit key space),
* a seed-stage write leaves ``user_modified`` false (the applier never
  flags).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.extensions.sdk.catalog import CatalogProposal
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept
from app.models.fhir.medication import MedicationCatalog
from app.schemas.anatomy import AnatomyStructureCreate, AnatomyStructureUpdate
from app.schemas.medication import MedicationCatalogCreate, MedicationCatalogUpdate
from app.services.anatomy.anatomy_import_service import AnatomyImportService
from app.services.anatomy.anatomy_service import (
    create_anatomy_structure,
    update_anatomy_structure,
)
from app.services.catalog.catalog_proposal_service import apply_proposal
from app.services.catalog.catalog_write_service import (
    create_medication,
    update_medication,
)
from app.services.seed.provenance import (
    entity_type_for,
    get_provenance,
    mark_user_modified_for_model,
)
from app.services.seed.service import SeedService
from tests._auth_helpers import create_user
from tests.seed_lane import provenance_for


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


def actor_for(user) -> SimpleNamespace:
    return SimpleNamespace(user_id=user.id, tenant_id=None, role=user.role)


@pytest.mark.asyncio
async def test_medication_create_and_update_flip_user_modified(db):
    """The unified write service flips the flag on create AND update — the
    flag row is created for user-authored items (no pack provenance yet)."""
    user = await create_user(role="SYSTEM_ADMIN")
    actor = actor_for(user)
    suffix = __import__("uuid").uuid4().hex[:8]
    created = await create_medication(
        db,
        actor,
        MedicationCatalogCreate(
            name=f"UM Probe {suffix}",
            description="probe",
        ),
    )
    row_id = created["id"]
    prov = await provenance_for(db, "medication", row_id)
    assert prov is not None and prov.user_modified is True
    assert prov.source_version_id is None, "a user-authored row has no pack provenance"

    await update_medication(db, actor, row_id, MedicationCatalogUpdate(description="edited"))
    prov = await provenance_for(db, "medication", row_id)
    await db.refresh(prov)
    assert prov.user_modified is True

    await db.delete(prov)
    row = await db.get(MedicationCatalog, row_id)
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_anatomy_structure_patch_flips_user_modified(db):
    """The anatomy structure PATCH (the atlas marker path — markers live in
    ``display.map.markers``) flips the flag via ``anatomy_service``."""
    user = await create_user(role="SYSTEM_ADMIN")
    slug = f"um-probe-{__import__('uuid').uuid4().hex[:8]}"
    structure = await create_anatomy_structure(
        db,
        AnatomyStructureCreate(slug=slug, name="UM Probe"),
        role=user.role,
        tenant_id=None,
        user_id=user.id,
    )
    prov = await provenance_for(db, "anatomy", structure.id)
    assert prov is not None and prov.user_modified is True

    await update_anatomy_structure(
        db,
        structure,
        AnatomyStructureUpdate(display={"map": {"markers": {}}}),
        tenant_id=None,
    )
    prov = await provenance_for(db, "anatomy", structure.id)
    await db.refresh(prov)
    assert prov.user_modified is True

    await db.delete(prov)
    await db.delete(structure)
    await db.commit()


@pytest.mark.asyncio
async def test_anatomy_graph_import_flips_user_modified(db):
    slug = f"um-import-{__import__('uuid').uuid4().hex[:8]}"
    from app.schemas.anatomy_import import AnatomyImportNode, AnatomyImportPayload

    service = AnatomyImportService(db)
    stats = await service.import_graph(
        AnatomyImportPayload(
            nodes=[AnatomyImportNode(slug=slug, name="Import Probe")],
            edges=[],
        )
    )
    assert stats["nodes_added"] == 1
    row = (
        await db.execute(select(AnatomyStructure).where(AnatomyStructure.slug == slug))
    ).scalar_one()
    prov = await provenance_for(db, "anatomy", row.id)
    assert prov is not None and prov.user_modified is True

    await db.delete(prov)
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_hitl_proposal_apply_flips_user_modified(db):
    """Applying an integration catalog proposal (the HITL create) flips the
    flag on the created biomarker."""
    user = await create_user(role="SYSTEM_ADMIN")
    actor = actor_for(user)
    slug = f"um-proposal-{__import__('uuid').uuid4().hex[:8]}"
    proposal = CatalogProposal(
        kind="biomarker",
        payload={
            "name": "UM Proposal Probe",
            "slug": slug,
            "coding_system": "custom",
            "class_concept_slug": "general",
        },
    )
    result = await apply_proposal(db, actor, integration=None, proposal=proposal)
    assert result.created
    prov = await provenance_for(db, "biomarker", result.entity_id)
    assert prov is not None and prov.user_modified is True

    row = await db.get(BiomarkerDefinition, result.entity_id)
    await db.delete(prov)
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_seed_stage_write_leaves_user_modified_false(db):
    """A seeder upsert (the applier's write path) never flags the row."""
    item = {
        "seed_key": f"medication:um-seed-{__import__('uuid').uuid4().hex[:8]}",
        "name": "UM Seed Probe",
    }
    async with AsyncSessionLocal() as session:
        await SeedService().seed_medications(session, [item])
        await session.commit()
    row = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == item["seed_key"])
        )
    ).scalar_one()
    prov = await provenance_for(db, "medication", row.id)
    assert prov is None or prov.user_modified is False

    async with AsyncSessionLocal() as session:
        fresh = await session.get(MedicationCatalog, row.id)
        p = await get_provenance(session, entity_type_for(MedicationCatalog), row.id)
        if p is not None:
            await session.delete(p)
        await session.delete(fresh)
        await session.commit()


@pytest.mark.asyncio
async def test_unit_create_flips_user_modified(db):
    """``POST /biomarkers/units`` (a human write onto the pack-seeded unit
    key space) flips the flag — the created row is never auto-updated by a
    later pack targeting the same symbol."""
    from app.api.v1.endpoints.biomarkers import create_unit
    from app.models.biomarker_model import Unit
    from app.models.enums import QuantityType
    from app.schemas.biomarker import UnitCreate

    symbol = f"um-{__import__('uuid').uuid4().hex[:8]}"
    user = await create_user(role="SYSTEM_ADMIN")
    token = SimpleNamespace(user_id=user.id, tenant_id=None, role=user.role)
    created = await create_unit(
        UnitCreate(symbol=symbol, name="UM Unit Probe", quantity_type=QuantityType.OTHER.value),
        db,
        token,
    )
    prov = await provenance_for(db, "unit", created.id)
    assert prov is not None and prov.user_modified is True
    assert prov.source_version_id is None, "a user-authored row has no pack provenance"

    await db.delete(prov)
    row = await db.get(Unit, created.id)
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_mark_user_modified_is_best_effort(db):
    """A bogus entity id never raises (the human write must not fail because
    of a provenance hiccup)."""
    await mark_user_modified_for_model(db, Concept, "not-a-uuid")
    await mark_user_modified_for_model(db, Concept, __import__("uuid").uuid4())


@pytest.mark.asyncio
async def test_anatomy_json_import_is_a_system_only_door(db):
    """F9: the global JSON import never matches or mutates a tenant row.

    Even though the endpoint is SYSTEM_ADMIN-only, the service is a
    defense-in-depth seam: a slug that belongs to a tenant's private structure
    is invisible to it, and a new *system* row is created instead. The refreshed
    edge map is system-only too, so an edge can never resolve to a tenant node.
    """
    import uuid

    from app.models.enums import CatalogScope
    from app.schemas.anatomy_import import AnatomyImportNode, AnatomyImportPayload

    user = await create_user(role="SYSTEM_ADMIN")
    slug = f"tenant-anat-{uuid.uuid4().hex[:8]}"
    tenant_row = AnatomyStructure(
        tenant_id=user.tenant_id,
        slug=slug,
        name="Tenant Heart",
        scope=CatalogScope.TENANT,
    )
    db.add(tenant_row)
    await db.commit()

    stats = await AnatomyImportService(db).import_graph(
        AnatomyImportPayload(
            nodes=[AnatomyImportNode(slug=slug, name="Global Heart")],
            edges=[],
        )
    )
    await db.commit()

    assert stats["nodes_added"] == 1
    await db.refresh(tenant_row)
    assert tenant_row.name == "Tenant Heart"
    global_row = (
        await db.execute(
            select(AnatomyStructure).where(
                AnatomyStructure.slug == slug,
                AnatomyStructure.tenant_id.is_(None),
            )
        )
    ).scalar_one()
    assert global_row.name == "Global Heart"
