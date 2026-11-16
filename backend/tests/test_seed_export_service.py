"""Tests for the seed export service + lossless seed-format reads.

Layered:
- Phase 1: the seed *ingest* path reads the new lossless fields
  (``class_concept_slug`` on anatomy; ``biomarker`` endpoint type on edges).
- Phase 2: ``SeedExportService`` is the faithful inverse of ``SeedService`` —
  round-trip equality against the shipped seeds, plus a lossless-field guard
  and determinism.
- Phase 3: the CLI + safety pipeline.

Isolation: global seed rows persist across runs in the shared test DB, so every
slug is uuid-suffixed via ``_uslug``. Synthetic payloads are fed straight to the
``_process_*`` methods (bypassing file load) so the shipped seed files aren't
mutated.
"""

import io
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept, ConceptEdge, ConceptKindTag
from app.models.enums import (
    CatalogScope,
    CodingSystem,
    ConceptKind,
    ConceptRelationType,
    EdgeEndpointType,
)
from app.services.seed.service import SeedService
from tests.seed_utils import seed_with_commit


def _uslug(base: str) -> str:
    return f"{base}-{uuid4().hex[:10]}"


def _make_concept(slug, name, kinds, tenant_id=None, primary=None) -> Concept:
    c = Concept(
        scope=(CatalogScope.TENANT if tenant_id is not None else CatalogScope.SYSTEM),
        tenant_id=tenant_id,
        slug=slug,
        name=name,
        primary_kind=primary or (kinds[0] if kinds else None),
    )
    for k in kinds:
        c.kind_tags.append(ConceptKindTag(kind=k))
    return c


@pytest.fixture
async def db():
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


# ===========================================================================
# Phase 1 — lossless seed reads
# ===========================================================================


@pytest.mark.asyncio
async def test_anatomy_seed_prefers_class_concept_slug_over_legacy_category(db):
    """When an anatomy seed item carries BOTH ``class_concept_slug`` and the
    legacy ``category`` enum, the explicit slug wins (points at a different
    concept than the enum would). This is what makes the format lossless."""
    svc = SeedService()
    target = _make_concept(
        _uslug("custom-anatomy-class"), "Custom Class", [ConceptKind.ANATOMY_CLASS]
    )
    db.add(target)
    await db.flush()
    target_slug = target.slug

    node_slug = _uslug("widget-organ")
    payload = {
        "nodes": [
            {
                "seed_key": f"anatomy:{node_slug}",
                "slug": node_slug,
                "name": "Widget Organ",
                "category": "ORGAN",  # legacy — would resolve to "organ"
                "class_concept_slug": target_slug,  # explicit — must win
            }
        ],
        "edges": [],
    }
    await svc._process_body_parts(db, payload)
    await db.commit()

    struct = (
        await db.execute(select(AnatomyStructure).where(AnatomyStructure.slug == node_slug))
    ).scalar_one()
    assert struct.class_concept_id == target.id  # NOT the legacy "organ"


@pytest.mark.asyncio
async def test_anatomy_seed_class_concept_slug_handles_non_legacy_class(db):
    """An anatomy class concept whose slug is NOT in the legacy enum map
    round-trips via ``class_concept_slug`` alone (no legacy ``category``).
    The legacy path couldn't represent this — the lossless-field guard."""
    svc = SeedService()
    niche = _make_concept(
        _uslug("lymphatic-structure"),
        "Lymphatic Structure",
        [ConceptKind.ANATOMY_CLASS],
    )
    db.add(niche)
    await db.flush()

    node_slug = _uslug("lymph-node")
    payload = {
        "nodes": [
            {
                "seed_key": f"anatomy:{node_slug}",
                "slug": node_slug,
                "name": "Lymph Node",
                "class_concept_slug": niche.slug,  # no legacy category at all
            }
        ],
        "edges": [],
    }
    await svc._process_body_parts(db, payload)
    await db.commit()

    struct = (
        await db.execute(select(AnatomyStructure).where(AnatomyStructure.slug == node_slug))
    ).scalar_one()
    assert struct.class_concept_id == niche.id


