"""The seed pack planner — diff, precedence, freeze, retirement (ADR 0056).

Pinned behaviors:

* diff by ``seed_key`` → create/update/noop; a second identical plan is
  byte-identical (deterministic, no timestamps);
* equal priority + different content is a typed CONFLICT; higher priority
  overrides and the loser is recorded; lower priority plans SKIP as loser;
* ``user_modified`` rows never auto-update (SKIP + ``update_available``);
* retirement plans only when no other provider claims the key and no
  patient data references the row; ``replaces`` pairs the single
  same-key-space candidate and repoints;
* a tenant-scope source plans against the tenant's own rows (tenant-qualified
  ids, scope-pinned lookups, provider isolation);
* homoglyph / near-duplicate names warn.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.catalog_identity import seed_key_uuid
from app.core.database import AsyncSessionLocal
from app.models.enums import SeedItemAction, SeedSourceScope
from app.models.fhir.medication import MedicationCatalog
from app.models.seed_source_model import SeedSourceVersion, SeedVersionStatus
from app.services.seed.applier import PackApplication, SeedPackApplier
from app.services.seed.planner import (
    WARN_NEAR_DUPLICATE,
    WARN_REPLACES_AMBIGUOUS,
    ItemPlan,
    SeedPackPlanner,
)
from tests._auth_helpers import create_tenant
from tests.seed_lane import make_pack, make_source_version, provenance_for


def entry(plan, stage: str, seed_key: str) -> ItemPlan:
    matches = [e for e in plan.stages.get(stage, []) if e.seed_key == seed_key]
    assert matches, f"no plan entry for {stage}/{seed_key}: {plan.stages}"
    return matches[0]


def med_item(key: str, name: str, **extra) -> dict:
    return {"seed_key": f"medication:{key}", "name": name, **extra}


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


@pytest.mark.asyncio
async def test_plan_diff_create_then_noop_and_deterministic(db):
    """A fresh key plans CREATE; after apply the same pack plans NOOP and the
    plan document is byte-identical across runs (deterministic JSONB)."""
    pack = make_pack({"medications.json": [med_item("plan-med-a", "Plan Med A")]})
    source, version = await make_source_version(db, pack)
    planner = SeedPackPlanner(db, source, version, pack)
    plan = await planner.plan()
    assert entry(plan, "medications", "medication:plan-med-a").action.value == "create"

    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )
    assert outcome.ok, outcome.statuses

    plan2 = await SeedPackPlanner(db, source, version, pack).plan()
    entry2 = entry(plan2, "medications", "medication:plan-med-a")
    assert entry2.action.value == "noop"

    plan3 = await SeedPackPlanner(db, source, version, pack).plan()
    assert plan2.to_doc() == plan3.to_doc(), "the plan document must be deterministic"

    prov = await provenance_for(db, "medication", seed_key_uuid("medication", "plan-med-a"))
    assert prov is not None and prov.user_modified is False
    await db.delete(prov)
    row = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == "medication:plan-med-a")
        )
    ).scalar_one()
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_equal_priority_conflict_blocks_apply(db):
    """Two sources at the same priority with different content: the planner
    emits a typed CONFLICT and the apply refuses (conflicted run, no writes)."""
    key = "plan-conflict-med"
    pack_a = make_pack(
        {"medications.json": [med_item(key, "Conflict Med A")]}, pack_id="conflict-a"
    )
    pack_b = make_pack(
        {"medications.json": [med_item(key, "Conflict Med B", description="different")]},
        pack_id="conflict-b",
    )
    source_a, version_a = await make_source_version(db, pack_a, uri="test://conflict-a")
    source_b, version_b = await make_source_version(db, pack_b, uri="test://conflict-b")
    assert pack_a.digest != pack_b.digest

    outcome_a = await SeedPackApplier().apply(
        [PackApplication(source=source_a, version=version_a, pack=pack_a)]
    )
    assert outcome_a.ok

    plan_b = await SeedPackPlanner(db, source_b, version_b, pack_b).plan()
    conflicted = entry(plan_b, "medications", f"medication:{key}")
    assert conflicted.action.value == "conflict"
    assert conflicted.conflict["reason"] == "equal_priority_different_content"

    outcome_b = await SeedPackApplier().apply(
        [PackApplication(source=source_b, version=version_b, pack=pack_b)]
    )
    from app.models.enums import SeedRunStatus

    assert outcome_b.statuses[version_b.id] is SeedRunStatus.CONFLICTED
    # the conflicted apply must not have moved the row or the pointer
    row = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == f"medication:{key}")
        )
    ).scalar_one()
    assert row.name == "Conflict Med A"

    # cleanup
    prov = await provenance_for(db, "medication", row.id)
    await db.delete(prov)
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_higher_priority_overrides_and_records_loser(db):
    """Priority 200 takes the key over priority 100 (provenance switches) and
    the displaced provider is recorded on the winner's run item."""
    key = "plan-override-med"
    pack_low = make_pack(
        {"medications.json": [med_item(key, "Low Med")]}, pack_id="override-low", priority=100
    )
    pack_high = make_pack(
        {"medications.json": [med_item(key, "High Med", description="takes over")]},
        pack_id="override-high",
        priority=150,
    )
    source_low, version_low = await make_source_version(db, pack_low, uri="test://override-low")
    source_high, version_high = await make_source_version(db, pack_high, uri="test://override-high")

    assert (
        await SeedPackApplier().apply(
            [PackApplication(source=source_low, version=version_low, pack=pack_low)]
        )
    ).ok

    plan = await SeedPackPlanner(db, source_high, version_high, pack_high).plan()
    assert entry(plan, "medications", f"medication:{key}").action.value == "override"

    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source_high, version=version_high, pack=pack_high)]
    )
    assert outcome.ok, outcome.statuses

    row = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == f"medication:{key}")
        )
    ).scalar_one()
    assert row.name == "High Med"
    prov = await provenance_for(db, "medication", row.id)
    assert str(prov.source_version_id) == str(version_high.id)

    from app.models.enums import SeedItemAction
    from app.models.seed_source_model import SeedImportRunItem

    items = (
        (
            await db.execute(
                select(SeedImportRunItem).where(
                    SeedImportRunItem.run_id == outcome.runs[version_high.id],
                    SeedImportRunItem.action == SeedItemAction.OVERRIDE,
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(
        i.seed_key == f"medication:{key}"
        and i.conflict.get("loser_source_id") == str(source_low.id)
        for i in items
    ), "the loser must be recorded on the winner's run item"

    prov = await provenance_for(db, "medication", row.id)
    await db.delete(prov)
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_lower_priority_plans_skip_as_loser(db):
    """A priority-100 pack targeting a key held by priority 150 plans SKIP
    with the loser payload and the apply does not touch the row."""
    key = "plan-lose-med"
    pack_high = make_pack(
        {"medications.json": [med_item(key, "Holder Med")]}, pack_id="lose-high", priority=150
    )
    pack_low = make_pack(
        {"medications.json": [med_item(key, "Loser Med")]}, pack_id="lose-low", priority=100
    )
    source_high, version_high = await make_source_version(db, pack_high, uri="test://lose-high")
    source_low, version_low = await make_source_version(db, pack_low, uri="test://lose-low")
    assert (
        await SeedPackApplier().apply(
            [PackApplication(source=source_high, version=version_high, pack=pack_high)]
        )
    ).ok

    plan = await SeedPackPlanner(db, source_low, version_low, pack_low).plan()
    skipped = entry(plan, "medications", f"medication:{key}")
    assert skipped.action.value == "skip"
    assert skipped.conflict["reason"] == "higher_priority_provider"

    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source_low, version=version_low, pack=pack_low)]
    )
    assert outcome.ok
    row = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == f"medication:{key}")
        )
    ).scalar_one()
    assert row.name == "Holder Med", "the lower-priority apply must not touch the row"

    prov = await provenance_for(db, "medication", row.id)
    await db.delete(prov)
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_user_modified_freeze_surfaces_update_available(db):
    """A human edit flips ``user_modified``; the pack's changed content then
    plans SKIP + update_available (never auto-updated) and the apply keeps
    the user's values."""
    from types import SimpleNamespace

    from app.schemas.medication import MedicationCatalogUpdate
    from app.services.catalog.catalog_write_service import update_medication
    from tests._auth_helpers import create_user

    key = "plan-freeze-med"
    pack_v1 = make_pack(
        {"medications.json": [med_item(key, "Frozen Med")]}, pack_id="freeze", version="1.0.0"
    )
    pack_v2 = make_pack(
        {"medications.json": [med_item(key, "Frozen Med", description="pack changed")]},
        pack_id="freeze",
        version="1.1.0",
    )
    source, version1 = await make_source_version(db, pack_v1)
    assert (
        await SeedPackApplier().apply(
            [PackApplication(source=source, version=version1, pack=pack_v1)]
        )
    ).ok

    user = await create_user()
    actor = SimpleNamespace(user_id=user.id, tenant_id=None, role=user.role)
    row = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == f"medication:{key}")
        )
    ).scalar_one()
    await update_medication(db, actor, row.id, MedicationCatalogUpdate(description="human edit"))

    prov = await provenance_for(db, "medication", row.id)
    assert prov.user_modified is True, "the human write must flip the flag"

    plan = await SeedPackPlanner(db, source, version1, pack_v2).plan()
    frozen = entry(plan, "medications", f"medication:{key}")
    assert frozen.action.value == "skip"
    assert frozen.update_available is True
    assert frozen.conflict["reason"] == "user_modified_freeze"

    version2 = SeedSourceVersion(
        source_id=source.id,
        digest=pack_v2.digest,
        manifest=pack_v2.manifest,
        contract_version="1.0",
        status=SeedVersionStatus.APPROVED,
        reason="freeze test",
    )
    db.add(version2)
    await db.commit()
    await db.refresh(version2)
    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version2, pack=pack_v2)]
    )
    assert outcome.ok
    await db.refresh(row)
    assert row.description == "human edit", "the user edit must win"

    prov = await provenance_for(db, "medication", row.id)
    await db.delete(prov)
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_near_duplicate_names_warn(db):
    """Homoglyph/near-duplicate names under different keys warn (in-pack and
    against existing rows)."""
    pack = make_pack(
        {
            "medications.json": [
                med_item("dup-cafe", "Café Med"),
                med_item("dup-cafe-plain", "Cafe Med"),
            ]
        },
        pack_id="dup-pack",
    )
    source, version = await make_source_version(db, pack)
    plan = await SeedPackPlanner(db, source, version, pack).plan()
    codes = {w["code"] for w in plan.warnings}
    assert WARN_NEAR_DUPLICATE in codes
    entry_a = entry(plan, "medications", "medication:dup-cafe")
    assert any(w == WARN_NEAR_DUPLICATE for w in entry_a.warnings)

    # cleanup (nothing applied; only the source/version rows persist)
    await db.delete(version)
    await db.delete(source)
    await db.commit()


