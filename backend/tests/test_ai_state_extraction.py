"""Tests for the OCR NLP state-extraction path (plan Step 8).

Covers:
1. The Pydantic schema ``KnownBiomarkerExtract`` accepts numeric OR state
   values but rejects both / neither (the validator that prevents the
   "loosen value: float → silent breakage" footgun).
2. ``save_observation`` builds an Observation with ``valueCodeableConcept``
   (and leaves raw_value / normalized_value / relative_score NULL) when the
   extract carries a state code, and skips the numeric pipeline entirely.
3. The legacy QUANTITY path is unchanged.
"""
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ai.pipeline.persistence import save_observation
from app.ai.schemas.nlp import KnownBiomarkerExtract
from app.models.enums import BiomarkerValueType, QuantityType


V3 = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


# ---------------------------------------------------------------------------
# Helpers (mirrors test_ocr_persistence_scoring.py shape)
# ---------------------------------------------------------------------------


def _unit(unit_id, symbol, multiplier=1.0):
    return SimpleNamespace(
        id=unit_id,
        symbol=symbol,
        conversion_multiplier=multiplier,
        quantity_type=QuantityType.OTHER,
        name=symbol,
    )


def _state(code, system, display):
    return SimpleNamespace(code=code, system=system, display=display)


def _allowed(state, is_normal=False):
    return SimpleNamespace(state=state, is_normal=is_normal, sort_order=0)


# POS + NEG allowed states so the validator can verify membership.
POS_NEG_ALLOWED = [
    _allowed(_state("POS", V3, "Positive"), is_normal=False),
    _allowed(_state("NEG", V3, "Negative"), is_normal=True),
]


def _state_bio(bio_id, *, multi=False, allowed_states=None):
    """A STATE BiomarkerDefinition double."""
    return SimpleNamespace(
        id=bio_id,
        slug="sars-cov-2-pcr",
        code="94500-6",
        name="SARS-CoV-2 PCR",
        preferred_unit_id=None,
        category=None,
        coding_system=None,
        class_concept=None,
        value_type=BiomarkerValueType.STATE,
        supports_multi_state=multi,
        allowed_states=allowed_states or [],
    )


def _quantity_bio(bio_id, preferred_unit_id=None):
    return SimpleNamespace(
        id=bio_id,
        slug="glucose",
        code="2345-7",
        name="Glucose",
        preferred_unit_id=preferred_unit_id,
        category=None,
        coding_system=None,
        class_concept=None,
        value_type=BiomarkerValueType.QUANTITY,
        supports_multi_state=False,
        allowed_states=[],
    )


def _exam():
    exam_id, tenant_id, patient_id = uuid4(), uuid4(), uuid4()
    return SimpleNamespace(
        id=exam_id,
        tenant_id=tenant_id,
        patient_id=patient_id,
        examination_date=None,
    )


def _intercept_obs(db_mock):
    calls = db_mock.add.call_args_list
    assert calls, "save_observation did not call db.add(...)"
    return calls[-1].args[0]


# ---------------------------------------------------------------------------
# 1. Pydantic schema validator
# ---------------------------------------------------------------------------


def test_known_biomarker_extract_accepts_numeric_value():
    b = KnownBiomarkerExtract(
        name="Glucose",
        matched_slug="glucose",
        value=5.5,
        unit_symbol="mmol/L",
    )
    assert b.value == 5.5
    assert b.value_state_code is None


def test_known_biomarker_extract_accepts_state_value():
    b = KnownBiomarkerExtract(
        name="SARS-CoV-2 PCR",
        matched_slug="sars-cov-2-pcr",
        value_state_code="POS",
        value_state_system=V3,
        value_state_display="Positive",
    )
    assert b.value_state_code == "POS"
    assert b.value is None


def test_known_biomarker_extract_rejects_both_numeric_and_state():
    with pytest.raises(ValidationError) as exc:
        KnownBiomarkerExtract(
            name="X",
            matched_slug="x",
            value=1.0,
            unit_symbol="mg/dL",
            value_state_code="POS",
        )
    assert "either value" in str(exc.value).lower()


def test_known_biomarker_extract_rejects_neither_value():
    """A biomarker extract must commit to either numeric or state — never
    neither (would silently produce a valueless Observation)."""
    with pytest.raises(ValidationError) as exc:
        KnownBiomarkerExtract(name="X", matched_slug="x")
    assert "value" in str(exc.value).lower()