@pytest.mark.asyncio
async def test_concept_edge_seed_resolves_biomarker_endpoint(db):
    """The concept-edge seed ingest resolves a ``biomarker`` endpoint type by
    identity (the uuid5 of ``biomarker:<key>`` / the ``seed_key`` column) —
    not just concept/anatomy. Unblocks round-tripping biomarker-level edges
    beyond panel membership."""
    from app.core.catalog_identity import seed_key_uuid

    svc = SeedService()
    panel_key = _uslug("my-panel")
    panel = _make_concept(panel_key, "My Panel", [ConceptKind.BIOMARKER_PANEL])
    panel.id = seed_key_uuid("concept", panel_key)
    panel.seed_key = f"concept:{panel_key}"
    bio_key = _uslug("widget-biomarker")
    bio_slug = bio_key
    bio = BiomarkerDefinition(
        id=seed_key_uuid("biomarker", bio_key),
        seed_key=f"biomarker:{bio_key}",
        slug=bio_slug,
        name="Widget Biomarker",
        coding_system=CodingSystem.CUSTOM,
    )
    db.add_all([panel, bio])
    await db.flush()

    payload = {
        "items": [
            {
                "src_type": "biomarker",
                "src_slug": bio_key,
                "dst_type": "concept",
                "dst_slug": panel_key,
                "relation": "MEMBER_OF",
            }
        ]
    }
    stats = await svc._process_concept_edges(db, payload)
    await db.commit()

    assert stats["added"] == 1
    edge = (
        await db.execute(
            select(ConceptEdge).where(
                ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                ConceptEdge.src_id == bio.id,
            )
        )
    ).scalar_one()
    assert edge.dst_type == EdgeEndpointType.CONCEPT
    assert edge.dst_id == panel.id
    assert edge.relation == ConceptRelationType.MEMBER_OF


# ===========================================================================
# Phase 2 — SeedExportService (inverse of SeedService)
# ===========================================================================


# The shipped seeds live in backend/data/seeds/. Used for the round-trip test.
def _shipped_seeds_dir() -> Path:
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / "data" / "seeds"


def _load_shipped(filename: str) -> dict[str, Any]:
    return json.loads((_shipped_seeds_dir() / filename).read_text())


@pytest.fixture
async def seeded_db():
    """A session with the shipped seeds loaded (global taxonomy + catalogs).
    Used by the round-trip-preservation tests."""
    from app.core.database import AsyncSessionLocal

    svc = SeedService()
    # Ordered so dependencies exist (concepts before edges/catalog/panels).
    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_diseases)
    await seed_with_commit(svc.seed_body_parts)
    await seed_with_commit(svc.seed_vaccines)
    await seed_with_commit(svc.seed_medications)
    await seed_with_commit(svc.seed_concept_edges)
    await seed_with_commit(svc.seed_default_catalog)
    await seed_with_commit(svc.seed_biomarker_panels)
    await seed_with_commit(svc.seed_biomarker_states)
    await seed_with_commit(svc.seed_clinical_event_types)
    await seed_with_commit(svc.seed_allergies)
    async with AsyncSessionLocal() as session:
        yield session


def _by_key(items, key="slug"):
    return {it[key]: it for it in items}


@pytest.mark.asyncio
async def test_export_concepts_preserves_seeded_data(seeded_db):
    """Every shipped concept slug is exported with matching name + kinds (as a
    set — order is not semantically meaningful). Proves the export doesn't
    lose or corrupt the curated taxonomy."""
    from app.services.seed.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_concepts()
    shipped = _load_shipped("concepts.json")["items"]
    by_slug = _by_key(out["items"])
    assert len(out["items"]) >= len(shipped), "export dropped concepts"
    for s in shipped:
        got = by_slug.get(s["slug"])
        assert got is not None, f"missing concept {s['slug']}"
        assert got["name"] == s["name"]
        assert set(got["kinds"]) == set(s["kinds"])


@pytest.mark.asyncio
async def test_export_concept_edges_preserves_seeded_data(seeded_db):
    from app.services.seed.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_concept_edges()
    shipped = _load_shipped("concept_edges.json")["items"]
    shipped_set = {(s["src_slug"], s["dst_slug"], s["relation"]) for s in shipped}
    got_set = {(it["src_slug"], it["dst_slug"], it["relation"]) for it in out["items"]}
    assert shipped_set <= got_set, "export dropped edges"


@pytest.mark.asyncio
async def test_export_diseases_preserves_seeded_data(seeded_db):
    """Every shipped disease concept is exported with matching coding_system +
    code. Also verifies diseases are NOT duplicated into concepts.json (the
    export keeps the two files as the true inverse of the seed pipeline)."""
    from app.services.seed.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_diseases()
    shipped = _load_shipped("diseases.json")["items"]
    by_slug = _by_key(out["items"])
    assert len(out["items"]) >= len(shipped), "export dropped diseases"
    for s in shipped:
        got = by_slug.get(s["slug"])
        assert got is not None, f"missing disease {s['slug']}"
        assert got["coding_system"] == s["coding_system"]
        assert got["code"] == s["code"]

    # Diseases must NOT appear in the concepts.json export (separate file).
    concepts_out = await SeedExportService(seeded_db).export_concepts()
    concept_slugs = {c["slug"] for c in concepts_out["items"]}
    disease_slugs = {s["slug"] for s in shipped}
    assert not (disease_slugs & concept_slugs), "diseases leaked into concepts.json"


