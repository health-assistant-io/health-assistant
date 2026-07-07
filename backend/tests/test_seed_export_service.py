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
from typing import Any, Dict
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept, ConceptEdge, ConceptKindTag
from app.models.enums import (
    CodingSystem,
    ConceptKind,
    ConceptRelationType,
    EdgeEndpointType,
)
from app.services.seed_service import SeedService


def _uslug(base: str) -> str:
    return f"{base}-{uuid4().hex[:10]}"


def _make_concept(slug, name, kinds, tenant_id=None, primary=None) -> Concept:
    c = Concept(
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
        await db.execute(
            select(AnatomyStructure).where(AnatomyStructure.slug == node_slug)
        )
    ).scalar_one()
    assert struct.class_concept_id == target.id  # NOT the legacy "organ"


@pytest.mark.asyncio
async def test_anatomy_seed_class_concept_slug_handles_non_legacy_class(db):
    """An anatomy class concept whose slug is NOT in the legacy enum map
    round-trips via ``class_concept_slug`` alone (no legacy ``category``).
    The legacy path couldn't represent this — the lossless-field guard."""
    svc = SeedService()
    niche = _make_concept(
        _uslug("lymphatic-structure"), "Lymphatic Structure", [ConceptKind.ANATOMY_CLASS]
    )
    db.add(niche)
    await db.flush()

    node_slug = _uslug("lymph-node")
    payload = {
        "nodes": [
            {
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
        await db.execute(
            select(AnatomyStructure).where(AnatomyStructure.slug == node_slug)
        )
    ).scalar_one()
    assert struct.class_concept_id == niche.id


@pytest.mark.asyncio
async def test_anatomy_seed_falls_back_to_legacy_category(db):
    """Backward compat: an item with only the legacy ``category`` enum still
    resolves via ``_ANATOMY_CATEGORY_TO_CONCEPT_SLUG`` (requires the matching
    global concept to exist, which seed_concepts provides)."""
    svc = SeedService()
    # Get-or-create the global "organ" concept the legacy ORGAN maps to (the
    # shared test DB may already have it from prior seed runs).
    existing = (
        await db.execute(
            select(Concept).where(Concept.slug == "organ", Concept.tenant_id.is_(None))
        )
    ).scalar_one_or_none()
    if existing:
        organ = existing
    else:
        organ = _make_concept("organ", "Organ", [ConceptKind.ANATOMY_CLASS])
        db.add(organ)
        await db.flush()

    node_slug = _uslug("legacy-heart")
    payload = {
        "nodes": [
            {
                "slug": node_slug,
                "name": "Legacy Heart",
                "category": "ORGAN",  # legacy only — no class_concept_slug
            }
        ],
        "edges": [],
    }
    await svc._process_body_parts(db, payload)
    await db.commit()

    struct = (
        await db.execute(
            select(AnatomyStructure).where(AnatomyStructure.slug == node_slug)
        )
    ).scalar_one()
    assert struct.class_concept_id == organ.id


@pytest.mark.asyncio
async def test_concept_edge_seed_resolves_biomarker_endpoint(db):
    """The concept-edge seed ingest resolves a ``biomarker`` endpoint type by
    BiomarkerDefinition.slug (not just concept/anatomy). Unblocks round-tripping
    biomarker-level edges beyond panel membership."""
    svc = SeedService()
    panel = _make_concept(
        _uslug("my-panel"), "My Panel", [ConceptKind.BIOMARKER_PANEL]
    )
    db.add(panel)
    bio_slug = _uslug("widget-biomarker")
    bio = BiomarkerDefinition(
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
                "src_slug": bio_slug,
                "dst_type": "concept",
                "dst_slug": panel.slug,
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


def _load_shipped(filename: str) -> Dict[str, Any]:
    return json.loads((_shipped_seeds_dir() / filename).read_text())


@pytest.fixture
async def seeded_db():
    """A session with the shipped seeds loaded (global taxonomy + catalogs).
    Used by the round-trip-preservation tests."""
    from app.core.database import AsyncSessionLocal

    svc = SeedService()
    # Ordered so dependencies exist (concepts before edges/catalog/panels).
    await svc.seed_concepts()
    await svc.seed_body_parts()
    await svc.seed_concept_edges()
    await svc.seed_default_catalog()
    await svc.seed_biomarker_panels()
    await svc.seed_clinical_event_types()
    await svc.seed_medications()
    await svc.seed_allergies()
    async with AsyncSessionLocal() as session:
        yield session


def _by_key(items, key="slug"):
    return {it[key]: it for it in items}


@pytest.mark.asyncio
async def test_export_concepts_preserves_seeded_data(seeded_db):
    """Every shipped concept slug is exported with matching name + kinds (as a
    set — order is not semantically meaningful). Proves the export doesn't
    lose or corrupt the curated taxonomy."""
    from app.services.seed_export_service import SeedExportService

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
    from app.services.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_concept_edges()
    shipped = _load_shipped("concept_edges.json")["items"]
    shipped_set = {
        (s["src_slug"], s["dst_slug"], s["relation"]) for s in shipped
    }
    got_set = {(it["src_slug"], it["dst_slug"], it["relation"]) for it in out["items"]}
    assert shipped_set <= got_set, "export dropped edges"


@pytest.mark.asyncio
async def test_export_anatomy_preserves_seeded_data(seeded_db):
    from app.services.seed_export_service import SeedExportService

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
    from app.services.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    out = await svc.export_anatomy_relations()
    shipped = _load_shipped("anatomy_relations.json")["items"]
    shipped_set = {
        (s["source_slug"], s["target_slug"], s["relation_type"]) for s in shipped
    }
    got_set = {
        (it["source_slug"], it["target_slug"], it["relation_type"])
        for it in out["items"]
    }
    assert shipped_set <= got_set, "export dropped anatomy relations"


@pytest.mark.asyncio
async def test_export_default_catalog_preserves_seeded_data(seeded_db):
    from app.services.seed_export_service import SeedExportService

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
    from app.services.seed_export_service import SeedExportService

    out = await SeedExportService(seeded_db).export_biomarker_panels()
    shipped = _load_shipped("biomarker_panels.json")["items"]
    shipped_set = {(s["panel_slug"], s["biomarker_slug"]) for s in shipped}
    got_set = {(it["panel_slug"], it["biomarker_slug"]) for it in out["items"]}
    assert shipped_set <= got_set, "export dropped panel memberships"


@pytest.mark.asyncio
async def test_export_clinical_event_types_preserves_seeded_data(seeded_db):
    from app.services.seed_export_service import SeedExportService

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
    from app.services.seed_export_service import SeedExportService

    svc = SeedExportService(seeded_db)
    meds = await svc.export_medications()
    allergies = await svc.export_allergies()
    shipped_meds = {m["name"] for m in _load_shipped("medications.json")["items"]}
    shipped_allergies = {
        a["name"] for a in _load_shipped("allergies.json")["items"]
    }
    assert shipped_meds <= {m["name"] for m in meds["items"]}
    assert shipped_allergies <= {a["name"] for a in allergies["items"]}


@pytest.mark.asyncio
async def test_export_is_deterministic(seeded_db):
    """Two exports of the same state produce identical item payloads. Makes git
    diffs on re-export clean."""
    from app.services.seed_export_service import SeedExportService

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
    from app.services.seed_export_service import SeedExportService

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
async def test_export_anatomy_emits_class_concept_slug_for_non_legacy(db):
    """Lossless-field guard: an anatomy structure whose class concept is NOT in
    the legacy enum map exports ``class_concept_slug`` (the legacy ``category``
    cannot represent it, so the slug is the only path back)."""
    from app.services.seed_export_service import SeedExportService

    niche = _make_concept(
        _uslug("lymphatic-class"), "Lymphatic", [ConceptKind.ANATOMY_CLASS]
    )
    db.add(niche)
    await db.flush()
    struct_slug = _uslug("ln")
    db.add(
        AnatomyStructure(
            tenant_id=None,
            name="Lymph Node",
            slug=struct_slug,
            class_concept_id=niche.id,
            is_custom=False,
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
    from app.services.seed_export_service import SeedExportService

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
    monkeypatch.setattr(
        sys, "argv", ["export_seeds.py", "--dry-run", "--out", str(out)]
    )
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
        "concept_edges.json",
        "anatomy_structures.json",
        "anatomy_relations.json",
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
    monkeypatch.setattr(
        sys, "argv", ["export_seeds.py", "--out", str(out), "--no-backup"]
    )
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
    """build_zip_bytes returns a valid ZIP whose flat layout contains every
    seed file and each parses as the expected envelope."""
    import zipfile

    from app.services.seed_export_service import SeedExportService

    data = await SeedExportService(seeded_db).build_zip_bytes()
    assert data[:2] == b"PK"  # ZIP magic
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        expected = set(SeedExportService.EXPORTERS.keys())
        assert expected <= names, f"missing files: {expected - names}"
        # each file is valid JSON with a metadata block
        for name in expected:
            payload = json.loads(zf.read(name))
            assert "metadata" in payload


@pytest.mark.asyncio
async def test_seeds_export_endpoint_system_admin_200(monkeypatch):
    """SYSTEM_ADMIN gets a ZIP download with every seed file."""
    import zipfile
    from httpx import ASGITransport, AsyncClient

    from app.core.security import get_current_user
    from app.main import app
    from app.schemas.user import TokenData
    from app.services.seed_service import SeedService

    # Seed the global taxonomy so the export has content.
    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_body_parts()

    tid = uuid4()
    monkeypatch.setattr(
        "app.core.security.get_current_user",
        lambda: TokenData(
            user_id=uuid4(), sub="sys", tenant_id=tid, role="SYSTEM_ADMIN"
        ),
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
            assert "concepts.json" in names
            assert "anatomy_structures.json" in names
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
    assert json.loads((out / "concepts.json").read_text()) == {"metadata": {}, "items": []}
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
