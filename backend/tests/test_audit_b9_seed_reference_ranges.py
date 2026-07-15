"""Regression tests for stratified reference ranges in the seed/import path.

The default catalog seed (``data/seeds/default_catalog.json``) ships standard
adult ranges + a few stratified examples. The import pipeline
(``CatalogImportService``) must (a) accept ``reference_ranges`` on the payload,
(b) create the rows, (c) be idempotent (re-import updates in place, no dupes),
and (d) leave user-added ranges alone on re-import (upsert-only).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition, BiomarkerReferenceRange
from app.schemas.biomarker import CatalogImportPayload, BiomarkerCreate, BiomarkerReferenceRangeCreate, UnitCreate
from app.services.catalog_import_service import CatalogImportService
from sqlalchemy import select

SEED_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "seeds"
    / "default_catalog.json"
)


def test_seed_file_parses_and_carries_stratified_ranges():
    """The seed must validate against CatalogImportPayload and include stratified examples."""
    payload = CatalogImportPayload.model_validate(json.loads(SEED_PATH.read_text()))
    assert len(payload.biomarkers) >= 10
    stratified = {b.slug for b in payload.biomarkers if b.reference_ranges}
    # Hemoglobin (♂/♀) + TSH (pediatric) are the canonical demo cases.
    assert {"hemoglobin", "tsh"}.issubset(stratified)
    # Hemoglobin must have both a male and a female row.
    hb = next(b for b in payload.biomarkers if b.slug == "hemoglobin")
    sexes = {r.sex for r in hb.reference_ranges}
    assert {"MALE", "FEMALE"}.issubset(sexes)


@pytest.mark.asyncio
async def test_importer_creates_stratified_ranges():
    import uuid

    slug = f"hb-s9-{uuid.uuid4().hex[:6]}"
    unit_sym = f"g/L-s9-{uuid.uuid4().hex[:6]}"
    payload = CatalogImportPayload(
        units=[UnitCreate(symbol=unit_sym, name="g per L", quantity_type="MASS_CONCENTRATION")],
        biomarkers=[
            BiomarkerCreate(
                slug=slug,
                name="Hemoglobin",
                coding_system="custom",
                aliases=[],
                reference_range_min=120,
                reference_range_max=175,
                reference_ranges=[
                    BiomarkerReferenceRangeCreate(sex="MALE", low=135, high=175),
                    BiomarkerReferenceRangeCreate(sex="FEMALE", low=120, high=155),
                ],
            )
        ],
    )
    async with AsyncSessionLocal() as db:
        svc = CatalogImportService(db)
        try:
            await svc.import_catalog(payload)
            bio = (
                await db.execute(
                    select(BiomarkerDefinition).where(BiomarkerDefinition.slug == slug)
                )
            ).scalar_one()
            rows = (
                await db.execute(
                    select(BiomarkerReferenceRange).where(
                        BiomarkerReferenceRange.biomarker_id == bio.id
                    )
                )
            ).scalars().all()
            # Read attributes while still bound to the session (before cleanup).
            by_sex = {r.sex.value: (r.low, r.high) for r in rows}
        finally:
            bio = (
                await db.execute(
                    select(BiomarkerDefinition).where(BiomarkerDefinition.slug == slug)
                )
            ).scalar_one_or_none()
            if bio:
                await db.delete(bio)  # CASCADE removes the ranges
                await db.commit()

    assert by_sex == {"MALE": (135.0, 175.0), "FEMALE": (120.0, 155.0)}


@pytest.mark.asyncio
async def test_importer_is_idempotent_and_preserves_user_ranges():
    """Re-import must update bounds in place (no dupes) and NOT delete a
    user-added range that isn't in the seed payload (upsert-only)."""
    import uuid

    slug = f"tsh-s9-{uuid.uuid4().hex[:6]}"
    payload = CatalogImportPayload(
        units=[],
        biomarkers=[
            BiomarkerCreate(
                slug=slug,
                name="TSH",
                coding_system="custom",
                aliases=[],
                reference_range_min=0.4,
                reference_range_max=4.0,
                reference_ranges=[
                    BiomarkerReferenceRangeCreate(sex="MALE", low=0.4, high=4.0),
                ],
            )
        ],
    )
    async with AsyncSessionLocal() as db:
        svc = CatalogImportService(db)
        try:
            await svc.import_catalog(payload)  # commits internally
            bio = (
                await db.execute(
                    select(BiomarkerDefinition).where(BiomarkerDefinition.slug == slug)
                )
            ).scalar_one()

            # Simulate a user-added range (e.g. a clinician configured pediatric).
            db.add(
                BiomarkerReferenceRange(
                    biomarker_id=bio.id, age_min=0, age_max=18, low=0.7, high=5.7
                )
            )
            await db.commit()

            # Re-import with a changed bound on the male row.
            payload.biomarkers[0].reference_ranges[0].high = 4.5
            await svc.import_catalog(payload)

            rows = (
                await db.execute(
                    select(BiomarkerReferenceRange).where(
                        BiomarkerReferenceRange.biomarker_id == bio.id
                    )
                )
            ).scalars().all()
            snapshot = [
                (r.sex.value if r.sex else None, r.age_min, r.age_max, r.low, r.high)
                for r in rows
            ]
        finally:
            # Clean up — import_catalog commits, so rows survive rollback.
            bio = (
                await db.execute(
                    select(BiomarkerDefinition).where(BiomarkerDefinition.slug == slug)
                )
            ).scalar_one_or_none()
            if bio:
                await db.delete(bio)  # CASCADE removes the ranges
                await db.commit()

    # The male row was updated in place (high 4.0 -> 4.5), the user-added
    # pediatric row survived, and no duplicate male row was created.
    assert len(snapshot) == 2, snapshot
    male = next(r for r in snapshot if r[0] == "MALE")
    assert male[4] == 4.5
    assert any(r[1] == 0 and r[2] == 18 for r in snapshot)