@pytest.mark.asyncio
async def test_export_anatomy_preserves_seeded_data(seeded_db):
    from app.services.seed.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    out = await svc.export_anatomy_structures()
    shipped = _load_shipped("anatomy_structures.json")["items"]
    by_slug = _by_key(out["items"])
    for s in shipped:
        got = by_slug.get(s["slug"])
        assert got is not None, f"missing anatomy {s['slug']}"
        assert got["name"] == s["name"]
        # class concept resolved + legacy category preserved for backward compat
        if "category" in s:
            assert got.get("category") == s["category"]
        assert "class_concept_slug" in got  # the lossless field is always emitted


@pytest.mark.asyncio
async def test_export_anatomy_relations_preserves_seeded_data(seeded_db):
    """The export function queries concept_edges for anatomy→anatomy edges
    (the anatomy_relations table is dropped; edges were merged into
    concept_edges.json)."""
    from app.services.seed.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    out = await svc.export_anatomy_relations()
    # The anatomy edges are now in concept_edges.json (src_type=anatomy,
    # dst_type=anatomy). Load them and map to the same {source_slug,
    # target_slug, relation_type} shape the exporter emits.
    shipped_edges = _load_shipped("concept_edges.json")["items"]
    shipped_set = {
        (e["src_slug"], e["dst_slug"], e["relation"])
        for e in shipped_edges
        if e.get("src_type") == "anatomy" and e.get("dst_type") == "anatomy"
    }
    got_set = {(it["source_slug"], it["target_slug"], it["relation_type"]) for it in out["items"]}
    assert shipped_set <= got_set, "export dropped anatomy relations"


@pytest.mark.asyncio
async def test_export_default_catalog_preserves_seeded_data(seeded_db):
    from app.services.seed.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_default_catalog()
    shipped = json.loads((_shipped_seeds_dir() / "default_catalog.json").read_text())
    shipped_slugs = {b["slug"] for b in shipped["biomarkers"]}
    got_slugs = {b["slug"] for b in out["biomarkers"]}
    assert shipped_slugs <= got_slugs, "export dropped biomarkers"
    # every shipped biomarker preserves its strong identity fields
    got_by_slug = _by_key(out["biomarkers"])
    for b in shipped["biomarkers"]:
        g = got_by_slug[b["slug"]]
        assert g["name"] == b["name"]
        if "code" in b:
            assert g.get("code") == b["code"]
        if "preferred_unit_symbol" in b:
            assert g.get("preferred_unit_symbol") == b["preferred_unit_symbol"]


@pytest.mark.asyncio
async def test_export_biomarker_panels_preserves_seeded_data(seeded_db):
    from app.services.seed.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_biomarker_panels()
    shipped = _load_shipped("biomarker_panels.json")["items"]
    shipped_set = {(s["panel_slug"], s["biomarker_slug"]) for s in shipped}
    got_set = {(it["panel_slug"], it["biomarker_slug"]) for it in out["items"]}
    assert shipped_set <= got_set, "export dropped panel memberships"


@pytest.mark.asyncio
async def test_export_clinical_event_types_preserves_seeded_data(seeded_db):
    from app.services.seed.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_clinical_event_types()
    shipped = _load_shipped("clinical_event_types.json")["items"]
    by_slug = _by_key(out["items"])
    for s in shipped:
        got = by_slug.get(s["slug"])
        assert got is not None, f"missing event type {s['slug']}"
        assert got["name"] == s["name"]
        if "category_slug" in s:
            assert got.get("category_slug") == s["category_slug"]


@pytest.mark.asyncio
async def test_export_medications_and_allergies_preserve_seeded_data(seeded_db):
    from app.services.seed.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    meds = await svc.export_medications()
    allergies = await svc.export_allergies()
    shipped_meds = {m["name"] for m in _load_shipped("medications.json")["items"]}
    shipped_allergies = {a["name"] for a in _load_shipped("allergies.json")["items"]}
    assert shipped_meds <= {m["name"] for m in meds["items"]}
    assert shipped_allergies <= {a["name"] for a in allergies["items"]}


@pytest.mark.asyncio
async def test_export_is_deterministic(seeded_db):
    """Two exports of the same state produce identical item payloads. Makes git
    diffs on re-export clean."""
    from app.services.seed.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    a = await svc.export_all()
    b = await svc.export_all()
    for fn in a:
        # default_catalog.json has {units, biomarkers}; others have {items}.
        # Compare the non-metadata body.
        body_keys = {k for k in a[fn] if k != "metadata"}
        for k in body_keys:
            assert a[fn][k] == b[fn][k], f"{fn}.{k} not deterministic"


