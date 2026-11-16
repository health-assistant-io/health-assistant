"""Seed/import identity — PREVENTS edges, edge refs, export round trip (5B).

Pins the Phase-5B contract (ADR 0055, plan A Phase 3 second half):

- the vaccine seeder materializes each seed item's ``target_diseases`` refs
  as approved ``PREVENTS`` ``concept_edges`` rows resolved **by identity**
  (never by name/slug), with deterministic uuid5 edge ids and idempotent
  re-runs — the JSONB column is gone (migration 0012);
- the ``concept_edges`` seeder resolves its readable endpoint refs by seed
  key (``metformin`` → ``medication:metformin``, … — never case-insensitive
  names) and derives deterministic edge ids;
- ``SeedExportService`` emits ``id``/``seed_key``/codes for every catalog,
  excludes the vaccine ``PREVENTS`` edges from ``concept_edges.json`` (they
  export as the readable ``target_diseases`` of ``vaccines.json``), and a
  full export → re-ingest round trip performs zero writes and re-exports
  identical items;
- the vaccine write contract no longer carries ``target_diseases``.

Isolation mirrors the other seed tests: the shared test DB persists across
runs, so synthetic payloads use uuid-suffixed keys and go straight to the
``_process_*`` methods; edge-id assertions first clear the seed-provenance
edges (exactly what the hard reseed deletes) so the run under test recreates
them.
"""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.core.catalog_identity import SEED_KEY_NAMESPACE, seed_key_uuid
from app.core.database import AsyncSessionLocal
from app.models.concept_model import Concept, ConceptEdge, ConceptKindTag
from app.models.enums import (
    CatalogScope,
    ConceptProvenance,
    ConceptRelationType,
    EdgeApprovalStatus,
    EdgeEndpointType,
)
from app.services.seed.service import SeedService
from tests.seed_utils import seed_with_commit

_SEEDS_DIR = Path(__file__).resolve().parent.parent / "data" / "seeds"


def _usuffix(base: str) -> str:
    return f"{base}-{uuid.uuid4().hex[:10]}"


def _make_concept(slug, name, kinds, tenant_id=None) -> Concept:
    c = Concept(
        scope=(CatalogScope.TENANT if tenant_id is not None else CatalogScope.SYSTEM),
        tenant_id=tenant_id,
        slug=slug,
        name=name,
        primary_kind=kinds[0] if kinds else None,
    )
    for k in kinds:
        c.kind_tags.append(ConceptKindTag(kind=k))
    return c