def test_known_biomarker_extract_rejects_state_with_unit():
    """State values are unitless — a unit_symbol alongside value_state_code
    is contradictory."""
    with pytest.raises(ValidationError) as exc:
        KnownBiomarkerExtract(
            name="X",
            matched_slug="x",
            value_state_code="POS",
            unit_symbol="mg/dL",
        )
    assert "unit" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# 2. save_observation STATE branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_observation_state_branch_builds_value_codeable_concept():
    """A state extract produces an Observation with valueCodeableConcept and
    no numeric fields (raw_value/normalized_value/relative_score all NULL)."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock()
        )
    )

    b = KnownBiomarkerExtract(
        name="SARS-CoV-2 PCR",
        matched_slug="sars-cov-2-pcr",
        value_state_code="POS",
        value_state_system=V3,
        value_state_display="Positive",
    )
    exam = _exam()

    await save_observation(
        db,
        b,
        target_bio=_state_bio(uuid4(), allowed_states=POS_NEG_ALLOWED),
        units_by_symbol={},
        exam=exam,
        patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.datetime.now(datetime.timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.value_codeable_concept is not None
    coding = obs.value_codeable_concept["coding"]
    assert coding[0]["code"] == "POS"
    assert coding[0]["system"] == V3
    assert coding[0]["display"] == "Positive"
    # No numeric shape at all.
    assert obs.value_quantity is None
    assert obs.raw_value is None
    assert obs.normalized_value is None
    assert obs.relative_score is None
    assert obs.raw_unit_id is None
    assert obs.lab_reference_range is None


@pytest.mark.asyncio
async def test_save_observation_state_branch_skips_when_target_is_quantity():
    """If the model mis-extracted a state value for a QUANTITY biomarker,
    skip the row rather than writing a valueCodeableConcept against a numeric
    biomarker (the hard validator would reject it anyway on the read paths
    that consult the biomarker's contract)."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    b = KnownBiomarkerExtract(
        name="Glucose",
        matched_slug="glucose",
        value_state_code="POS",
        value_state_system=V3,
    )
    exam = _exam()

    await save_observation(
        db,
        b,
        target_bio=_quantity_bio(uuid4()),
        units_by_symbol={},
        exam=exam,
        patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.datetime.now(datetime.timezone.utc),
    )

    assert not db.add.called, "save_observation should skip the row entirely"


@pytest.mark.asyncio
async def test_save_observation_state_branch_defaults_system_when_model_omits():
    """The LLM may legitimately omit value_state_system; default to the
    canonical HL7 v3-ObservationInterpretation URL."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock()
        )
    )

    b = KnownBiomarkerExtract(
        name="PCR",
        matched_slug="sars-cov-2-pcr",
        value_state_code="NEG",  # no system, no display
    )
    exam = _exam()

    await save_observation(
        db,
        b,
        target_bio=_state_bio(uuid4(), allowed_states=POS_NEG_ALLOWED),
        units_by_symbol={},
        exam=exam,
        patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.datetime.now(datetime.timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.value_codeable_concept["coding"][0]["system"] == V3


# ---------------------------------------------------------------------------
# 3. QUANTITY path unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_observation_quantity_path_unchanged():
    """The legacy numeric path is untouched: state fields are absent →
    value_quantity / raw_value / normalized_value / relative_score populate
    exactly as before."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock()
        )
    )

    b = KnownBiomarkerExtract(
        name="Glucose",
        matched_slug="glucose",
        value=5.5,
        unit_symbol="mmol/L",
        reference_range_min=3.9,
        reference_range_max=5.5,
    )
    exam = _exam()

    await save_observation(
        db,
        b,
        target_bio=_quantity_bio(uuid4()),
        units_by_symbol={"mmol/l": _unit(uuid4(), "mmol/L", 1.0)},
        exam=exam,
        patient_ref=f"Patient/{exam.patient_id}",
        effective_date=datetime.datetime.now(datetime.timezone.utc),
    )

    obs = _intercept_obs(db)
    assert obs.value_codeable_concept is None
    assert obs.value_quantity == {"value": 5.5, "unit": "mmol/L"}
    assert obs.raw_value == 5.5
    assert obs.normalized_value is not None
    assert obs.relative_score is not None