@pytest.mark.asyncio
async def test_export_concepts_then_reingest_is_idempotent(db):
    """The strong round-trip guarantee, scoped to concepts this test owns:
    exporting and feeding the export back through the ingest path does NOT
    duplicate the rows (every slug still resolves to exactly one row). Scoped
    rather than global so shared-DB state from other test files can't flake it."""
    from app.services.seed.seed_export_service import SeedExportService

    parent = _make_concept(_uslug("rt-parent"), "RT Parent", [ConceptKind.SPECIALTY])
    child = _make_concept(_uslug("rt-child"), "RT Child", [ConceptKind.SPECIALTY])
    db.add_all([parent, child])
    await db.commit()
    parent_slug, child_slug = parent.slug, child.slug

    # Export global concepts, then re-ingest the subset we own.
    exported = await SeedExportService(db).export_concepts()
    mine = [it for it in exported["items"] if it["slug"] in (parent_slug, child_slug)]
    assert len(mine) == 2
    stats = await SeedService()._process_concepts(db, {"items": mine})
    await db.commit()
    assert stats["added"] == 0, f"re-ingest duplicated rows: {stats}"

    # Exactly one row per slug (no duplication).
    from sqlalchemy import func

    for slug in (parent_slug, child_slug):
        n = (
            await db.execute(
                select(func.count())
                .select_from(Concept)
                .where(Concept.slug == slug, Concept.tenant_id.is_(None))
            )
        ).scalar()
        assert n == 1, f"{slug} duplicated ({n} rows) after re-ingest"


@pytest.mark.asyncio
async def test_fresh_install_from_export_reuses_exported_unit_ids(tmp_path):
    """A fresh install built from an export recreates every unit under the
    EXPORTED id (units carry no ``seed_key`` — the id is the identity), so a
    re-export is stable instead of drifting to new random ids."""
    from sqlalchemy import delete

    from app.core.database import AsyncSessionLocal
    from app.models.biomarker_model import Unit
    from app.services.seed.seed_export_service import SeedExportService

    svc = SeedService()
    await seed_with_commit(svc.seed_default_catalog)

    async with AsyncSessionLocal() as session:
        exported = await SeedExportService(session).export_default_catalog()
    units = exported["units"]
    assert len(units) >= 7, "the curated units must export"
    assert all("id" in u for u in units), "every exported unit carries its id"
    exported_ids = {u["symbol"]: u["id"] for u in units}

    # Simulate the fresh install: no units at all, then re-ingest the export
    # (biomarkers filtered to the curated set — non-curated rows are not seed
    # data and would fail the mandatory-key validation).
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Unit))
        await session.commit()

    export_dir = tmp_path / "seeds"
    export_dir.mkdir()
    payload = {
        **exported,
        "biomarkers": [b for b in exported["biomarkers"] if b.get("seed_key")],
    }
    (export_dir / "default_catalog.json").write_bytes(SeedExportService._serialize(payload))

    fresh = SeedService()
    fresh.seeds_dir = export_dir
    export_data = json.loads((export_dir / "default_catalog.json").read_bytes())
    stats = await seed_with_commit(fresh.seed_default_catalog, export_data)
    assert stats["details"]["units_added"] == len(units), stats
    assert stats["details"]["errors"] == 0, stats

    async with AsyncSessionLocal() as session:
        for symbol, uid in exported_ids.items():
            row = await session.get(Unit, UUID(uid))
            assert row is not None, f"unit {symbol!r} not recreated under the exported id"
            assert row.symbol == symbol

    # Re-export is stable on unit ids (no drift).
    async with AsyncSessionLocal() as session:
        reexport = await SeedExportService(session).export_default_catalog()
    assert {u["symbol"]: u["id"] for u in reexport["units"]} == exported_ids


@pytest.mark.asyncio
async def test_unit_import_conflicting_explicit_id_is_a_typed_error(db):
    """A unit item claiming an id already owned by another unit is a typed
    per-item conflict (``SeedIdentityConflict`` → ``errors``), never a raw
    PK ``IntegrityError``, and the clashing unit is not written."""
    from sqlalchemy import func as sa_func

    from app.models.biomarker_model import Unit
    from app.schemas.biomarker import CatalogImportPayload, UnitCreate
    from app.services.catalog.catalog_import_service import CatalogImportService

    existing = (await db.execute(select(Unit).limit(1))).scalar_one()
    assert existing is not None, "a unit must exist to clash against"
    clash_symbol = _uslug("clash-unit")

    payload = CatalogImportPayload(
        units=[UnitCreate(id=existing.id, symbol=clash_symbol, name="Clash Unit")],
        biomarkers=[],
    )
    result = await CatalogImportService(db).import_catalog(payload)
    assert result.stats["errors"] == 1, result.stats
    assert result.stats["units_added"] == 0, result.stats
    assert result.failures, "the clashing unit must appear as a typed failed outcome"

    created = (
        await db.execute(
            select(sa_func.count()).select_from(Unit).where(Unit.symbol == clash_symbol)
        )
    ).scalar_one()
    assert created == 0, "the clashing unit must not be written"