async def _clear_seed_edges() -> None:
    """Delete the system seed-provenance edges (the hard reseed's exact
    scope) so the stage under test recreates them from scratch."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(ConceptEdge).where(
                ConceptEdge.tenant_id.is_(None),
                ConceptEdge.source == ConceptProvenance.SEED,
            )
        )
        await session.commit()


@pytest.fixture
async def db():
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
async def seeded_db(tmp_path, monkeypatch):
    """A session over the canonical seeded state (the exported files'
    source). Local to this module — the pipeline order matters."""
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal

    monkeypatch.setattr(settings, "HA_UPLOAD_DIR", str(tmp_path / "uploads"))
    svc = SeedService()
    # The seeders never own transactions (ADR 0056): one session, one commit.
    async with AsyncSessionLocal() as session:
        for stage in _STAGES:
            await getattr(svc, f"seed_{stage}")(session, *_stage_args(svc, stage))
        await session.commit()
    async with AsyncSessionLocal() as session:
        yield session


async def _prevents_edges(session, vaccine_id) -> list[ConceptEdge]:
    return (
        (
            await session.execute(
                select(ConceptEdge)
                .where(
                    ConceptEdge.src_type == EdgeEndpointType.IMMUNIZATION,
                    ConceptEdge.src_id == vaccine_id,
                    ConceptEdge.relation == ConceptRelationType.PREVENTS,
                    ConceptEdge.tenant_id.is_(None),
                )
                .order_by(ConceptEdge.dst_id)
            )
        )
        .scalars()
        .all()
    )


# ===========================================================================
# Vaccine seeder → PREVENTS edges
# ===========================================================================


@pytest.mark.asyncio
async def test_vaccine_seeder_creates_prevents_edges_by_identity():
    """The shipped vaccines seed creates one approved seed edge per curated
    ``target_diseases`` ref, resolved by identity, with uuid5 edge ids; the
    second run adds nothing."""
    from app.models.fhir.vaccine import VaccineCatalog

    await _clear_seed_edges()
    svc = SeedService()
    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_diseases)
    stats = await seed_with_commit(svc.seed_vaccines)
    assert stats["errors"] == 0, stats
    assert stats["skipped"] == 0, f"every curated target disease must resolve: {stats}"
    assert stats["added"] >= 12, f"7 curated vaccines carry 12 refs: {stats}"

    async with AsyncSessionLocal() as session:
        mmr = (
            await session.execute(
                select(VaccineCatalog).where(VaccineCatalog.seed_key == "vaccine:mmr")
            )
        ).scalar_one()
        edges = await _prevents_edges(session, mmr.id)
        assert len(edges) == 3, f"MMR must prevent 3 diseases, got {len(edges)}"
        for edge in edges:
            assert edge.source == ConceptProvenance.SEED
            assert edge.status == EdgeApprovalStatus.APPROVED
            assert edge.dst_type == EdgeEndpointType.CONCEPT
        expected = seed_key_uuid("vaccine-prevents", "mmr/measles")
        assert any(e.id == expected for e in edges), "edge id is not the uuid5 identity"
        assert uuid.uuid5(SEED_KEY_NAMESPACE, "vaccine-prevents:mmr/measles") == expected

    second = await seed_with_commit(svc.seed_vaccines)
    assert second["errors"] == 0 and second["added"] == 0, second
    assert second["updated"] == 0, f"second run rewrote rows: {second}"
    assert second["skipped"] == 0, second


@pytest.mark.asyncio
async def test_vaccine_seeder_skips_unresolvable_refs_without_failing():
    """A target_diseases ref with no seeded disease concept is skipped with a
    warning — never guessed by name — and the vaccine row still seeds."""
    from app.models.fhir.vaccine import VaccineCatalog

    svc = SeedService()
    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_diseases)
    key = f"vaccine:{_usuffix('probe')}"
    slug = _usuffix("probe-vac")
    async with AsyncSessionLocal() as session:
        stats = await svc._process_vaccines(
            session,
            {
                "items": [
                    {
                        "seed_key": key,
                        "slug": slug,
                        "name": "Probe Vaccine",
                        "target_diseases": ["not-a-seeded-disease"],
                    }
                ]
            },
        )
        await session.commit()
    assert stats["errors"] == 0, stats
    assert stats["added"] == 1, stats
    assert stats["skipped"] == 1, f"the unresolvable ref must count as skipped: {stats}"

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(VaccineCatalog).where(VaccineCatalog.seed_key == key))
        ).scalar_one()
        assert await _prevents_edges(session, row.id) == []
        await session.execute(delete(VaccineCatalog).where(VaccineCatalog.seed_key == key))
        await session.commit()


# ===========================================================================
# concept_edges seeder — identity-resolved refs, deterministic ids
# ===========================================================================


@pytest.mark.asyncio
async def test_concept_edges_seeder_resolves_medication_by_seed_key():
    """The shipped concept_edges file resolves medication endpoints by seed
    key (``metformin`` → ``medication:metformin``), never by name, and the
    TREATS edges land with deterministic ids; a second run adds nothing."""
    await _clear_seed_edges()
    svc = SeedService()
    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_diseases)
    await seed_with_commit(svc.seed_medications)
    await seed_with_commit(svc.seed_body_parts)
    await seed_with_commit(svc.seed_default_catalog)
    stats = await seed_with_commit(svc.seed_concept_edges)
    assert stats["errors"] == 0, stats
    assert stats["skipped"] == 0, f"every shipped ref must resolve: {stats}"

    expected_metformin = seed_key_uuid("medication", "metformin")
    expected_t2dm = seed_key_uuid("concept", "type-2-diabetes")
    expected_edge = uuid.uuid5(
        SEED_KEY_NAMESPACE,
        "concept-edge:medication:metformin/concept:type-2-diabetes/TREATS",
    )
    async with AsyncSessionLocal() as session:
        edge = (
            await session.execute(
                select(ConceptEdge).where(
                    ConceptEdge.src_type == EdgeEndpointType.MEDICATION,
                    ConceptEdge.src_id == expected_metformin,
                    ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                    ConceptEdge.dst_id == expected_t2dm,
                    ConceptEdge.relation == ConceptRelationType.TREATS,
                    ConceptEdge.tenant_id.is_(None),
                )
            )
        ).scalar_one()
        assert edge.id == expected_edge, "edge id is not the deterministic identity"
        assert edge.source == ConceptProvenance.SEED
        assert edge.status == EdgeApprovalStatus.APPROVED

    second = await seed_with_commit(svc.seed_concept_edges)
    assert second["added"] == 0 and second["errors"] == 0, second
    assert second["updated"] == 0, f"second run rewrote edges: {second}"
    assert second["skipped"] == 0, second


@pytest.mark.asyncio
async def test_concept_edges_seeder_skips_unknown_refs(db):
    """An endpoint ref that resolves to no seeded row is skipped (never
    created by name or slug guesswork)."""
    svc = SeedService()
    payload = {
        "items": [
            {
                "src_slug": _usuffix("no-such-med"),
                "src_type": "medication",
                "dst_slug": "type-2-diabetes",
                "relation": "TREATS",
            }
        ]
    }
    stats = await svc._process_concept_edges(db, payload)
    await db.commit()
    assert stats["errors"] == 0 and stats["added"] == 0 and stats["skipped"] == 1, stats


# ===========================================================================
# Export → re-ingest round trip
# ===========================================================================

_STAGES = (
    # the canonical seed_all order (subset) — concept_edges resolves the
    # file's biomarker endpoints, so it runs after default_catalog
    "concepts",
    "diseases",
    "medications",
    "vaccines",
    "clinical_event_types",
    "allergies",
    "body_parts",
    "biomarker_states",
    "default_catalog",
    "concept_edges",
    "biomarker_panels",
)


def _stage_args(svc: SeedService, stage: str) -> tuple:
    """The parsed content of one stage's export file in ``svc.seeds_dir``."""
    from app.core.seed_pack.items import REGISTRY

    file_for = {item.stage: item.filename for item in REGISTRY}
    path = svc.seeds_dir / file_for[stage]
    if not path.exists():
        # the exporter does not cover every stage yet (states/OMB) — an
        # absent file is the old skip behavior, not an error
        return ({},) if stage == "default_catalog" else ([],)
    data = json.loads(path.read_bytes())
    return (data,) if stage == "default_catalog" else (data.get("items", []),)


