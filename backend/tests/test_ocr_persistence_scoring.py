"""Regression tests for OCR persistence: relative_score (C7) + normalized_value (C8).

Pre-fix contract: ``save_observation`` in ``app.ai.pipeline.persistence`` —
1. Never computed ``relative_score``. Every OCR-sourced observation had
   ``relative_score = NULL``, so ``_get_observation_status`` step 2 in
   ``analytics_service.py:104-107`` was dead code for OCR data, and the
   biomarker engine's scoring feature was silently disabled.
2. Computed ``normalized_value`` only when the biomarker had a preferred
   unit *different* from the matched raw unit, AND used the wrong direction:
   ``val_float * matched_unit.conversion_multiplier`` converts raw → base SI,
   not raw → preferred. Trends mixed units across labs.

Post-fix contract pinned here:
1. ``relative_score`` is computed whenever the extracted reference range has
   both ``min`` and ``max`` with ``max > min``, mirroring
   ``ObservationBuilder.build()`` in the integrations SDK:
   ``(value - min) / (max - min)`` clamped to ``[0.0, 1.0]``.
2. Incomplete reference range (only min OR only max) → ``relative_score = 0.5``
   (middle default, matches ObservationBuilder).
3. ``normalized_value`` is now expressed in the biomarker's *preferred* unit.
   If no preferred unit is set, it falls back to the base SI unit so trends
   are at least consistent across labs reporting in different raw units.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.pipeline.persistence import save_observation
from app.ai.schemas.nlp import KnownBiomarkerExtract
from app.models.enums import QuantityType


def _unit(unit_id, symbol, multiplier):
    """Build a minimal Unit-like object for tests."""
    return SimpleNamespace(
        id=unit_id,
        symbol=symbol,
        conversion_multiplier=multiplier,
        quantity_type=QuantityType.OTHER,
        name=symbol,
    )


def _biomarker(bio_id, preferred_unit_id=None):
    """Build a minimal BiomarkerDefinition-like object for tests."""
    return SimpleNamespace(
        id=bio_id,
        slug=f"bio-{bio_id}",
        code=f"code-{bio_id}",
        name=f"Biomarker {bio_id}",
        preferred_unit_id=preferred_unit_id,
        category=None,
        coding_system=None,
        class_concept=None,
    )


def _exam(tenant_id, patient_id, exam_id):
    return SimpleNamespace(
        id=exam_id,
        tenant_id=tenant_id,
        patient_id=patient_id,
        examination_date=None,
    )


def _build_extract(value, ref_min=None, ref_max=None, unit="mg/dL"):
    return KnownBiomarkerExtract(
        name="Glucose",
        matched_slug="glucose",
        value=value,
        unit_symbol=unit,
        reference_range_min=ref_min,
        reference_range_max=ref_max,
    )


def _intercept_obs(db_mock):
    """Return the Observation that save_observation added to the session."""
    calls = db_mock.add.call_args_list
    assert calls, "save_observation did not call db.add(...)"
    return calls[-1].args[0]


# ---------------------------------------------------------------------------
# C7: relative_score computed in OCR path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c7_relative_score_computed_inside_range():
    """Value mid-range → relative_score strictly inside (0, 1)."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    # db.begin_nested must return an async context manager
    db.begin_nested = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))

    b = _build_extract(value=5.0, ref_min=0.0, ref_max=10.0)
    exam = _exam(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    await save_observation(
        db, b, target_bio=_biomarker(uuid.uuid4()),
        units_by_symbol={"mg/dl": _unit(uuid.uuid4(), "mg/dL", 1.0)},
        exam=exam, patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.now(timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.relative_score is not None, (
        "OCR-sourced observations must populate relative_score; was previously NULL."
    )
    assert obs.relative_score == pytest.approx(0.5)
    assert 0.0 < obs.relative_score < 1.0


@pytest.mark.asyncio
async def test_c7_relative_score_clamped_below_range():
    """Value below range → relative_score clamped to 0.0."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    b = _build_extract(value=-5.0, ref_min=0.0, ref_max=10.0)
    exam = _exam(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    await save_observation(
        db, b, target_bio=_biomarker(uuid.uuid4()),
        units_by_symbol={"mg/dl": _unit(uuid.uuid4(), "mg/dL", 1.0)},
        exam=exam, patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.now(timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.relative_score == 0.0


@pytest.mark.asyncio
async def test_c7_relative_score_clamped_above_range():
    """Value above range → relative_score clamped to 1.0."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    b = _build_extract(value=100.0, ref_min=0.0, ref_max=10.0)
    exam = _exam(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    await save_observation(
        db, b, target_bio=_biomarker(uuid.uuid4()),
        units_by_symbol={"mg/dl": _unit(uuid.uuid4(), "mg/dL", 1.0)},
        exam=exam, patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.now(timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.relative_score == 1.0


@pytest.mark.asyncio
async def test_c7_relative_score_incomplete_range_uses_middle_default():
    """Only min OR only max present → 0.5 (matches ObservationBuilder)."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    b = _build_extract(value=42.0, ref_min=10.0)  # no max
    exam = _exam(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    await save_observation(
        db, b, target_bio=_biomarker(uuid.uuid4()),
        units_by_symbol={"mg/dl": _unit(uuid.uuid4(), "mg/dL", 1.0)},
        exam=exam, patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.now(timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.relative_score == 0.5


@pytest.mark.asyncio
async def test_c7_relative_score_null_when_no_range():
    """No reference range at all → relative_score stays NULL (None)."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    b = _build_extract(value=5.0)  # no ref_min, no ref_max
    exam = _exam(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    await save_observation(
        db, b, target_bio=_biomarker(uuid.uuid4()),
        units_by_symbol={"mg/dl": _unit(uuid.uuid4(), "mg/dL", 1.0)},
        exam=exam, patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.now(timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.relative_score is None


# ---------------------------------------------------------------------------
# C8: normalized_value direction + skip-condition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c8_normalized_value_expressed_in_preferred_unit():
    """normalized_value must be expressed in the biomarker's preferred unit,
    not the raw unit and not the base SI unit.

    Setup: raw unit "g/dL" (multiplier 1000 → base mg/dL). Preferred unit
    "mg/dL" (multiplier 1 → base mg/dL). Value 2 g/dL.
    Expected: normalized = 2 * 1000 / 1 = 2000 mg/dL (the preferred unit).
    Pre-fix: would have been 2 * 1000 = 2000 (base SI) — same number here
    by coincidence (preferred IS base), so the bug is invisible in this case.
    See the next test for the case where preferred ≠ base.
    """
    preferred_unit_id = uuid.uuid4()
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    b = _build_extract(value=2.0, unit="g/dL")
    exam = _exam(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    units = {
        "g/dl": _unit(uuid.uuid4(), "g/dL", 1000.0),
        "mg/dl": _unit(preferred_unit_id, "mg/dL", 1.0),
    }

    await save_observation(
        db, b, target_bio=_biomarker(uuid.uuid4(), preferred_unit_id=preferred_unit_id),
        units_by_symbol=units, exam=exam,
        patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.now(timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.normalized_value == pytest.approx(2000.0)


@pytest.mark.asyncio
async def test_c8_normalized_value_direction_is_raw_to_preferred():
    """The previous code computed raw * matched.conversion_multiplier and
    labelled it normalized_value. That converts raw → base SI, NOT raw →
    preferred. The fix divides base by preferred's multiplier so the result
    is genuinely in the preferred unit.

    Setup: raw "mg/dL" (mult 1, base mg/dL). Preferred "g/dL" (mult 1000,
    meaning 1 g/dL = 1000 mg/dL base). Value 5000 mg/dL.
    Expected normalized = 5000 * 1 / 1000 = 5.0 g/dL (the preferred unit).
    Pre-fix: would have been 5000 * 1 = 5000 (base SI) — wrong unit.
    """
    preferred_unit_id = uuid.uuid4()
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    b = _build_extract(value=5000.0, unit="mg/dL")
    exam = _exam(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    units = {
        "mg/dl": _unit(uuid.uuid4(), "mg/dL", 1.0),
        "g/dl": _unit(preferred_unit_id, "g/dL", 1000.0),
    }

    await save_observation(
        db, b, target_bio=_biomarker(uuid.uuid4(), preferred_unit_id=preferred_unit_id),
        units_by_symbol=units, exam=exam,
        patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.now(timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.normalized_value == pytest.approx(5.0), (
        f"normalized_value must be in the preferred unit (g/dL); got {obs.normalized_value}"
    )


@pytest.mark.asyncio
async def test_c8_normalized_value_when_no_preferred_unit_falls_back_to_base():
    """Auto-created biomarkers (no preferred unit) used to get normalized_value
    == raw_value, mixing units across labs. The fix falls back to the base SI
    unit so trends are at least consistent.

    Setup: raw "g/dL" (mult 1000 → base mg/dL). Value 2 g/dL. No preferred.
    Expected: normalized = 2 * 1000 = 2000 (base mg/dL).
    """
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    b = _build_extract(value=2.0, unit="g/dL")
    exam = _exam(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    units = {
        "g/dl": _unit(uuid.uuid4(), "g/dL", 1000.0),
    }

    await save_observation(
        db, b, target_bio=_biomarker(uuid.uuid4(), preferred_unit_id=None),
        units_by_symbol=units, exam=exam,
        patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.now(timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.normalized_value == pytest.approx(2000.0), (
        "Without a preferred unit, normalized_value should fall back to base SI "
        "(2000 mg/dL), not the raw value (2 g/dL) — otherwise trends mix units."
    )