@pytest.mark.asyncio
async def test_export_anatomy_emits_class_concept_slug_for_non_legacy(db):
    """Lossless-field guard: an anatomy structure whose class concept is NOT in
    the legacy enum map exports ``class_concept_slug`` (the legacy ``category``
    cannot represent it, so the slug is the only path back)."""
    from app.services.seed.seed_export_service import SeedExportService

    niche = _make_concept(_uslug("lymphatic-class"), "Lymphatic", [ConceptKind.ANATOMY_CLASS])
    db.add(niche)
    await db.flush()
    struct_slug = _uslug("ln")
    db.add(
        AnatomyStructure(
            tenant_id=None,
            name="Lymph Node",
            slug=struct_slug,
            class_concept_id=niche.id,
        )
    )
    await db.commit()

    out = await SeedExportService(db).export_anatomy_structures()
    item = next(i for i in out["items"] if i["slug"] == struct_slug)
    assert item["class_concept_slug"] == niche.slug
    # no legacy category emitted for a non-mapped class concept
    assert "category" not in item


@pytest.mark.asyncio
async def test_write_all_safety_pipeline_backs_up_and_writes(tmp_path, seeded_db):
    """The filesystem writer stages, backs up existing files, and writes the
    new ones atomically. Originals survive in a timestamped backup dir."""
    from app.services.seed.seed_export_service import SeedExportService

    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "concepts.json").write_text('{"old": true}')

    svc = SeedExportService(seeded_db)
    report = await svc.write_all(seeds, backup=True)

    # new file written
    assert (seeds / "concepts.json").exists()
    new_content = json.loads((seeds / "concepts.json").read_text())
    assert "metadata" in new_content and "items" in new_content
    # old content backed up
    assert report["backup_dir"]
    backup_concepts = Path(report["backup_dir"]) / "concepts.json"
    assert backup_concepts.exists()
    assert json.loads(backup_concepts.read_text()) == {"old": True}
    # staging dir cleaned up
    assert not (seeds / ".export-staging").exists()


# ===========================================================================
# Phase 3 — CLI (scripts/export_seeds.py)
# ===========================================================================


@pytest.mark.asyncio
async def test_cli_dry_run_writes_nothing(tmp_path, monkeypatch, capsys, seeded_db):
    """`--dry-run` prints the per-file counts and writes nothing to disk."""
    import scripts.export_seeds as cli

    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["export_seeds.py", "--dry-run", "--out", str(out)])
    await cli.main()
    captured = capsys.readouterr().out
    assert "Dry run" in captured
    assert "concepts.json" in captured
    assert not out.exists()  # nothing written


@pytest.mark.asyncio
async def test_cli_write_creates_files_and_backup(tmp_path, monkeypatch, capsys):
    """A real write creates every seed file + a timestamped backup of any file
    that was already present."""
    import scripts.export_seeds as cli

    out = tmp_path / "seeds"
    out.mkdir()
    # pre-existing file that should be backed up
    (out / "concepts.json").write_text('{"old": true}')

    monkeypatch.setattr(sys, "argv", ["export_seeds.py", "--out", str(out)])
    await cli.main()
    capsys.readouterr()  # clear

    # every shipped seed file is present
    expected = {
        "concepts.json",
        "diseases.json",
        "vaccines.json",
        "concept_edges.json",
        "anatomy_structures.json",
        "default_catalog.json",
        "biomarker_panels.json",
        "clinical_event_types.json",
        "medications.json",
        "allergies.json",
    }
    for name in expected:
        assert (out / name).exists(), f"{name} missing"

    # the pre-existing concepts.json was backed up (not clobbered without trace)
    backups = list(out.glob(".backup-*"))
    assert backups, "no backup dir created"
    assert json.loads((backups[0] / "concepts.json").read_text()) == {"old": True}

    # written concepts.json is a valid seed envelope
    written = json.loads((out / "concepts.json").read_text())
    assert "metadata" in written and "items" in written


@pytest.mark.asyncio
async def test_cli_no_backup_flag_skips_backup(tmp_path, monkeypatch):
    import scripts.export_seeds as cli

    out = tmp_path / "seeds"
    out.mkdir()
    (out / "concepts.json").write_text('{"old": true}')
    monkeypatch.setattr(sys, "argv", ["export_seeds.py", "--out", str(out), "--no-backup"])
    await cli.main()
    assert not list(out.glob(".backup-*")), "--no-backup should skip backup"


def test_cli_source_parser():
    """_parse_source accepts 'global' (-> None) and UUID strings; rejects garbage."""
    import argparse

    import scripts.export_seeds as cli

    assert cli._parse_source("global") is None
    valid = uuid4()
    assert cli._parse_source(str(valid)) == valid
    with pytest.raises(argparse.ArgumentTypeError):
        cli._parse_source("not-a-uuid-or-global")


# ===========================================================================
# Phase 4 — download ZIP (service + endpoint)
# ===========================================================================


