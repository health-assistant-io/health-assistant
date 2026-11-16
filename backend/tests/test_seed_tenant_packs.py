"""Tenant-scope seed packs — overlay apply, precedence, isolation (ADR 0056
assumption 3, program Phase 10A).

Pinned behaviors:

* a tenant overlay pack applies into the tenant's own rows: the row carries
  the tenant's id and a tenant-qualified deterministic id (it may reuse a
  system ``seed_key`` without colliding — ADR 0055 per-tenant-scope
  uniqueness), and the system lane's row is untouched;
* the capability matrix still holds: a tenant pack carrying a global-only
  item type (``concepts.json``…) never parses;
* tenant isolation: another tenant's (or the system lane's) claim on the
  same seed key never enters a tenant pack's precedence decision; two
  tenants overlaying the same key produce two independent rows;
* equal priority within ONE tenant + different content is a typed conflict
  that blocks the apply; a higher-priority tenant pack overrides;
* a tenant vaccine's PREVENTS edges inherit its tenancy with
  tenant-qualified edge ids (disease endpoints stay system rows);
* deferred-reference retries are scope-pinned: a tenant apply retries only
  its tenant's pending refs, a system apply only the system-scope refs.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.seed_pack.manifest import parse_pack
from app.models.concept_model import ConceptEdge
from app.models.enums import (
    SeedItemAction,
    SeedItemState,
    SeedRunStatus,
    SeedSourceScope,
    SeedVersionStatus,
)
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.vaccine import VaccineCatalog
from app.models.seed_source_model import (
    SeedDeferredRef,
    SeedItemProvenance,
    SeedSourceVersion,
)
from app.services.seed.applier import PackApplication, SeedPackApplier
from app.services.seed.planner import SeedPackPlanner
from tests._auth_helpers import create_tenant
from tests.seed_lane import make_pack, make_source_version


def med_item(key: str, name: str, **extra) -> dict:
    return {"seed_key": f"medication:{key}", "name": name, **extra}


def vac_item(key: str, name: str, targets: list[str] | None = None) -> dict:
    item = {
        "seed_key": f"vaccine:{key}",
        "slug": key,
        "name": name,
        "coding_system": "cvx",
    }
    if targets is not None:
        item["target_diseases"] = targets
    return item


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def _row(db, model, seed_key, tenant_id=None):
    from app.services.seed.identity import identity_id

    row_id = identity_id(seed_key, tenant_id=tenant_id)
    stmt = select(model).where(model.id == row_id)
    if tenant_id is not None:
        stmt = stmt.where(model.tenant_id == tenant_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _cleanup_med(db, key, tenant_id=None):

    row = await _row(db, MedicationCatalog, key, tenant_id)
    if row is None:
        return
    prov = (
        await db.execute(
            select(SeedItemProvenance).where(
                SeedItemProvenance.entity_type == "medication",
                SeedItemProvenance.entity_id == row.id,
            )
        )
    ).scalar_one_or_none()
    if prov is not None:
        await db.delete(prov)
    await db.delete(row)
    await db.commit()


async def test_tenant_overlay_applies_into_tenant_rows(db):
    tenant_id = await create_tenant()
    pack = make_pack({"medications.json": [med_item("overlay-med", "Overlay Med")]})
    source, version = await make_source_version(
        db, pack, scope=SeedSourceScope.TENANT, tenant_id=tenant_id
    )
    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )
    assert outcome.statuses[version.id] is SeedRunStatus.OK, outcome.plan_docs

    from app.services.seed.identity import seed_key_uuid

    tenant_row = await _row(db, MedicationCatalog, "medication:overlay-med", tenant_id)
    assert tenant_row is not None
    assert str(tenant_row.tenant_id) == str(tenant_id)
    assert tenant_row.id != seed_key_uuid("medication", "overlay-med"), (
        "tenant overlay rows derive tenant-qualified ids"
    )
    system_row = await _row(db, MedicationCatalog, "medication:overlay-med", None)
    assert system_row is None, "the tenant pack never writes system rows"

    prov = (
        await db.execute(
            select(SeedItemProvenance).where(
                SeedItemProvenance.entity_type == "medication",
                SeedItemProvenance.entity_id == tenant_row.id,
            )
        )
    ).scalar_one()
    assert str(prov.source_version_id) == str(version.id)

    await _cleanup_med(db, "medication:overlay-med", tenant_id)
    await db.delete(version)
    await db.delete(source)
    await db.commit()


async def test_tenant_pack_cannot_carry_global_only_types(db):
    manifest = {
        "id": "tenant-global-pack",
        "schema_version": "1.0",
        "version": "1.0.0",
        "license": "CC0-1.0",
        "scope": "tenant",
        "priority": 250,
        "item_files": [],
    }
    import hashlib
    import json

    content = json.dumps(
        {
            "metadata": {"version": "1.0.0"},
            "items": [{"seed_key": "concept:tenant-only", "name": "T", "slug": "tenant-only"}],
        }
    ).encode("utf-8")
    manifest["item_files"] = [
        {"path": "items/concepts.json", "sha256": hashlib.sha256(content).hexdigest()}
    ]
    parsed, errors = parse_pack(manifest, {"items/concepts.json": content})
    assert parsed is None
    assert any(error.code == "GLOBAL_ONLY_ITEM_TYPE" for error in errors)
    await db.commit()


async def test_two_tenants_same_key_are_isolated(db):
    tenant_a = await create_tenant()
    tenant_b = await create_tenant()
    pack = make_pack({"medications.json": [med_item("iso-med", "Isolated Med")]})
    source_a, version_a = await make_source_version(
        db, pack, scope=SeedSourceScope.TENANT, tenant_id=tenant_a, uri="test://iso-a"
    )
    outcome_a = await SeedPackApplier().apply(
        [PackApplication(source=source_a, version=version_a, pack=pack)]
    )
    assert outcome_a.statuses[version_a.id] is SeedRunStatus.OK

    source_b, version_b = await make_source_version(
        db, pack, scope=SeedSourceScope.TENANT, tenant_id=tenant_b, uri="test://iso-b"
    )
    planner = SeedPackPlanner(db, source_b, version_b, pack)
    plan_b = await planner.plan()
    entry = next(e for e in plan_b.entries if e.seed_key == "medication:iso-med")
    assert entry.action is SeedItemAction.CREATE, (
        "tenant A's claim on the same key never enters tenant B's diff"
    )
    outcome_b = await SeedPackApplier().apply(
        [PackApplication(source=source_b, version=version_b, pack=pack)]
    )
    assert outcome_b.statuses[version_b.id] is SeedRunStatus.OK

    row_a = await _row(db, MedicationCatalog, "medication:iso-med", tenant_a)
    row_b = await _row(db, MedicationCatalog, "medication:iso-med", tenant_b)
    assert row_a is not None and row_b is not None
    assert row_a.id != row_b.id
    assert str(row_a.tenant_id) == str(tenant_a)
    assert str(row_b.tenant_id) == str(tenant_b)

    await _cleanup_med(db, "medication:iso-med", tenant_a)
    await _cleanup_med(db, "medication:iso-med", tenant_b)
    for version, source in ((version_a, source_a), (version_b, source_b)):
        await db.delete(version)
        await db.delete(source)
    await db.commit()


async def test_equal_priority_same_tenant_conflicts_higher_priority_overrides(db):
    tenant_id = await create_tenant()
    pack_a = make_pack(
        {"medications.json": [med_item("prio-med", "Content A")]},
        pack_id="prio-pack-a",
        priority=200,
        scope="tenant",
    )
    pack_b = make_pack(
        {"medications.json": [med_item("prio-med", "Content B")]},
        pack_id="prio-pack-b",
        priority=200,
        scope="tenant",
    )
    source_a, version_a = await make_source_version(
        db, pack_a, scope=SeedSourceScope.TENANT, tenant_id=tenant_id, uri="test://prio-a"
    )
    outcome_a = await SeedPackApplier().apply(
        [PackApplication(source=source_a, version=version_a, pack=pack_a)]
    )
    assert outcome_a.statuses[version_a.id] is SeedRunStatus.OK

    source_b, version_b = await make_source_version(
        db, pack_b, scope=SeedSourceScope.TENANT, tenant_id=tenant_id, uri="test://prio-b"
    )
    outcome_b = await SeedPackApplier().apply(
        [PackApplication(source=source_b, version=version_b, pack=pack_b)]
    )
    assert outcome_b.statuses[version_b.id] is SeedRunStatus.CONFLICTED, (
        "equal priority + different content within one tenant is a typed conflict"
    )
    row = await _row(db, MedicationCatalog, "medication:prio-med", tenant_id)
    assert row.name == "Content A", "the conflicted apply wrote nothing"

    pack_c = make_pack(
        {"medications.json": [med_item("prio-med", "Content C")]},
        pack_id="prio-pack-c",
        priority=300,
        scope="tenant",
    )
    source_c, version_c = await make_source_version(
        db, pack_c, scope=SeedSourceScope.TENANT, tenant_id=tenant_id, uri="test://prio-c"
    )
    outcome_c = await SeedPackApplier().apply(
        [PackApplication(source=source_c, version=version_c, pack=pack_c)]
    )
    assert outcome_c.statuses[version_c.id] is SeedRunStatus.OK, "higher priority takes over"
    await db.refresh(row)
    assert row.name == "Content C"

    await _cleanup_med(db, "medication:prio-med", tenant_id)
    for version, source in ((version_a, source_a), (version_b, source_b), (version_c, source_c)):
        await db.delete(version)
        await db.delete(source)
    await db.commit()


async def test_tenant_vaccine_edges_inherit_tenancy(db):
    from app.core.catalog_identity import seed_key_uuid
    from app.models.concept_model import Concept
    from app.models.enums import CatalogScope, ConceptKind, ConceptStatus

    tenant_id = await create_tenant()
    disease_id = seed_key_uuid("concept", "hpv-infection")
    disease = await db.get(Concept, disease_id)
    created_disease = disease is None
    if created_disease:
        disease = Concept(
            id=disease_id,
            slug="hpv-infection",
            name="HPV Infection",
            seed_key="concept:hpv-infection",
            primary_kind=ConceptKind.DISEASE,
            status=ConceptStatus.ACTIVE,
            display_order=0,
            scope=CatalogScope.SYSTEM,
            tenant_id=None,
        )
        db.add(disease)
        await db.commit()
    try:
        pack = make_pack(
            {"vaccines.json": [vac_item("overlay-vax", "Overlay Vax", targets=["hpv-infection"])]},
        )
        source, version = await make_source_version(
            db, pack, scope=SeedSourceScope.TENANT, tenant_id=tenant_id
        )
        outcome = await SeedPackApplier().apply(
            [PackApplication(source=source, version=version, pack=pack)]
        )
        assert outcome.statuses[version.id] is SeedRunStatus.OK, outcome.plan_docs

        vax = await _row(db, VaccineCatalog, "vaccine:overlay-vax", tenant_id)
        assert vax is not None and str(vax.tenant_id) == str(tenant_id)
        edges = (
            (await db.execute(select(ConceptEdge).where(ConceptEdge.src_id == vax.id)))
            .scalars()
            .all()
        )
        assert edges, "the PREVENTS edge materialized"
        for edge in edges:
            assert str(edge.tenant_id) == str(tenant_id), "the edge inherits the overlay's tenancy"

        for edge in edges:
            await db.delete(edge)
        await db.delete(vax)
        await db.delete(version)
        await db.delete(source)
        await db.commit()
    finally:
        if created_disease:
            fresh = await db.get(Concept, disease_id)
            if fresh is not None:
                await db.delete(fresh)
            await db.commit()


async def test_deferred_retry_is_scope_pinned(db):
    """A tenant apply retries only its tenant's pending refs — the system
    lane's unresolved refs stay untouched by the overlay apply."""
    tenant_id = await create_tenant()
    system_pack = make_pack({"medications.json": [med_item("sys-anchor-med", "Anchor")]})
    system_source, system_version = await make_source_version(db, system_pack)
    system_ref = SeedDeferredRef(
        source_version_id=system_version.id,
        payload={"stage": "vaccines", "seed_key": "vaccine:sys-def", "item": {}, "ref": "x"},
    )
    db.add(system_ref)
    await db.commit()

    pack = make_pack({"medications.json": [med_item("defer-tenant-med", "Defer Tenant Med")]})
    source, version = await make_source_version(
        db, pack, scope=SeedSourceScope.TENANT, tenant_id=tenant_id
    )
    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )
    assert outcome.statuses[version.id] is SeedRunStatus.OK
    await db.refresh(system_ref)
    assert system_ref.attempts == 0, "the tenant apply never touched the system-scope ref"

    await db.delete(system_ref)
    await db.commit()
    await _cleanup_med(db, "medication:defer-tenant-med", tenant_id)
    await db.delete(version)
    await db.delete(source)
    await db.delete(system_version)
    await db.delete(system_source)
    await db.commit()