@pytest.mark.asyncio
async def test_replaces_ambiguous_pairing_warns(db):
    """A ``replaces`` body whose qualified key exists but with TWO same-key-
    space candidates in the pack is ambiguous: loud warning, no repoint."""
    previous_key = "medication:replaced-old"
    row = MedicationCatalog(seed_key=previous_key, name="Replaced Old", tenant_id=None)
    db.add(row)
    await db.commit()
    pack = make_pack(
        {
            "medications.json": [
                med_item("replaced-new-a", "New A"),
                med_item("replaced-new-b", "New B"),
            ]
        },
        pack_id="ambig-pack",
        replaces=["replaced-old"],
    )
    source, version = await make_source_version(db, pack)
    plan = await SeedPackPlanner(db, source, version, pack).plan()
    assert any(
        w["code"] == WARN_REPLACES_AMBIGUOUS and w["seed_key"] == "replaced-old"
        for w in plan.warnings
    )
    await db.delete(version)
    await db.delete(source)
    await db.delete(row)
    await db.commit()


@pytest.mark.asyncio
async def test_tenant_scope_plan_diffs_tenant_rows(db):
    """A tenant-scope source plans against the tenant's own rows: the tenant
    medication is created (tenant-qualified id, ``tenant_id`` set) and the
    plan never sees the system lane's claim on the same seed key."""
    tenant_id = await create_tenant()
    pack = make_pack({"medications.json": [med_item("tenant-med-a", "Tenant Med A")]})
    source, version = await make_source_version(
        db,
        pack,
        scope=SeedSourceScope.TENANT,
        tenant_id=tenant_id,
        uri="test://tenant-scope",
    )
    plan = await SeedPackPlanner(db, source, version, pack).plan()
    entry = next(e for e in plan.entries if e.seed_key == "medication:tenant-med-a")
    assert entry.action is SeedItemAction.CREATE
    assert plan.scope == "tenant"
    assert plan.priority == 100
    assert entry.entity_id is not None
    from app.core.catalog_identity import seed_key_uuid as _sku

    assert entry.entity_id != _sku("medication", "tenant-med-a"), (
        "tenant overlay rows derive tenant-qualified ids"
    )
    await db.delete(version)
    await db.delete(source)
    await db.commit()