async def _ingest_dir(svc: SeedService) -> dict[str, dict[str, int]]:
    """Run every exported stage against ``svc.seeds_dir`` (an export dir).

    One session, one commit — the seeders never own the transaction (ADR 0056).
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        stats = {
            stage: await getattr(svc, f"seed_{stage}")(session, *_stage_args(svc, stage))
            for stage in _STAGES
        }
        await session.commit()
        return stats


def _write_export(payloads: dict, out_dir: Path) -> None:
    """Write the export for re-ingest, scoped to the curated subset.

    The export carries every system row of the shared DB — including the
    random-id rows other tests leave behind (no ``seed_key``). Those are not
    seed data: the identity seeders reject or re-create them by design, so
    the round trip — whose contract is the curated set — ingests only items
    that carry a ``seed_key`` (units are the exception: symbol upsert makes
    hint-less units a no-op). Edges/panels always resolve or skip.
    """
    from app.services.seed.seed_export_service import SeedExportService

    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in payloads.items():
        if "items" in payload:
            payload = {
                **payload,
                "items": [it for it in payload["items"] if it.get("seed_key")],
            }
        elif "biomarkers" in payload:
            payload = {
                **payload,
                "biomarkers": [b for b in payload["biomarkers"] if b.get("seed_key")],
            }
        (out_dir / filename).write_bytes(SeedExportService._serialize(payload))


async def _export_all() -> dict:
    from app.services.seed.seed_export_service import SeedExportService

    async with AsyncSessionLocal() as session:
        return await SeedExportService(session, tenant_id=None).export_all()


@pytest.mark.asyncio
async def test_export_reingest_round_trip_performs_no_writes(tmp_path, monkeypatch):
    """Export the running instance → feed the export back through the seed
    pipeline → zero added, zero updated, zero errors (a second import also
    writes nothing), and a re-export is identical on the item bodies."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "HA_UPLOAD_DIR", str(tmp_path / "uploads"))

    svc = SeedService()
    from app.core.database import AsyncSessionLocal as _ASL

    async with _ASL() as session:
        for stage in _STAGES:
            await getattr(svc, f"seed_{stage}")(session, *_stage_args(svc, stage))
        await session.commit()

    payloads = await _export_all()
    export_dir = tmp_path / "seeds"
    _write_export(payloads, export_dir)

    round_trip = SeedService()
    round_trip.seeds_dir = export_dir
    first = await _ingest_dir(round_trip)
    offenders = {
        stage: (stats.get("added"), stats.get("updated"), stats.get("errors"))
        for stage, stats in first.items()
        if (stats.get("added") or stats.get("updated") or stats.get("errors"))
    }
    assert not offenders, f"re-ingesting the export must write nothing: {offenders}"

    second = await _ingest_dir(round_trip)
    offenders2 = {
        stage: (stats.get("added"), stats.get("updated"), stats.get("errors"))
        for stage, stats in second.items()
        if (stats.get("added") or stats.get("updated") or stats.get("errors"))
    }
    assert not offenders2, f"the second import must also write nothing: {offenders2}"

    reexport = await _export_all()
    assert set(reexport) == set(payloads)
    for filename, payload in payloads.items():
        for key in {k for k in payload if k != "metadata"}:
            assert reexport[filename][key] == payload[key], f"{filename}.{key} drifted"
    # the curated subset stayed byte-stable through the round trip
    for filename in ("concepts.json", "diseases.json", "vaccines.json", "medications.json"):
        curated = [it for it in payloads[filename]["items"] if it.get("seed_key")]
        re_curated = {
            it["seed_key"]: it for it in reexport[filename]["items"] if it.get("seed_key")
        }
        for it in curated:
            assert re_curated[it["seed_key"]] == it, f"{filename}:{it['seed_key']} drifted"


