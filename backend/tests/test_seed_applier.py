"""The seed pack applier — locks, transactions, deferred refs, rollback,
retire/restore, replaces, bundled boot (ADR 0056 program Phase 9B).

Pinned behaviors:

* an apply writes rows + provenance + run items + audit and advances the
  approved pointer; seed writes leave ``user_modified`` false;
* a failed stage rolls back completely: no partial rows, the pointer stays,
  the run records ``failed``;
* unresolved cross-pack references land in ``seed_deferred_refs`` and are
  retried on the next apply (a ref that resolves flips to ``landed``);
* retirement is provenance-level (never a delete), skipped while another
  active source provides the key or patient data references the row, and a
  re-apply of the older version RESTORES retired items (the revocation path);
* ``replaces`` repoints curated system edges to the new row and leaves
  patient FKs untouched;
* concurrent applies serialize on the advisory lock;
* a fresh ``running`` run is superseded when the apply holds the advisory
  lock (a live applier would hold it — the crash leftover must not wedge
  the boot apply), and refuses with ``ALREADY_RUNNING`` without the lock;
* the app-only side stages run for the bundled source only;
* a PARTIAL bundled apply keeps the pointer at boot (no restart loop) and
  logs the item-error count loudly;
* the bundled source applies on digest change and skips when unchanged;
* an unapproved version is refused.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.core.catalog_identity import seed_key_uuid
from app.core.database import AsyncSessionLocal
from app.core.seed_pack.bundled import bundled_seeds_dir
from app.models.concept_model import Concept, ConceptEdge
from app.models.enums import (
    ConceptStatus,
    SeedDeferredStatus,
    SeedItemAction,
    SeedItemState,
    SeedRunMode,
    SeedRunStatus,
    SeedSourceKind,
)
from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.fhir.patient import Gender, Patient
from app.models.seed_source_model import (
    SeedDeferredRef,
    SeedImportRun,
    SeedImportRunItem,
    SeedSourceVersion,
    SeedVersionStatus,
)
from app.services.seed.applier import PackApplication, SeedApplyError, SeedPackApplier
from app.services.seed.bundled_source import (
    apply_bundled_on_boot,
    ensure_approved_version,
    ensure_bundled_source,
)
from app.services.seed.concepts import _seed_edge_identity
from app.services.seed.planner import SeedPackPlanner
from app.services.seed.service import SeedService
from tests._auth_helpers import create_tenant
from tests.seed_lane import make_pack, make_source_version, provenance_for


def concept_item(slug: str, name: str, **extra) -> dict:
    return {
        "seed_key": f"concept:{slug}",
        "slug": slug,
        "name": name,
        "kind": "anatomy_class",
        **extra,
    }


def med_item(key: str, name: str, **extra) -> dict:
    return {"seed_key": f"medication:{key}", "name": name, **extra}


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def cleanup_med(session: AsyncSessionLocal, seed_key: str) -> None:
    row = (
        await session.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == seed_key)
        )
    ).scalar_one_or_none()
    if row is not None:
        prov = await provenance_for(session, "medication", row.id)
        if prov is not None:
            await session.delete(prov)
        await session.delete(row)
        await session.commit()


@pytest.mark.asyncio
async def test_apply_writes_rows_provenance_run_and_audit(db):
    """A successful apply: the row exists, provenance points at the version
    (``user_modified`` false), run items record the action, an audit event is
    written (fail-closed) and the pointer advances."""
    pack = make_pack({"medications.json": [med_item("apply-med-a", "Apply Med A")]})
    source, version = await make_source_version(db, pack)
    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )
    assert outcome.statuses[version.id] is SeedRunStatus.OK, outcome.plan_docs

    row = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == "medication:apply-med-a")
        )
    ).scalar_one()
    prov = await provenance_for(db, "medication", row.id)
    assert prov is not None
    assert str(prov.source_version_id) == str(version.id)
    assert prov.state is SeedItemState.ACTIVE
    assert prov.user_modified is False, "seed writes leave the flag false"
    assert prov.content_hash

    run_items = (
        (
            await db.execute(
                select(SeedImportRunItem).where(
                    SeedImportRunItem.run_id == outcome.runs[version.id]
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(
        i.seed_key == "medication:apply-med-a" and i.action is SeedItemAction.CREATE
        for i in run_items
    )

    audit = (
        await db.execute(
            text(
                "SELECT action FROM audit_events WHERE resource_type = 'seed_source_version' "
                "AND resource_id = :rid"
            ),
            {"rid": version.id},
        )
    ).first()
    assert audit is not None and audit.action == "seed.apply"

    await db.refresh(source)
    assert str(source.current_version_id) == str(version.id)

    await cleanup_med(db, "medication:apply-med-a")


@pytest.mark.asyncio
async def test_failed_stage_rolls_back_and_pointer_stays(db, monkeypatch):
    """A seeder crashing mid-stage: the transaction rolls back (no partial
    rows), the run is ``failed`` and ``current_version_id`` does not move."""
    pack = make_pack({"medications.json": [med_item("boom-med-a", "Boom Med A")]})
    source, version = await make_source_version(db, pack)

    original = SeedService.seed_medications

    async def _boom(self, session, items=None):
        await original(self, session, items)
        raise RuntimeError("boom mid-stage")

    monkeypatch.setattr(SeedService, "seed_medications", _boom)
    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )
    monkeypatch.undo()
    assert outcome.statuses[version.id] is SeedRunStatus.FAILED

    run = await db.get(SeedImportRun, outcome.runs[version.id])
    assert run.status is SeedRunStatus.FAILED
    assert "boom" in (run.error or "")

    leftover = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == "medication:boom-med-a")
        )
    ).scalar_one_or_none()
    assert leftover is None, "a failed stage must leave nothing behind"

    await db.refresh(source)
    assert source.current_version_id is None, "a failed pack does not advance the pointer"

    await db.delete(run)
    await db.delete(version)
    await db.delete(source)
    await db.commit()


@pytest.mark.asyncio
async def test_deferred_refs_land_on_a_later_apply(db):
    """An edge whose endpoint is missing becomes a pending deferred ref; the
    apply that brings the concept in retries it to ``landed`` and the edge
    row appears."""
    edge_item = {
        "src_slug": "deferred-missing-c",
        "dst_slug": "deferred-anchor-c",
        "relation": "CORRELATES_WITH",
    }
    pack_a = make_pack(
        {
            "concepts.json": [concept_item("deferred-anchor-c", "Anchor")],
            "concept_edges.json": [edge_item],
        },
        pack_id="deferred-a",
    )
    pack_b = make_pack(
        {"concepts.json": [concept_item("deferred-missing-c", "Late Arrival")]},
        pack_id="deferred-b",
    )
    source_a, version_a = await make_source_version(db, pack_a, uri="test://deferred-a")
    source_b, version_b = await make_source_version(db, pack_b, uri="test://deferred-b")

    outcome_a = await SeedPackApplier().apply(
        [PackApplication(source=source_a, version=version_a, pack=pack_a)]
    )
    assert outcome_a.ok, outcome_a.plan_docs

    deferred = (
        (
            await db.execute(
                select(SeedDeferredRef).where(
                    SeedDeferredRef.source_version_id == version_a.id,
                    SeedDeferredRef.status == SeedDeferredStatus.PENDING,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(deferred) == 1, "the unresolved edge must be deferred, never silently skipped"
    assert deferred[0].payload["stage"] == "concept_edges"

    anchor_id = seed_key_uuid("concept", "deferred-anchor-c")
    missing_id = seed_key_uuid("concept", "deferred-missing-c")
    expected_edge_id = _seed_edge_identity(
        ("concept", "deferred-missing-c"), ("concept", "deferred-anchor-c"), "CORRELATES_WITH"
    )
    assert (await db.get(ConceptEdge, expected_edge_id)) is None, (
        "the edge must not exist while its endpoint is missing"
    )

    outcome_b = await SeedPackApplier().apply(
        [PackApplication(source=source_b, version=version_b, pack=pack_b)]
    )
    assert outcome_b.ok

    await db.refresh(deferred[0])
    assert deferred[0].status is SeedDeferredStatus.LANDED
    edge = await db.get(ConceptEdge, expected_edge_id)
    assert edge is not None and edge.dst_id == anchor_id

    # cleanup
    prov_edge = await provenance_for(db, "concept-edge", expected_edge_id)
    for obj in (prov_edge, edge):
        if obj is not None:
            await db.delete(obj)
    for cid in (anchor_id, missing_id):
        concept = await db.get(Concept, cid)
        if concept is not None:
            await db.execute(
                text("DELETE FROM concept_kind_tags WHERE concept_id = :cid"), {"cid": cid}
            )
            await db.delete(concept)
    await db.delete(deferred[0])
    await db.commit()


@pytest.mark.asyncio
async def test_retire_skip_when_patient_referenced_or_still_provided(db):
    """Retirement never deletes: a patient-referenced row is kept active, and
    a key another active source still provides is not retired either."""
    tenant_id = await create_tenant()
    patient = Patient(tenant_id=tenant_id, name=[{"text": "Retire Patient"}], gender=Gender.FEMALE)
    db.add(patient)
    await db.flush()
    pack_v1 = make_pack(
        {"medications.json": [med_item("retire-med-a", "Retire Med A")]},
        pack_id="retire-a",
        version="1.0.0",
    )
    source_a, version_a1 = await make_source_version(db, pack_v1, uri="test://retire-a")
    assert (
        await SeedPackApplier().apply(
            [PackApplication(source=source_a, version=version_a1, pack=pack_v1)]
        )
    ).ok

    med = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == "medication:retire-med-a")
        )
    ).scalar_one()
    statement = Medication(
        tenant_id=tenant_id,
        patient_id=patient.id,
        code={"text": "Retire Med A"},
        medication_catalog_id=med.id,
    )
    db.add(statement)
    await db.commit()

    # v2 drops the key — but the patient references it → SKIP, row kept active
    pack_v2 = make_pack(
        {"medications.json": [med_item("retire-med-b", "Retire Med B")]},
        pack_id="retire-a",
        version="2.0.0",
    )
    version_a2 = SeedSourceVersion(
        source_id=source_a.id,
        digest=pack_v2.digest,
        manifest=pack_v2.manifest,
        contract_version="1.0",
        status=SeedVersionStatus.APPROVED,
        reason="retire test",
    )
    db.add(version_a2)
    await db.commit()
    await db.refresh(version_a2)
    plan = await SeedPackPlanner(db, source_a, version_a2, pack_v2).plan()
    retire = [r for r in plan.retires if r.seed_key == "medication:retire-med-a"]
    assert retire and retire[0].action is SeedItemAction.SKIP
    assert retire[0].reason == "patient_referenced"

    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source_a, version=version_a2, pack=pack_v2)]
    )
    assert outcome.ok
    prov = await provenance_for(db, "medication", med.id)
    assert prov.state is SeedItemState.ACTIVE, "patient-referenced rows stay active"

    # now a second source still provides the key → still not retired
    await db.delete(statement)
    await db.commit()
    pack_other = make_pack(
        {"medications.json": [med_item("retire-med-a", "Retire Med A")]},
        pack_id="retire-other",
        priority=100,
    )
    source_other, version_other = await make_source_version(
        db, pack_other, uri="test://retire-other"
    )
    assert (
        await SeedPackApplier().apply(
            [PackApplication(source=source_other, version=version_other, pack=pack_other)]
        )
    ).ok

    pack_v3 = make_pack(
        {"medications.json": [med_item("retire-med-c", "Retire Med C")]},
        pack_id="retire-a",
        version="3.0.0",
    )
    version_a3 = SeedSourceVersion(
        source_id=source_a.id,
        digest=pack_v3.digest,
        manifest=pack_v3.manifest,
        contract_version="1.0",
        status=SeedVersionStatus.APPROVED,
        reason="retire test",
    )
    db.add(version_a3)
    await db.commit()
    await db.refresh(version_a3)
    plan = await SeedPackPlanner(db, source_a, version_a3, pack_v3).plan()
    retire = [r for r in plan.retires if r.seed_key == "medication:retire-med-a"]
    assert retire and retire[0].action.value == "noop"
    assert retire[0].reason == "still_provided_by_active_source"

    # cleanup
    prov = await provenance_for(db, "medication", med.id)
    await db.delete(prov)
    await db.delete(med)
    med_b = (
        await db.execute(
            select(MedicationCatalog).where(MedicationCatalog.seed_key == "medication:retire-med-b")
        )
    ).scalar_one_or_none()
    if med_b is not None:
        prov_b = await provenance_for(db, "medication", med_b.id)
        if prov_b is not None:
            await db.delete(prov_b)
        await db.delete(med_b)
    await db.commit()


@pytest.mark.asyncio
async def test_revocation_path_reapply_restores_retired_items(db):
    """Re-applying the older version (what a revocation does) restores items
    its successor retired: provenance back to active, concept status ACTIVE."""
    pack_v1 = make_pack(
        {"concepts.json": [concept_item("restore-con-c", "Restore Me")]},
        pack_id="restore",
        version="1.0.0",
    )
    pack_v2 = make_pack(
        {"concepts.json": [concept_item("restore-con-b", "Replacement")]},
        pack_id="restore",
        version="2.0.0",
    )
    source, version1 = await make_source_version(db, pack_v1, uri="test://restore")
    assert (
        await SeedPackApplier().apply(
            [PackApplication(source=source, version=version1, pack=pack_v1)]
        )
    ).ok

    version2 = SeedSourceVersion(
        source_id=source.id,
        digest=pack_v2.digest,
        manifest=pack_v2.manifest,
        contract_version="1.0",
        status=SeedVersionStatus.APPROVED,
        reason="restore test",
    )
    db.add(version2)
    await db.commit()
    await db.refresh(version2)
    assert (
        await SeedPackApplier().apply(
            [PackApplication(source=source, version=version2, pack=pack_v2)]
        )
    ).ok

    old_id = seed_key_uuid("concept", "restore-con-c")
    old = await db.get(Concept, old_id)
    assert old is not None, "retire never deletes"
    prov = await provenance_for(db, "concept", old_id)
    assert prov.state is SeedItemState.RETIRED
    assert old.status is ConceptStatus.RETIRED

    # revocation → the previous approved version is re-applied → restored
    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version1, pack=pack_v1)]
    )
    assert outcome.ok
    prov = await provenance_for(db, "concept", old_id)
    await db.refresh(prov)
    assert prov.state is SeedItemState.ACTIVE
    await db.refresh(old)
    assert old.status is ConceptStatus.ACTIVE

    # cleanup
    new_id = seed_key_uuid("concept", "restore-con-b")
    for cid in (old_id, new_id):
        concept = await db.get(Concept, cid)
        if concept is not None:
            await db.execute(
                text("DELETE FROM concept_kind_tags WHERE concept_id = :cid"), {"cid": cid}
            )
            prov = await provenance_for(db, "concept", cid)
            if prov is not None:
                await db.delete(prov)
            await db.delete(concept)
    await db.commit()


@pytest.mark.asyncio
async def test_replaces_repoints_curated_edges_and_keeps_patient_fks(db):
    """``replaces``: the old row retires, curated system edges repoint to the
    new row, patient ``*_catalog_id`` FKs stay on the old row."""
    pack_v1 = make_pack(
        {
            "concepts.json": [
                concept_item("replaced-old-c", "Replaced Old"),
                concept_item("replace-anchor-c", "Anchor"),
            ],
            "concept_edges.json": [
                {
                    "src_slug": "replaced-old-c",
                    "dst_slug": "replace-anchor-c",
                    "relation": "CORRELATES_WITH",
                }
            ],
        },
        pack_id="replaces",
        version="1.0.0",
    )
    source, version1 = await make_source_version(db, pack_v1, uri="test://replaces")
    assert (
        await SeedPackApplier().apply(
            [PackApplication(source=source, version=version1, pack=pack_v1)]
        )
    ).ok

    old_id = seed_key_uuid("concept", "replaced-old-c")
    anchor_id = seed_key_uuid("concept", "replace-anchor-c")
    tenant_id = await create_tenant()
    patient = Patient(tenant_id=tenant_id, name=[{"text": "Replaces Patient"}], gender=Gender.MALE)
    db.add(patient)
    await db.flush()
    statement = Medication(
        tenant_id=tenant_id,
        patient_id=patient.id,
        code={"text": "Replaced Old"},
    )
    db.add(statement)
    await db.flush()
    statement.medication_catalog_id = None  # patient FKs on CONCEPTS are class refs:
    cls_med = MedicationCatalog(
        seed_key=f"medication:replaces-cls-{uuid.uuid4().hex[:6]}",
        name="Class Holder",
        tenant_id=None,
        class_concept_id=old_id,
    )
    db.add(cls_med)
    await db.commit()

    pack_v2 = make_pack(
        {"concepts.json": [concept_item("replaced-new-c", "Replaced New")]},
        pack_id="replaces",
        version="2.0.0",
        replaces=["replaced-old-c"],
    )
    version2 = SeedSourceVersion(
        source_id=source.id,
        digest=pack_v2.digest,
        manifest=pack_v2.manifest,
        contract_version="1.0",
        status=SeedVersionStatus.APPROVED,
        reason="replaces test",
    )
    db.add(version2)
    await db.commit()
    await db.refresh(version2)

    plan = await SeedPackPlanner(db, source, version2, pack_v2).plan()
    retires = [r for r in plan.retires if r.seed_key == "concept:replaced-old-c"]
    assert retires and retires[0].repoint
    assert retires[0].replaces_new_key == "concept:replaced-new-c"

    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version2, pack=pack_v2)]
    )
    assert outcome.ok, outcome.plan_docs

    new_id = seed_key_uuid("concept", "replaced-new-c")
    edge_id = _seed_edge_identity(
        ("concept", "replaced-old-c"), ("concept", "replace-anchor-c"), "CORRELATES_WITH"
    )
    edge = await db.get(ConceptEdge, edge_id)
    assert edge is not None and edge.src_id == new_id, "the curated edge repointed"
    await db.refresh(old := await db.get(Concept, old_id))
    assert old.status is ConceptStatus.RETIRED
    await db.refresh(cls_med)
    assert cls_med.class_concept_id == old_id, "the patient-side FK stays untouched"

    # cleanup
    for cid in (old_id, new_id, anchor_id):
        concept = await db.get(Concept, cid)
        if concept is not None:
            await db.execute(
                text("DELETE FROM concept_kind_tags WHERE concept_id = :cid"), {"cid": cid}
            )
            prov = await provenance_for(db, "concept", cid)
            if prov is not None:
                await db.delete(prov)
            await db.delete(concept)
    prov = await provenance_for(db, "concept-edge", edge_id)
    if prov is not None:
        await db.delete(prov)
    if edge is not None:
        await db.delete(edge)
    prov_med = await provenance_for(db, "medication", cls_med.id)
    if prov_med is not None:
        await db.delete(prov_med)
    await db.delete(cls_med)
    await db.delete(statement)
    await db.delete(patient)
    await db.commit()


@pytest.mark.asyncio
async def test_concurrent_applies_serialize_on_the_advisory_lock(db):
    """Two system applies racing: both complete OK, serialized by the global
    advisory lock (no deadlock, no interleaved partial state)."""
    pack_a = make_pack(
        {"medications.json": [med_item("race-med-a", "Race Med A")]}, pack_id="race-a"
    )
    pack_b = make_pack(
        {"medications.json": [med_item("race-med-b", "Race Med B")]}, pack_id="race-b"
    )
    source_a, version_a = await make_source_version(db, pack_a, uri="test://race-a")
    source_b, version_b = await make_source_version(db, pack_b, uri="test://race-b")

    applier = SeedPackApplier()
    outcomes = await asyncio.gather(
        applier.apply([PackApplication(source=source_a, version=version_a, pack=pack_a)]),
        applier.apply([PackApplication(source=source_b, version=version_b, pack=pack_b)]),
    )
    for outcome in outcomes:
        assert outcome.ok, outcome.statuses

    for key in ("medication:race-med-a", "medication:race-med-b"):
        await cleanup_med(db, key)


@pytest.mark.asyncio
async def test_apply_refuses_unapproved_version(db):
    pack = make_pack({"medications.json": [med_item("unapproved-med", "Unapproved")]})
    source, version = await make_source_version(db, pack, approved=False)
    with pytest.raises(SeedApplyError, match="APPLY_NOT_APPROVED"):
        await SeedPackApplier().apply([PackApplication(source=source, version=version, pack=pack)])
    await db.delete(version)
    await db.delete(source)
    await db.commit()


@pytest.mark.asyncio
async def test_run_guard_supersedes_fresh_running_under_lock(db):
    """A fresh ``running`` run cannot block an apply that holds the advisory
    lock — a live applier would hold the lock, so the leftover is a crashed
    process: it is superseded (failed + reason) and the apply proceeds."""
    pack = make_pack({"medications.json": [med_item("guard-med-a", "Guard Med A")]})
    source, version = await make_source_version(db, pack, uri="test://guard-a")
    wedged = SeedImportRun(
        source_version_id=version.id,
        mode=SeedRunMode.APPLY,
        status=SeedRunStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db.add(wedged)
    await db.commit()

    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )
    assert outcome.statuses[version.id] is SeedRunStatus.OK, outcome.plan_docs

    await db.refresh(wedged)
    assert wedged.status is SeedRunStatus.FAILED
    assert "held advisory lock" in (wedged.error or "")

    await cleanup_med(db, "medication:guard-med-a")
    await db.delete(version)
    await db.delete(source)
    await db.commit()


@pytest.mark.asyncio
async def test_run_guard_refuses_fresh_running_without_lock(db):
    """Without the lock a fresh ``running`` run may be a genuinely live
    apply — the guard refuses with ``ALREADY_RUNNING`` and leaves it alone."""
    pack = make_pack({"medications.json": [med_item("guard-med-b", "Guard Med B")]})
    source, version = await make_source_version(db, pack, uri="test://guard-b")
    live = SeedImportRun(
        source_version_id=version.id,
        mode=SeedRunMode.APPLY,
        status=SeedRunStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db.add(live)
    await db.commit()

    with pytest.raises(SeedApplyError, match="ALREADY_RUNNING"):
        await SeedPackApplier()._enforce_run_guard(
            db, [PackApplication(source=source, version=version, pack=pack)], locked=False
        )
    await db.refresh(live)
    assert live.status is SeedRunStatus.RUNNING, "a fresh run is not touched without the lock"

    await db.delete(live)
    await db.delete(version)
    await db.delete(source)
    await db.commit()


@pytest.mark.asyncio
async def test_side_stages_run_bundled_only(db, monkeypatch, tmp_path):
    """The app-only side stages run for the bundled source only — a
    non-bundled pack runs its declared stages and never the side stages."""
    monkeypatch.setattr("app.core.config.settings.HA_UPLOAD_DIR", str(tmp_path / "uploads"))
    calls: list[str] = []
    original = SeedService.seed_retention_policies

    async def _spy(self, session):
        calls.append("retention_policies")
        return await original(self, session)

    monkeypatch.setattr(SeedService, "seed_retention_policies", _spy)

    pack = make_pack(
        {"medications.json": [med_item("side-med-a", "Side Med A")]}, pack_id="side-gate"
    )
    source, version = await make_source_version(db, pack, uri="test://side-gate")
    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )
    assert outcome.ok, outcome.plan_docs
    assert calls == [], "a non-bundled pack must not run the app-only side stages"

    bundled_source, bundled_version = await make_source_version(
        db, pack, kind=SeedSourceKind.BUNDLED, uri="test://side-gate-bundled"
    )
    bundled_outcome = await SeedPackApplier().apply(
        [PackApplication(source=bundled_source, version=bundled_version, pack=pack)]
    )
    assert bundled_outcome.ok, bundled_outcome.plan_docs
    assert calls == ["retention_policies"], "the bundled source runs its side stages"

    await cleanup_med(db, "medication:side-med-a")
    await db.delete(bundled_version)
    await db.delete(bundled_source)
    await db.delete(version)
    await db.delete(source)
    await db.commit()


@pytest.mark.asyncio
async def test_bundled_boot_partial_keeps_pointer_and_logs_loudly(
    db, monkeypatch, tmp_path, caplog
):
    """A PARTIAL apply keeps the pointer: the boot does not re-apply (a
    permanent item error must not become a restart loop) — it logs the
    item-error count loudly instead (the Phase 10 re-apply door retries)."""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("app.core.config.settings.HA_UPLOAD_DIR", str(upload_dir))
    seeds = tmp_path / "seeds"
    shutil.copytree(bundled_seeds_dir(), seeds)

    import json

    med_path = seeds / "medications.json"
    data = json.loads(med_path.read_text())
    data["items"][0]["name"] = f"Partial Probe {uuid.uuid4().hex[:8]}"
    med_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    from app.core.seed_pack.bundled import _CACHE, load_bundled_pack

    _CACHE.clear()
    pack = load_bundled_pack(seeds)
    async with AsyncSessionLocal() as session:
        source = await ensure_bundled_source(session)
        version, _created = await ensure_approved_version(
            session, source, pack.digest, pack.manifest
        )
        source.current_version_id = version.id
        session.add(
            SeedImportRun(
                source_version_id=version.id,
                mode=SeedRunMode.APPLY,
                status=SeedRunStatus.PARTIAL,
                stats={"errors": 2},
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        version_id = version.id

    with caplog.at_level(logging.WARNING, logger="app.services.seed.bundled_source"):
        outcome = await apply_bundled_on_boot(seeds)
    assert outcome is None, "a pointer-kept PARTIAL version must not re-apply on boot"
    assert any("PARTIAL" in r.message and "2" in r.message for r in caplog.records), caplog.text

    # cleanup: drop the probe version (cascades its runs; the bundled source
    # row is shared — it stays), then restore the pristine bundled state.
    async with AsyncSessionLocal() as session:
        probe = await session.get(SeedSourceVersion, version_id)
        await session.delete(probe)
        await session.commit()
    _CACHE.clear()
    assert await apply_bundled_on_boot(bundled_seeds_dir()) is not None


@pytest.mark.asyncio
async def test_bundled_boot_applies_on_digest_change_then_skips(monkeypatch, tmp_path):
    """The trusted bundled lane: a fresh digest applies (audited), an
    unchanged digest skips, a re-edited seed applies again."""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("app.core.config.settings.HA_UPLOAD_DIR", str(upload_dir))
    seeds = tmp_path / "seeds"
    shutil.copytree(bundled_seeds_dir(), seeds)

    # make the digest unique per test run (the test DB persists across runs)
    import json

    med_path = seeds / "medications.json"
    data = json.loads(med_path.read_text())
    data["items"][0]["name"] = f"Boot Probe {uuid.uuid4().hex[:8]}"
    med_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    from app.core.seed_pack.bundled import _CACHE

    _CACHE.clear()  # boot reads the seeds dir fresh; the loader caches per dir
    first = await apply_bundled_on_boot(seeds)
    assert first, "a changed bundled digest must apply"
    second = await apply_bundled_on_boot(seeds)
    assert second is None, "an unchanged digest must skip the boot apply"

    data["items"][0]["name"] = f"Boot Probe {uuid.uuid4().hex[:8]}"
    med_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    _CACHE.clear()
    third = await apply_bundled_on_boot(seeds)
    assert third, "re-editing a seed applies again on the next boot"

    # restore the pristine bundled content for the rest of the suite (the
    # probe digest changed item[0]'s name in the shared test DB)
    _CACHE.clear()
    assert await apply_bundled_on_boot(bundled_seeds_dir()) is not None
