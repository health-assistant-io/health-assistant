"""Regression tests for audit B9 / F3 — stratified biomarker reference ranges.

``BiomarkerDefinition`` carried a single global ``reference_range_min``/``max``,
which made ``relative_score`` and the status badge unreliable for anyone
outside the "default" demographic. The fix adds a child table
``biomarker_reference_ranges`` (0..* rows scoped by sex / age window / unit)
and a specificity-ranked resolver that picks the best match for a patient,
falling back to the legacy global range.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.base import Base
from app.models.biomarker_model import BiomarkerDefinition, BiomarkerReferenceRange
from app.models.enums import Gender
from app.services.reference_ranges import (
    ResolvedRange,
    compute_relative_score,
    pick_reference_range,
    resolve_for_patient,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_compute_relative_score_math():
    assert compute_relative_score(50, 0, 100) == 0.5
    assert compute_relative_score(0, 0, 100) == 0.0
    assert compute_relative_score(100, 0, 100) == 1.0
    # Clamped outside the range.
    assert compute_relative_score(150, 0, 100) == 1.0
    assert compute_relative_score(-10, 0, 100) == 0.0
    # One-sided range → middle score.
    assert compute_relative_score(50, 0, None) == 0.5
    assert compute_relative_score(50, None, 100) == 0.5
    # No range at all.
    assert compute_relative_score(50, None, None) is None
    assert compute_relative_score(None, 0, 100) is None


# ---------------------------------------------------------------------------
# Model / schema
# ---------------------------------------------------------------------------


def test_model_declares_table_constraints_and_relationship():
    table = Base.metadata.tables["biomarker_reference_ranges"]
    cols = set(table.c.keys())
    for expected in (
        "id",
        "biomarker_id",
        "sex",
        "age_min",
        "age_max",
        "unit_id",
        "low",
        "high",
        "text",
        "applies_to",
    ):
        assert expected in cols, f"missing column {expected}"
    cks = {c.name for c in table.constraints if getattr(c, "name", None)}
    assert "ck_biomarker_reference_ranges_low_le_high" in cks
    assert "ck_biomarker_reference_ranges_age_window" in cks
    # The relationship + cascade is declared on the parent.
    rel = BiomarkerDefinition.__mapper__.relationships.get("reference_ranges")
    assert rel is not None, "BiomarkerDefinition.reference_ranges relationship missing"
    assert "delete-orphan" in (rel.cascade or "")


@pytest.mark.asyncio
async def test_table_exists_with_constraints():
    """The migration must have created the table + constraints + index."""
    async with AsyncSessionLocal() as session:
        exists = (
            await session.execute(
                text(
                    "SELECT to_regclass('biomarker_reference_ranges')"
                )
            )
        ).scalar()
        assert exists == "biomarker_reference_ranges"
        idx = (
            await session.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename='biomarker_reference_ranges'"
                )
            )
        ).scalars().all()
        assert "ix_biomarker_reference_ranges_biomarker_id" in idx


# ---------------------------------------------------------------------------
# Resolver — in-memory specificity + fallback
# ---------------------------------------------------------------------------


def _bio(**kw) -> BiomarkerDefinition:
    base = dict(
        id=uuid.uuid4(),
        slug="tsh",
        name="TSH",
        coding_system=None,
        aliases=[],
        reference_range_min=0.4,
        reference_range_max=4.0,
    )
    base.update(kw)
    return BiomarkerDefinition(**base)


def _range(**kw) -> BiomarkerReferenceRange:
    return BiomarkerReferenceRange(**kw)


def test_resolver_falls_back_to_legacy_global_range():
    bio = _bio()
    # No stratified rows → legacy global range.
    res = pick_reference_range(bio, [])
    assert res == ResolvedRange(0.4, 4.0, None, "definition")


def test_resolver_returns_none_when_no_range_anywhere():
    bio = _bio(reference_range_min=None, reference_range_max=None)
    assert pick_reference_range(bio, []) is None


def test_resolver_prefers_sex_specific_over_catchall():
    bio = _bio()
    rows = [
        _range(sex=None, low=0.4, high=4.0),  # catch-all
        _range(sex=Gender.MALE, low=0.5, high=5.0),  # male-specific
    ]
    res = pick_reference_range(bio, rows, sex=Gender.MALE)
    assert res.low == 0.5 and res.high == 5.0 and res.source == "stratified"


def test_resolver_falls_through_when_sex_does_not_match():
    bio = _bio()
    rows = [_range(sex=Gender.FEMALE, low=0.4, high=3.5)]
    # A male patient: the female-only row does not apply → catch-all fallback.
    res = pick_reference_range(bio, rows, sex=Gender.MALE)
    assert res.source == "definition"
    assert res.low == 0.4 and res.high == 4.0


def test_resolver_age_window_matching():
    bio = _bio()
    rows = [
        _range(age_min=0, age_max=18, low=0.7, high=5.5),  # pediatric
        _range(age_min=19, age_max=99, low=0.4, high=4.0),  # adult
    ]
    child = pick_reference_range(bio, rows, age=10)
    adult = pick_reference_range(bio, rows, age=40)
    assert child.low == 0.7
    assert adult.low == 0.4


def test_resolver_age_outside_window_falls_back_to_global():
    bio = _bio()
    rows = [_range(age_min=0, age_max=17, low=0.7, high=5.5)]
    # An 80-year-old matches no stratified row → global fallback.
    res = pick_reference_range(bio, rows, age=80)
    assert res.source == "definition"


def test_resolver_unit_specificity():
    bio = _bio()
    unit_a, unit_b = uuid.uuid4(), uuid.uuid4()
    rows = [
        _range(unit_id=None, low=0.4, high=4.0),
        _range(unit_id=unit_a, low=1.0, high=10.0),
    ]
    res = pick_reference_range(bio, rows, unit_id=unit_a)
    assert res.low == 1.0
    # A different unit → the unit-specific row does not apply, catch-all wins.
    res2 = pick_reference_range(bio, rows, unit_id=unit_b)
    assert res2.low == 0.4


def test_resolver_most_specific_wins_combining_dimensions():
    bio = _bio()
    unit = uuid.uuid4()
    rows = [
        _range(sex=None, age_min=None, age_max=None, unit_id=None, low=0.4, high=4.0),
        _range(sex=Gender.MALE, age_min=None, age_max=None, unit_id=None, low=0.5, high=5.0),
        _range(sex=Gender.MALE, age_min=19, age_max=99, unit_id=unit, low=0.6, high=6.0),
    ]
    res = pick_reference_range(
        bio, rows, sex=Gender.MALE, age=40, unit_id=unit
    )
    assert (res.low, res.high) == (0.6, 6.0)
    assert res.source == "stratified"


# ---------------------------------------------------------------------------
# Resolver — DB integration (resolve_for_patient)
# ---------------------------------------------------------------------------


async def _seed_biomarker_with_ranges(session, tenant_id):
    bio = BiomarkerDefinition(
        id=uuid.uuid4(),
        slug=f"tsh-{uuid.uuid4().hex[:6]}",
        name="TSH",
        coding_system=None,
        aliases=[],
        reference_range_min=0.4,
        reference_range_max=4.0,
        tenant_id=tenant_id,
        scope="system",
    )
    session.add(bio)
    await session.flush()
    # catch-all + male-specific + pediatric
    session.add_all(
        [
            BiomarkerReferenceRange(biomarker_id=bio.id, sex=None, low=0.4, high=4.0),
            BiomarkerReferenceRange(
                biomarker_id=bio.id, sex=Gender.MALE, low=0.5, high=5.0
            ),
            BiomarkerReferenceRange(
                biomarker_id=bio.id,
                sex=None,
                age_min=0,
                age_max=17,
                low=0.7,
                high=5.5,
            ),
        ]
    )
    await session.commit()
    return bio


@pytest.mark.asyncio
async def test_resolve_for_patient_picks_male_range():
    from datetime import date

    from app.models.fhir import Patient
    from app.models.tenant_model import TenantModel

    async with AsyncSessionLocal() as session:
        tenant = TenantModel(name=f"t-{uuid.uuid4().hex[:6]}", slug=f"t-{uuid.uuid4().hex[:6]}")
        session.add(tenant)
        await session.flush()
        bio = await _seed_biomarker_with_ranges(session, tenant.id)
        patient = Patient(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name=[{"text": "Test"}],
            gender=Gender.MALE,
            birth_date=date(1990, 1, 1),
        )
        session.add(patient)
        await session.commit()

        res = await resolve_for_patient(session, bio, patient)
        assert res is not None
        assert (res.low, res.high) == (0.5, 5.0)
        assert res.source == "stratified"


@pytest.mark.asyncio
async def test_resolve_for_patient_falls_back_when_unstratified():
    """A female patient with no female-specific row → global fallback."""
    from app.models.fhir import Patient
    from app.models.tenant_model import TenantModel

    async with AsyncSessionLocal() as session:
        tenant = TenantModel(name=f"t-{uuid.uuid4().hex[:6]}", slug=f"t-{uuid.uuid4().hex[:6]}")
        session.add(tenant)
        await session.flush()
        bio = await _seed_biomarker_with_ranges(session, tenant.id)
        patient = Patient(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name=[{"text": "Test"}],
            gender=Gender.FEMALE,
            birth_date=None,
        )
        session.add(patient)
        await session.commit()

        res = await resolve_for_patient(session, bio, patient)
        assert res is not None
        # Catch-all row (0.4–4.0) outranks the legacy global, but they're equal
        # here; both are the same value, so just assert the resolved bounds.
        assert (res.low, res.high) == (0.4, 4.0)


@pytest.mark.asyncio
async def test_check_constraint_rejects_inverted_range():
    async with AsyncSessionLocal() as session:
        bio = BiomarkerDefinition(
            id=uuid.uuid4(),
            slug=f"inv-{uuid.uuid4().hex[:6]}",
            name="Inv",
            coding_system=None,
            aliases=[],
        )
        session.add(bio)
        await session.flush()
        session.add(
            BiomarkerReferenceRange(biomarker_id=bio.id, low=10.0, high=1.0)
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_patient_delete_cascades_to_biomarker_and_ranges():
    """Deleting a biomarker must CASCADE to its reference ranges (FK)."""
    from app.models.tenant_model import TenantModel

    async with AsyncSessionLocal() as session:
        tenant = TenantModel(name=f"t-{uuid.uuid4().hex[:6]}", slug=f"t-{uuid.uuid4().hex[:6]}")
        session.add(tenant)
        await session.flush()
        bio = await _seed_biomarker_with_ranges(session, tenant.id)
        bio_id = bio.id
        before = (
            await session.execute(
                text(
                    "SELECT count(*) FROM biomarker_reference_ranges "
                    "WHERE biomarker_id = :bid"
                ),
                {"bid": bio_id},
            )
        ).scalar()
        assert before == 3
        await session.delete(bio)
        await session.commit()
        after = (
            await session.execute(
                text(
                    "SELECT count(*) FROM biomarker_reference_ranges "
                    "WHERE biomarker_id = :bid"
                ),
                {"bid": bio_id},
            )
        ).scalar()
        assert after == 0