@pytest.mark.asyncio
async def test_export_curated_rows_carry_identity(seeded_db):
    """Every exported catalog item carries its id; curated rows carry their
    seed_key; medication items carry their codes — items sort by seed_key."""
    from app.models.fhir.medication import MedicationCatalog
    from app.services.seed.seed_export_service import SeedExportService

    probe = MedicationCatalog(
        scope=CatalogScope.SYSTEM,
        tenant_id=None,
        name=_usuffix("Probe Med"),
        seed_key=None,
    )
    seeded_db.add(probe)
    await seeded_db.flush()

    svc = SeedExportService(seeded_db)
    meds = await svc.export_medications()
    keys = [it.get("seed_key") for it in meds["items"]]
    curated = [k for k in keys if k]
    assert len(curated) >= 7, "the curated medications must export seed_keys"
    # the seed_key subsequence is globally sorted (items sort by seed_key,
    # slug/name fallback for rows outside the curated identity)
    assert curated == sorted(curated), "items must sort by seed_key"
    metformin = next(it for it in meds["items"] if it.get("seed_key") == "medication:metformin")
    assert metformin["coding_system"] == "atc" and metformin["code"] == "A10BA02"
    assert uuid.UUID(metformin["id"]) == seed_key_uuid("medication", "metformin")
    # the mixed contract: a non-curated row is still exported, with its id
    # but without a seed_key (the curated identity is optional per row)
    probe_item = next(it for it in meds["items"] if it["id"] == str(probe.id))
    assert "seed_key" not in probe_item
    assert uuid.UUID(probe_item["id"]) == probe.id
    await seeded_db.delete(probe)
    await seeded_db.flush()

    concepts = await svc.export_concepts()
    assert all("id" in it for it in concepts["items"])
    cardiology = next(it for it in concepts["items"] if it.get("seed_key") == "concept:cardiology")
    assert cardiology["slug"] == "cardiology"

    panels = await svc.export_biomarker_panels()
    assert all(
        it["seed_key"] == f"panel-membership:{it['panel_slug']}/{it['biomarker_slug']}"
        for it in panels["items"]
    ), "panel memberships export their derived seed_key"


