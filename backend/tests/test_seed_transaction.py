"""Seeder transaction integrity + typed import results (ADR 0056 Phase 2).

The named refactor's contract:

* every seeder takes a **required session**, never opens its own and never
  commits — a rolled-back seed leaves nothing behind;
* ``CatalogImportService.import_catalog`` never commits, reports typed
  per-item outcomes (failures are never swallowed), and a re-import of
  identical content is a true no-op;
* ``seed_all`` passes the bundled pack's parsed items to each stage.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.seed_pack import load_bundled_pack
from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.fhir.medication import MedicationCatalog
from app.schemas.biomarker import BiomarkerCreate, CatalogImportPayload, UnitCreate
from app.services.catalog.catalog_import_service import CatalogImportService
from app.services.seed.service import SeedService


@pytest.fixture
async def db():
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


@pytest.mark.asyncio
async def test_seeder_never_commits_rollback_leaves_nothing():
    """A medication seeded through a session the test rolls back writes
    nothing: no own commit, no partial state (the caller owns the
    transaction)."""
    symbol_key = f"medication:tx-probe-{uuid.uuid4().hex[:8]}"
    item = {"seed_key": symbol_key, "name": "TX Probe Med"}
    async with AsyncSessionLocal() as session:
        stats = await SeedService().seed_medications(session, [item])
        assert stats["added"] == 1 and stats["errors"] == 0, stats
        await session.rollback()

    async with AsyncSessionLocal() as session:
        found = await session.scalar(
            select(func.count())
            .select_from(MedicationCatalog)
            .where(MedicationCatalog.seed_key == symbol_key)
        )
    assert found == 0, "a rolled-back seed stage must leave nothing behind"


@pytest.mark.asyncio
async def test_seeded_stage_persists_only_when_the_caller_commits():
    """Symmetric pin: committing the caller's session is what persists the
    stage (the transaction boundary is visible at the call site)."""
    symbol_key = f"medication:tx-probe-{uuid.uuid4().hex[:8]}"
    item = {"seed_key": symbol_key, "name": "TX Probe Med"}
    async with AsyncSessionLocal() as session:
        await SeedService().seed_medications(session, [item])
        await session.commit()

    async with AsyncSessionLocal() as session:
        found = await session.scalar(
            select(func.count())
            .select_from(MedicationCatalog)
            .where(MedicationCatalog.seed_key == symbol_key)
        )
        await session.delete(
            (
                await session.execute(
                    select(MedicationCatalog).where(MedicationCatalog.seed_key == symbol_key)
                )
            ).scalar_one()
        )
        await session.commit()
    assert found == 1


@pytest.mark.asyncio
async def test_seed_all_dispatches_bundled_pack_items(monkeypatch, tmp_path):
    """seed_all passes each stage the bundled pack's parsed items (not raw
    file reads): a captured stage receives exactly ``pack.stage_items``."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "HA_UPLOAD_DIR", str(tmp_path / "uploads"))
    pack = load_bundled_pack()
    captured: dict[str, list[dict]] = {}

    async def _spy(self, session, items=None):
        captured["medications"] = items
        return {"added": 0, "updated": 0, "unchanged": 0, "skipped": len(items or []), "errors": 0}

    monkeypatch.setattr(SeedService, "seed_medications", _spy)
    async with AsyncSessionLocal() as session:
        stats = await SeedService().seed_all(session)
        await session.rollback()  # nothing real ran; keep the DB untouched

    assert captured["medications"] == pack.stage_items("medications")
    assert set(stats) == set(SeedService._SEED_STAGE_NAMES)


@pytest.mark.asyncio
async def test_import_catalog_does_not_commit_and_reports_typed_outcomes(db):
    """An import with a conflicting explicit id: the failing unit is a typed
    per-item outcome (never swallowed), and — because the importer never
    commits — a rollback leaves nothing behind."""
    clash = Unit(symbol=f"clash-src-{uuid.uuid4().hex[:6]}", name="Clash Source")
    db.add(clash)
    await db.commit()
    clash_id = clash.id
    payload = CatalogImportPayload(
        units=[UnitCreate(id=clash.id, symbol=f"clash-{uuid.uuid4().hex[:6]}", name="Clash")],
        biomarkers=[],
    )
    result = await CatalogImportService(db).import_catalog(payload)
    assert result.stats["errors"] == 1
    assert len(result.failures) == 1
    assert result.failures[0].kind == "unit"
    assert result.failures[0].action == "failed"
    assert result.failures[0].error
    assert result.items[0].reference == payload.units[0].symbol

    await db.rollback()
    async with AsyncSessionLocal() as fresh:
        found = await fresh.scalar(
            select(func.count()).select_from(Unit).where(Unit.symbol == payload.units[0].symbol)
        )
        source = await fresh.scalar(
            select(func.count()).select_from(Unit).where(Unit.id == clash_id)
        )
    assert found == 0, "the importer must not commit; rollback leaves nothing"
    assert source == 1, "the rolled-back run must not touch pre-existing rows"
    async with AsyncSessionLocal() as fresh:
        row = (await fresh.execute(select(Unit).where(Unit.id == clash_id))).scalar_one()
        await fresh.delete(row)
        await fresh.commit()


@pytest.mark.asyncio
async def test_import_catalog_rerun_is_a_true_noop(db):
    """Re-importing identical content reports unchanged and writes nothing
    (content-hash conditional updates — the second run is a no-op)."""
    slug = f"noop-{uuid.uuid4().hex[:8]}"
    payload = CatalogImportPayload(
        units=[UnitCreate(symbol=f"u-{slug}", name="Noop Unit", quantity_type="MASS")],
        biomarkers=[
            BiomarkerCreate(
                slug=slug,
                name="Noop Biomarker",
                coding_system="custom",
                aliases=["noop"],
                reference_range_min=1,
                reference_range_max=2,
            )
        ],
    )
    first = await CatalogImportService(db).import_catalog(payload)
    assert first.stats["units_added"] == 1 and first.stats["biomarkers_added"] == 1, first.stats
    await db.commit()

    second = await CatalogImportService(db).import_catalog(payload)
    await db.commit()

    assert second.stats["biomarkers_unchanged"] == 1, second.stats
    assert second.stats["units_unchanged"] == 1, second.stats
    assert second.stats["biomarkers_updated"] == 0 and second.stats["units_updated"] == 0
    assert not second.failures

    after_count = (
        await db.execute(select(func.count()).select_from(Unit).where(Unit.symbol == f"u-{slug}"))
    ).scalar_one()
    assert after_count == 1, "the no-op run must not duplicate rows"

    bio = (
        await db.execute(select(BiomarkerDefinition).where(BiomarkerDefinition.slug == slug))
    ).scalar_one()
    await db.delete(bio)
    unit = (await db.execute(select(Unit).where(Unit.symbol == f"u-{slug}"))).scalar_one()
    await db.delete(unit)
    await db.commit()


@pytest.mark.asyncio
async def test_bundled_pack_loads_and_matches_the_seeds_dir():
    """The bundled pack builder (pack → validate → parse) covers the shipped
    seeds and its parsed medication items equal the raw file's items."""
    import json

    from app.core.seed_pack.bundled import bundled_seeds_dir

    pack = load_bundled_pack()
    raw = json.loads((bundled_seeds_dir() / "medications.json").read_bytes())
    assert pack.stage_items("medications") == raw["items"]