async def test_seed_apply_audit_carries_the_source_tenant(db):
    """The ``seed.apply`` audit row of a tenant overlay apply is written on
    the source's tenant (never NULL) — the audit lane keeps the lanes apart."""
    from sqlalchemy import text

    tenant_id = await create_tenant()
    pack = make_pack({"medications.json": [med_item("audit-tenant-med", "Audit Tenant Med")]})
    source, version = await make_source_version(
        db, pack, scope=SeedSourceScope.TENANT, tenant_id=tenant_id
    )
    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )
    assert outcome.statuses[version.id] is SeedRunStatus.OK, outcome.plan_docs

    row = (
        await db.execute(
            text(
                "SELECT tenant_id FROM audit_events "
                "WHERE action = 'seed.apply' AND resource_id = :rid"
            ),
            {"rid": version.id},
        )
    ).first()
    assert row is not None, "the apply audited"
    assert row[0] is not None and str(row[0]) == str(tenant_id), (
        "the overlay apply audits with the source's tenant"
    )

    await _cleanup_med(db, "medication:audit-tenant-med", tenant_id)
    await db.delete(version)
    await db.delete(source)
    await db.commit()


async def test_tenant_replaces_repoints_tenant_edges(db):
    """A tenant overlay's ``replaces`` retires the old overlay row and
    repoints the TENANT edges to the replacement row (found in the pack's
    own lane) — an overlay repoint never rewrites the system graph."""
    from app.core.catalog_identity import seed_key_uuid
    from app.models.concept_model import Concept
    from app.models.enums import CatalogScope, ConceptKind, ConceptStatus
    from tests.seed_lane import provenance_for

    tenant_id = await create_tenant()
    disease_id = seed_key_uuid("concept", "hpv-infection")
    disease = await db.get(Concept, disease_id)
    created_disease = disease is None
    if created_disease:
        disease = Concept(
            id=disease_id,
            slug="hpv-infection",
            name="HPV Infection",
            seed_key="concept:hpv-infection",
            primary_kind=ConceptKind.DISEASE,
            status=ConceptStatus.ACTIVE,
            display_order=0,
            scope=CatalogScope.SYSTEM,
            tenant_id=None,
        )
        db.add(disease)
        await db.commit()
    try:
        pack_v1 = make_pack(
            {
                "vaccines.json": [
                    vac_item("tenant-replace-old", "Old Overlay Vax", targets=["hpv-infection"])
                ]
            },
            pack_id="tenant-replaces",
            scope="tenant",
            priority=250,
            version="1.0.0",
        )
        source, version1 = await make_source_version(
            db, pack_v1, scope=SeedSourceScope.TENANT, tenant_id=tenant_id
        )
        outcome = await SeedPackApplier().apply(
            [PackApplication(source=source, version=version1, pack=pack_v1)]
        )
        assert outcome.statuses[version1.id] is SeedRunStatus.OK, outcome.plan_docs
        old = await _row(db, VaccineCatalog, "vaccine:tenant-replace-old", tenant_id)
        assert old is not None
        old_edges = (
            (await db.execute(select(ConceptEdge).where(ConceptEdge.src_id == old.id)))
            .scalars()
            .all()
        )
        assert old_edges, "the PREVENTS edge materialized on the overlay row"
        assert all(str(e.tenant_id) == str(tenant_id) for e in old_edges)

        pack_v2 = make_pack(
            {"vaccines.json": [vac_item("tenant-replace-new", "New Overlay Vax")]},
            pack_id="tenant-replaces",
            scope="tenant",
            priority=250,
            version="2.0.0",
            replaces=["tenant-replace-old"],
        )
        version2 = SeedSourceVersion(
            source_id=source.id,
            digest=pack_v2.digest,
            manifest=pack_v2.manifest,
            contract_version="1.0",
            status=SeedVersionStatus.APPROVED,
            reason="tenant replaces test",
        )
        db.add(version2)
        await db.commit()
        await db.refresh(version2)

        plan = await SeedPackPlanner(db, source, version2, pack_v2).plan()
        retires = [r for r in plan.retires if r.seed_key == "vaccine:tenant-replace-old"]
        assert retires and retires[0].repoint, plan.retires
        assert retires[0].replaces_new_key == "vaccine:tenant-replace-new"

        outcome2 = await SeedPackApplier().apply(
            [PackApplication(source=source, version=version2, pack=pack_v2)]
        )
        assert outcome2.statuses[version2.id] is SeedRunStatus.OK, outcome2.plan_docs

        new = await _row(db, VaccineCatalog, "vaccine:tenant-replace-new", tenant_id)
        assert new is not None
        repointed = (
            (await db.execute(select(ConceptEdge).where(ConceptEdge.src_id == new.id)))
            .scalars()
            .all()
        )
        assert repointed, "the tenant edges repointed to the replacement row"
        assert all(str(e.tenant_id) == str(tenant_id) for e in repointed)
        prov = await provenance_for(db, "vaccine", old.id)
        await db.refresh(prov)
        assert prov.state is SeedItemState.RETIRED, "the replaced overlay row retired"

        # cleanup
        for edge in old_edges + list(repointed):
            await db.delete(edge)
        for vac, vid in ((old, old.id), (new, new.id)):
            p = (
                await db.execute(
                    select(SeedItemProvenance).where(
                        SeedItemProvenance.entity_type == "vaccine",
                        SeedItemProvenance.entity_id == vid,
                    )
                )
            ).scalar_one_or_none()
            if p is not None:
                await db.delete(p)
            await db.delete(vac)
        await db.delete(version1)
        await db.delete(version2)
        await db.delete(source)
        await db.commit()
    finally:
        if created_disease:
            fresh = await db.get(Concept, disease_id)
            if fresh is not None:
                await db.delete(fresh)
            await db.commit()