def _load_shipped(filename):
    return json.loads((_SEEDS_DIR / filename).read_text())


@pytest.mark.asyncio
async def test_export_vaccines_derives_target_diseases_from_edges(seeded_db):
    """``vaccines.json`` exports the readable ``target_diseases`` derived from
    the approved PREVENTS edges, and ``concept_edges.json`` excludes exactly
    those edges (one owner per dataset)."""
    from app.services.seed.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    vaccines = await svc.export_vaccines()
    mmr = next(it for it in vaccines["items"] if it["slug"] == "mmr")
    assert mmr["seed_key"] == "vaccine:mmr"
    assert mmr["target_diseases"] == ["measles", "mumps", "rubella"]

    edges = await svc.export_concept_edges()
    prevents = [
        it
        for it in edges["items"]
        if it["relation"] == "PREVENTS" and it.get("src_type") == "immunization"
    ]
    assert prevents == [], "vaccine PREVENTS edges must export via vaccines.json"

    shipped = _load_shipped("vaccines.json")["items"]
    by_slug = {it["slug"]: it for it in vaccines["items"]}
    for s in shipped:
        got = by_slug[s["slug"]]
        assert got["name"] == s["name"]
        assert got.get("target_diseases") == s.get("target_diseases"), s["slug"]


# ===========================================================================
# Identity safety — tenant collisions, pending proposals, export exactness
# ===========================================================================


@pytest.mark.asyncio
async def test_panel_membership_binds_to_system_rows_not_tenant_collisions():
    """A tenant biomarker (or tenant panel concept) whose slug collides with a
    curated panel member is NEVER bound into a system ``source=seed``
    MEMBER_OF edge: both endpoints resolve by identity (derived uuid5 of the
    qualified seed key, then the ``seed_key`` column, system rows only)."""
    from uuid import uuid4 as new_uuid

    from app.models.biomarker_model import BiomarkerDefinition
    from app.models.enums import CodingSystem, ConceptKind
    from app.models.tenant_model import TenantModel

    await _clear_seed_edges()
    svc = SeedService()
    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_default_catalog)

    tenant_id = new_uuid()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(id=tenant_id, name=f"panel-{tenant_id}", slug=f"pnl-{tenant_id.hex[:8]}")
        )
        await session.flush()
        # Tenant rows colliding with the curated lipid-panel membership slugs.
        session.add(
            BiomarkerDefinition(
                slug="cholesterol-total",
                name="Tenant Cholesterol",
                coding_system=CodingSystem.CUSTOM,
                tenant_id=tenant_id,
                scope=CatalogScope.TENANT,
            )
        )
        session.add(
            _make_concept(
                "lipid-panel",
                "Tenant Lipid Panel",
                [ConceptKind.BIOMARKER_PANEL],
                tenant_id=tenant_id,
            )
        )
        await session.commit()

    try:
        stats = await seed_with_commit(svc.seed_biomarker_panels)
        assert stats["errors"] == 0, stats

        expected_edge = seed_key_uuid("panel-membership", "lipid-panel/cholesterol-total")
        expected_bio = seed_key_uuid("biomarker", "cholesterol-total")
        expected_panel = seed_key_uuid("concept", "lipid-panel")

        async with AsyncSessionLocal() as session:
            tenant_bio_id = (
                await session.execute(
                    select(BiomarkerDefinition.id).where(
                        BiomarkerDefinition.slug == "cholesterol-total",
                        BiomarkerDefinition.tenant_id == tenant_id,
                    )
                )
            ).scalar_one()
            tenant_panel_id = (
                await session.execute(
                    select(Concept.id).where(
                        Concept.slug == "lipid-panel", Concept.tenant_id == tenant_id
                    )
                )
            ).scalar_one()
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
            members = [e for e in edges if e.src_id in (expected_bio, tenant_bio_id)]
            assert members, "the curated membership edge is missing"
            for edge in members:
                assert edge.id == expected_edge
                assert edge.source == ConceptProvenance.SEED
                assert edge.status == EdgeApprovalStatus.APPROVED
                assert edge.src_id == expected_bio, (
                    "the edge must bind the SYSTEM biomarker, never the tenant collision"
                )
                assert edge.dst_id == expected_panel, (
                    "the edge must bind the SYSTEM panel concept, never the tenant collision"
                )
            assert all(e.src_id != tenant_bio_id for e in edges)
            assert all(e.dst_id != tenant_panel_id for e in edges)
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(BiomarkerDefinition).where(BiomarkerDefinition.tenant_id == tenant_id)
            )
            await session.execute(delete(Concept).where(Concept.tenant_id == tenant_id))
            await session.execute(delete(TenantModel).where(TenantModel.id == tenant_id))
            await session.commit()