@pytest.mark.asyncio
async def test_build_zip_bytes_produces_valid_zip_with_all_files(seeded_db):
    """build_zip_bytes returns the **canonical pack zip** (``manifest.json`` +
    ``items/*.json``, ADR 0056): every registered item file is pinned by the
    manifest, each parses as the expected envelope, and the archive is
    deterministic (fixed entry order, fixed timestamps)."""
    import zipfile

    from app.core.seed_pack import content_digest, parse_pack
    from app.services.seed.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    data = await svc.build_zip_bytes()
    assert data[:2] == b"PK"  # ZIP magic
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        assert [i.filename for i in infos] == [
            "manifest.json",
            *(sorted(f"items/{fn}" for fn in svc.EXPORTERS)),
        ], "canonical order: manifest first, then items sorted by path"
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in infos), "no timestamps"
        manifest = json.loads(zf.read("manifest.json"))
        files = {name: zf.read(name) for name in zf.namelist() if name != "manifest.json"}
        assert manifest["scope"] == "system"
        for entry in manifest["item_files"]:
            assert content_digest(files[entry["path"]]) == entry["sha256"], "pins match bytes"
    parsed, errors = parse_pack(manifest, files)
    assert not errors
    assert parsed is not None

    # Deterministic: two builds are byte-identical.
    again = await svc.build_zip_bytes()
    assert again == data

    # A template-tenant export is still a system-scoped pack (scope stripped).
    tenant_svc = SeedExportService(seeded_db, tenant_id=uuid4())
    tmanifest, _ = await tenant_svc.build_pack()
    assert tmanifest["scope"] == "system"