@pytest.mark.asyncio
async def test_pending_edge_does_not_block_the_approved_seed_edge(db):
    """A pending (HITL-proposed) edge between two seeded endpoints must not
    suppress the curated approved edge: only an APPROVED tuple short-circuits
    the concept-edges and panel-membership seeders (the approved edge and the
    proposal then coexist, per the approved-edge unique index)."""
    from uuid import uuid4 as new_uuid

    svc = SeedService()
    # Clear the seed-provenance edges first (the hard reseed's scope) so the
    # run under test must CREATE the approved edges despite the proposals.
    await _clear_seed_edges()
    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_diseases)
    await seed_with_commit(svc.seed_medications)
    await seed_with_commit(svc.seed_default_catalog)

    med_id = seed_key_uuid("medication", "metformin")
    t2dm_id = seed_key_uuid("concept", "type-2-diabetes")
    bio_id = seed_key_uuid("biomarker", "cholesterol-total")
    panel_id = seed_key_uuid("concept", "lipid-panel")

    proposal_treats = ConceptEdge(
        id=new_uuid(),
        src_type=EdgeEndpointType.MEDICATION,
        src_id=med_id,
        dst_type=EdgeEndpointType.CONCEPT,
        dst_id=t2dm_id,
        relation=ConceptRelationType.TREATS,
        tenant_id=None,
        source=ConceptProvenance.AI,
        status=EdgeApprovalStatus.PROPOSED,
    )
    proposal_member = ConceptEdge(
        id=new_uuid(),
        src_type=EdgeEndpointType.BIOMARKER,
        src_id=bio_id,
        dst_type=EdgeEndpointType.CONCEPT,
        dst_id=panel_id,
        relation=ConceptRelationType.MEMBER_OF,
        tenant_id=None,
        source=ConceptProvenance.AI,
        status=EdgeApprovalStatus.PROPOSED,
    )
    db.add_all([proposal_treats, proposal_member])
    await db.commit()

    try:
        edges_stats = await seed_with_commit(svc.seed_concept_edges)
        assert edges_stats["errors"] == 0, edges_stats
        panels_stats = await seed_with_commit(svc.seed_biomarker_panels)
        assert panels_stats["errors"] == 0, panels_stats

        expected_treats = uuid.uuid5(
            SEED_KEY_NAMESPACE,
            "concept-edge:medication:metformin/concept:type-2-diabetes/TREATS",
        )
        expected_member = seed_key_uuid("panel-membership", "lipid-panel/cholesterol-total")
        async with AsyncSessionLocal() as session:
            treats = (
                (
                    await session.execute(
                        select(ConceptEdge).where(
                            ConceptEdge.src_type == EdgeEndpointType.MEDICATION,
                            ConceptEdge.src_id == med_id,
                            ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                            ConceptEdge.dst_id == t2dm_id,
                            ConceptEdge.relation == ConceptRelationType.TREATS,
                            ConceptEdge.tenant_id.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            approved = [e for e in treats if e.status == EdgeApprovalStatus.APPROVED]
            assert approved, "the pending proposal suppressed the approved seed edge"
            assert any(e.id == expected_treats for e in approved)

            member = (
                await session.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.id == expected_member,
                        ConceptEdge.tenant_id.is_(None),
                    )
                )
            ).scalar_one_or_none()
            assert member is not None, "the pending proposal blocked the panel seed edge"
            assert member.status == EdgeApprovalStatus.APPROVED

            proposals = (
                (
                    await session.execute(
                        select(ConceptEdge).where(
                            ConceptEdge.id.in_([proposal_treats.id, proposal_member.id]),
                            ConceptEdge.status == EdgeApprovalStatus.PROPOSED,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(proposals) == 2, "the proposals must survive untouched"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(ConceptEdge).where(
                    ConceptEdge.id.in_([proposal_treats.id, proposal_member.id])
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_export_concept_edges_keeps_immunization_non_concept_prevents(db):
    """The PREVENTS exclusion in ``concept_edges.json`` is exact: only the
    vaccine → disease shape (immunization → concept) is excluded. An
    immunization → non-concept PREVENTS edge exports here — and its emitted
    ``immunization`` tag re-ingests through the edge seeder — so no edge
    vanishes from the files."""
    from uuid import uuid4 as new_uuid

    from app.models.anatomy_model import AnatomyStructure
    from app.models.fhir.vaccine import VaccineCatalog
    from app.services.seed.seed_export_service import SeedExportService

    vslug, aslug = _usuffix("probe-vac"), _usuffix("probe-organ")
    vaccine = VaccineCatalog(
        seed_key=f"vaccine:{vslug}",
        slug=vslug,
        name="Probe Vac",
        coding_system="cvx",
        scope=CatalogScope.SYSTEM,
    )
    # Anatomy carries no seed_key column — the identity is the derived id,
    # which is exactly what the edge seeder resolves by.
    structure = AnatomyStructure(
        id=seed_key_uuid("anatomy", aslug),
        tenant_id=None,
        name="Probe Organ",
        slug=aslug,
    )
    db.add_all([vaccine, structure])
    await db.flush()
    db.add(
        ConceptEdge(
            id=new_uuid(),
            src_type=EdgeEndpointType.IMMUNIZATION,
            src_id=vaccine.id,
            dst_type=EdgeEndpointType.ANATOMY,
            dst_id=structure.id,
            relation=ConceptRelationType.PREVENTS,
            tenant_id=None,
            source=ConceptProvenance.MANUAL,
            status=EdgeApprovalStatus.APPROVED,
        )
    )
    await db.commit()

    try:
        exported = await SeedExportService(db).export_concept_edges()
        item = next(
            (
                it
                for it in exported["items"]
                if it.get("src_type") == "immunization" and it.get("dst_type") == "anatomy"
            ),
            None,
        )
        assert item is not None, "the immunization→anatomy PREVENTS edge must export"
        assert item["src_slug"] == vslug and item["relation"] == "PREVENTS"

        # the vaccine→disease shape stays excluded (vaccines.json owns it)
        assert not any(
            it["relation"] == "PREVENTS"
            and it.get("src_type") == "immunization"
            and it.get("dst_type") == "concept"
            for it in exported["items"]
        )

        # the emitted item re-ingests: the seeder resolves both endpoints
        # (the tuple already exists approved, so it reports unchanged — the
        # point is it resolves rather than skips)
        svc = SeedService()
        stats = await svc._process_concept_edges(db, {"items": [item]})
        await db.commit()
        assert stats["errors"] == 0 and stats["skipped"] == 0, stats
        assert stats["added"] + stats["unchanged"] == 1, (
            f"the re-ingested edge must resolve: {stats}"
        )
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(ConceptEdge).where(
                    ConceptEdge.src_type == EdgeEndpointType.IMMUNIZATION,
                    ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                    ConceptEdge.relation == ConceptRelationType.PREVENTS,
                )
            )
            await session.execute(delete(VaccineCatalog).where(VaccineCatalog.slug == vslug))
            await session.execute(delete(AnatomyStructure).where(AnatomyStructure.slug == aslug))
            await session.commit()


# ===========================================================================
# Write contract — target_diseases is gone
# ===========================================================================


def test_vaccine_write_rejects_target_diseases():
    """The write contract no longer carries ``target_diseases``: both the
    create and the update schema answer 422 (``extra="forbid"``), and the
    response shape has no such key."""
    from app.schemas.vaccine import VaccineCatalogCreate, VaccineCatalogUpdate

    with pytest.raises(ValueError):
        VaccineCatalogCreate.model_validate({"name": "X", "target_diseases": ["measles"]})
    with pytest.raises(ValueError):
        VaccineCatalogUpdate.model_validate({"target_diseases": ["measles"]})
    row = VaccineCatalogCreate.model_validate({"name": "Probe Only"})
    assert "target_diseases" not in row.model_dump()