@pytest.mark.asyncio
async def test_seeds_export_endpoint_system_admin_200(monkeypatch):
    """SYSTEM_ADMIN gets a ZIP download with every seed file."""
    import zipfile

    from httpx import ASGITransport, AsyncClient

    from app.core.security import get_current_user
    from app.main import app
    from app.schemas.user import TokenData
    from app.services.seed.service import SeedService

    # Seed the global taxonomy so the export has content.
    svc = SeedService()
    await seed_with_commit(svc.seed_concepts)
    await seed_with_commit(svc.seed_body_parts)

    tid = uuid4()
    monkeypatch.setattr(
        "app.core.security.get_current_user",
        lambda: TokenData(user_id=uuid4(), sub="sys", tenant_id=tid, role="SYSTEM_ADMIN"),
    )
    # bypass the real dependency override wiring used elsewhere
    app.dependency_overrides[get_current_user] = lambda: TokenData(
        user_id=uuid4(), sub="sys", tenant_id=tid, role="SYSTEM_ADMIN"
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/seeds/export.zip")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = set(zf.namelist())
            assert "manifest.json" in names
            assert "items/concepts.json" in names
            assert "items/anatomy_structures.json" in names
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_seeds_export_endpoint_rejects_non_system_admin(monkeypatch):
    """ADMIN / MANAGER / USER all get 403 — seeds are SYSTEM_ADMIN-only."""
    from httpx import ASGITransport, AsyncClient

    from app.core.security import get_current_user
    from app.main import app
    from app.schemas.user import TokenData

    tid = uuid4()
    for role in ("USER", "MANAGER", "ADMIN"):
        token = TokenData(user_id=uuid4(), sub="x", tenant_id=tid, role=role)
        app.dependency_overrides[get_current_user] = lambda t=token: t
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/admin/seeds/export.zip")
            assert resp.status_code == 403, f"{role} should be forbidden"
        finally:
            app.dependency_overrides.pop(get_current_user, None)


# ===========================================================================
# Phase 4 — dev-side unpack helper (scripts/unpack_seeds_zip.py)
# ===========================================================================


def test_unpack_seeds_zip_backs_up_and_extracts(tmp_path, monkeypatch):
    """unpack_seeds_zip backs up existing files, then extracts the ZIP."""
    import scripts.unpack_seeds_zip as unpack

    out = tmp_path / "seeds"
    out.mkdir()
    (out / "concepts.json").write_text('{"old": true}')  # pre-existing

    # build a ZIP with two seed files
    zip_path = tmp_path / "seeds.zip"
    import zipfile as zf_mod

    with zf_mod.ZipFile(zip_path, "w") as zf:
        zf.writestr("concepts.json", '{"metadata": {}, "items": []}')
        zf.writestr("allergies.json", '{"metadata": {}, "items": []}')

    monkeypatch.setattr(sys, "argv", ["unpack_seeds_zip.py", str(zip_path), "--out", str(out)])
    unpack.main()

    # extracted
    assert json.loads((out / "concepts.json").read_text()) == {
        "metadata": {},
        "items": [],
    }
    assert (out / "allergies.json").exists()
    # old content backed up
    backups = list(out.glob(".backup-*"))
    assert backups
    assert json.loads((backups[0] / "concepts.json").read_text()) == {"old": True}


def test_unpack_seeds_zip_rejects_unknown_entries(tmp_path, monkeypatch):
    """A ZIP with non-seed entries is refused (no extraction)."""
    import scripts.unpack_seeds_zip as unpack

    out = tmp_path / "seeds"
    out.mkdir()
    zip_path = tmp_path / "evil.zip"
    import zipfile as zf_mod

    with zf_mod.ZipFile(zip_path, "w") as zf:
        zf.writestr("concepts.json", "{}")
        zf.writestr("../../etc/evil", "pwned")  # path traversal / unknown entry

    monkeypatch.setattr(sys, "argv", ["unpack_seeds_zip.py", str(zip_path), "--out", str(out)])
    with pytest.raises(SystemExit):
        unpack.main()
    # nothing extracted
    assert not (out / "concepts.json").exists()


# ===========================================================================
# ADR 0056 Phase 6 — the canonical pack export (coverage, determinism, gates)
# ===========================================================================


def test_export_covers_every_pack_item_file():
    """The exporter covers **every** registered pack item file — the coverage
    matrix is the whole truth, so the canonical export ships all twelve files
    (vaccines, biomarker states, OMB picklist and panels included)."""
    from app.core.seed_pack import BUNDLED_ITEM_FILES
    from app.services.seed.seed_export_service import SeedExportService

    assert set(SeedExportService.EXPORTERS) == set(BUNDLED_ITEM_FILES)


@pytest.mark.asyncio
async def test_export_biomarker_states_preserves_seeded_data(seeded_db):
    """Every shipped biomarker state is exported with its code/system/display/
    category/sort_order, identity rows carrying their mechanical seed_key."""
    from app.services.seed.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_biomarker_states()
    shipped = _load_shipped("biomarker_states.json")["items"]
    by_slug = {i["slug"]: i for i in out["items"]}
    assert len(out["items"]) >= len(shipped), "export dropped states"
    for s in shipped:
        got = by_slug.get(s["slug"])
        assert got is not None, f"missing state {s['slug']}"
        assert got["seed_key"] == s["seed_key"]
        assert got["code"] == s["code"]
        assert got["system"] == s["system"]
        assert got["display"] == s["display"]
        assert got["category"] == s["category"]
        assert got["sort_order"] == s["sort_order"]


@pytest.mark.asyncio
async def test_export_omb_picklist_normalizes_pack_data(seeded_db):
    """The OMB picklist exports as a deterministic pass-through of the pack
    path: the three collections sorted by seed_key, the schema note kept, the
    timestamp gone."""
    from app.services.seed.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_omb_picklist()
    assert "races" in out and "ethnicities" in out and "languages" in out
    shipped = _load_shipped("omb_race_ethnicity.json")
    for key in ("races", "ethnicities", "languages"):
        got = out[key]
        expected = {(r["seed_key"], r["code"], r["display"]) for r in shipped[key]}
        assert {(r["seed_key"], r["code"], r["display"]) for r in got} == expected, key
        keys = [r["seed_key"] for r in got]
        assert keys == sorted(keys), f"{key} sorted by seed_key"
    assert "last_updated" not in out["metadata"]
    assert out["metadata"]["schema_note"] == shipped["metadata"]["schema_note"]


@pytest.mark.asyncio
async def test_export_is_byte_deterministic_no_timestamps(seeded_db):
    """The canonical export carries **no timestamps anywhere** — serializing
    two export_all runs byte-for-byte is identical (the CI ``--check`` gate
    and git-diff review both rely on this)."""
    from app.services.seed.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    a = await svc.export_all()
    b = await svc.export_all()
    assert set(a) == set(b)
    for fn in a:
        sa = SeedExportService._serialize(a[fn])
        sb = SeedExportService._serialize(b[fn])
        assert sa == sb, f"{fn} not byte-deterministic"
        assert b"last_updated" not in sa, f"{fn} carries a timestamp"
        assert "last_updated" not in a[fn].get("metadata", {})


@pytest.mark.asyncio
async def test_build_pack_is_contract_valid_and_deterministic(seeded_db):
    """build_pack emits a manifest + item files that pass the pack contract,
    with a stable whole-pack digest across builds (round-trip identity with
    the source lane: the same digest always registers the same version)."""
    from app.core.seed_pack import pack_digest, parse_pack, validate_pack
    from app.services.seed.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    manifest, files = await svc.build_pack()
    assert not validate_pack(manifest, files)
    assert pack_digest(manifest)

    manifest_b, files_b = await svc.build_pack()
    assert manifest == manifest_b
    assert files == files_b

    parsed, errors = parse_pack(manifest, files)
    assert not errors
    assert parsed is not None
    assert parsed.stage_items("concepts"), "the pack parses into stages"


def test_seed_pack_cli_check_detects_drift(tmp_path, monkeypatch, capsys):
    """``seed_pack.py pack --check`` is green on a current pack and exits
    non-zero with typed findings once the checked-in copy drifts."""
    import scripts.seed_pack as cli

    out = tmp_path / "pack"
    monkeypatch.setattr(sys, "argv", ["seed_pack.py", "pack", "--out", str(out)])
    assert cli.main() == 0

    monkeypatch.setattr(sys, "argv", ["seed_pack.py", "pack", "--out", str(out), "--check"])
    assert cli.main() == 0

    drift = out / "items" / "allergies.json"
    original = drift.read_bytes()
    drift.write_bytes(original + b'{"tampered": true}')
    monkeypatch.setattr(sys, "argv", ["seed_pack.py", "pack", "--out", str(out), "--check"])
    assert cli.main() == 1
    assert "items/allergies.json differs" in capsys.readouterr().out

    drift.write_bytes(original)
    (out / "items" / "medications.json").unlink()
    assert cli.main() == 1
    assert "missing from" in capsys.readouterr().out


def test_unpack_seeds_zip_accepts_canonical_pack_and_verifies_pins(tmp_path, monkeypatch):
    """The canonical pack zip unpacks (items/ stripped, manifest skipped) and
    every sha256 pin is verified — a tampered archive never lands."""
    import hashlib
    import zipfile as zf_mod

    import scripts.unpack_seeds_zip as unpack

    manifest = {
        "id": "unit-pack",
        "schema_version": "1.0",
        "version": "1.0.0",
        "license": "CC0-1.0",
        "scope": "system",
        "priority": 100,
        "item_files": [],
    }
    files = {
        "concepts.json": b'{"metadata": {"version": "1"}, "items": []}',
        "allergies.json": b'{"metadata": {"version": "1"}, "items": []}',
    }
    for name, content in files.items():
        manifest["item_files"].append(
            {"path": f"items/{name}", "sha256": hashlib.sha256(content).hexdigest()}
        )

    zip_path = tmp_path / "pack.zip"
    with zf_mod.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            zf.writestr(f"items/{name}", content)

    out = tmp_path / "seeds"
    monkeypatch.setattr(sys, "argv", ["unpack_seeds_zip.py", str(zip_path), "--out", str(out)])
    unpack.main()
    assert (out / "concepts.json").read_bytes() == files["concepts.json"]
    assert not (out / "manifest.json").exists(), "the manifest never lands in data/seeds"

    # Tampered content does not match its pin.
    tampered = tmp_path / "tampered.zip"
    with zf_mod.ZipFile(tampered, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            zf.writestr(f"items/{name}", content + b"pwned" if name == "concepts.json" else content)
    out2 = tmp_path / "seeds2"
    monkeypatch.setattr(sys, "argv", ["unpack_seeds_zip.py", str(tampered), "--out", str(out2)])
    with pytest.raises(SystemExit):
        unpack.main()
    assert not out2.exists() or not (out2 / "concepts.json").exists()

    # A manifest-pinned file missing from the archive is a refusal too.
    incomplete = tmp_path / "incomplete.zip"
    with zf_mod.ZipFile(incomplete, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("items/concepts.json", files["concepts.json"])
    out3 = tmp_path / "seeds3"
    monkeypatch.setattr(sys, "argv", ["unpack_seeds_zip.py", str(incomplete), "--out", str(out3)])
    with pytest.raises(SystemExit):
        unpack.main()


def test_unpack_seeds_zip_still_accepts_a_flat_archive(tmp_path, monkeypatch):
    """A hand-rolled flat zip of seed files unpacks whitelist-only (the
    whitelist is the full registered file set)."""
    import zipfile as zf_mod

    import scripts.unpack_seeds_zip as unpack

    out = tmp_path / "seeds"
    zip_path = tmp_path / "flat.zip"
    with zf_mod.ZipFile(zip_path, "w") as zf:
        zf.writestr("biomarker_states.json", '{"metadata": {}, "items": []}')
        zf.writestr("omb_race_ethnicity.json", '{"metadata": {}, "races": []}')
        zf.writestr("unregistered.json", "{}")
    monkeypatch.setattr(sys, "argv", ["unpack_seeds_zip.py", str(zip_path), "--out", str(out)])
    with pytest.raises(SystemExit):
        unpack.main()
    assert not (out / "biomarker_states.json").exists()

    zip_ok = tmp_path / "flat-ok.zip"
    with zf_mod.ZipFile(zip_ok, "w") as zf:
        zf.writestr("biomarker_states.json", '{"metadata": {}, "items": []}')
        zf.writestr("omb_race_ethnicity.json", '{"metadata": {}, "races": []}')
    monkeypatch.setattr(sys, "argv", ["unpack_seeds_zip.py", str(zip_ok), "--out", str(out)])
    unpack.main()
    assert (out / "biomarker_states.json").exists()
    assert (out / "omb_race_ethnicity.json").exists()
