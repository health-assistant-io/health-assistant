"""Categorical (valueString) support on ObservationBuilder.

Regression coverage for the silent-drop bug at
``integrations/health_assistant_bridge/provider.py`` — the bridge poked
``obs_builder._data["value_string"] = ...`` as a private attribute, but
``ObservationBuilder.build()`` only ever read ``value_quantity``, so the
categorical value was discarded before reaching the FHIR validator.

Scope: builder-level correctness + FHIR R4 round-trip (the validator
enforces the value[x] mutual-exclusion rule, so a builder that emits both
valueQuantity and valueString would be silently dropped — the same class
of bug this file guards against).
"""
from datetime import datetime, timezone
from uuid import uuid4

from integrations.sdk.observation_builder import ObservationBuilder


TENANT = uuid4()
PATIENT = uuid4()


def _a_tz():
    return datetime(2026, 7, 21, 9, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# set_value_string: basic shape
# ---------------------------------------------------------------------------


def test_set_value_string_emits_value_string_field():
    obs = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("coll", "Sleep Stage")
        .set_value_string("REM")
        .set_effective_date(_a_tz())
        .build()
    )
    assert obs.value_string == "REM"
    # FHIR R4 §3.1.1: value[x] is exactly one of valueQuantity | valueString | …
    assert obs.value_quantity is None


def test_set_value_emits_value_quantity_and_no_value_string():
    """Quantitative path unchanged by the categorical addition."""
    obs = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72.0, "bpm", "{beats}/min")
        .set_reference_range(low=60, high=100)
        .set_effective_date(_a_tz())
        .build()
    )
    assert obs.value_quantity is not None
    assert obs.value_quantity["value"] == 72.0
    assert obs.value_string is None


# ---------------------------------------------------------------------------
# Mutual exclusion: last setter wins
# ---------------------------------------------------------------------------


def test_set_value_after_set_value_string_clears_string_slot():
    """The last value-setter wins — FHIR R4 forbids both on one observation."""
    obs = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("8867-4", "Heart rate")
        .set_value_string("REM")
        .set_value(72.0, "bpm")
        .set_effective_date(_a_tz())
        .build()
    )
    assert obs.value_quantity is not None
    assert obs.value_quantity["value"] == 72.0
    assert obs.value_string is None


def test_set_value_string_after_set_value_clears_quantity_slot():
    obs = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("coll", "Sleep Stage")
        .set_value(72.0, "bpm")
        .set_value_string("REM")
        .set_effective_date(_a_tz())
        .build()
    )
    assert obs.value_string == "REM"
    assert obs.value_quantity is None


# ---------------------------------------------------------------------------
# Numeric normalization is skipped for categoricals
# ---------------------------------------------------------------------------


def test_categorical_value_has_no_relative_score_or_normalized_value():
    """raw_value/normalized_value/relative_score are numeric concepts.
    A categorical observation must leave them unset so downstream
    analytics doesn't try to chart a string on a numeric axis."""
    obs = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("coll", "Sleep Stage")
        .set_reference_range(low=0, high=3)  # explicitly set; must be ignored
        .set_value_string("REM")
        .set_effective_date(_a_tz())
        .build()
    )
    assert obs.relative_score is None
    assert obs.normalized_value is None
    assert obs.raw_value is None


# ---------------------------------------------------------------------------
# FHIR R4 round-trip — the whole point
# ---------------------------------------------------------------------------


def test_categorical_observation_passes_fhir_validation():
    """A string-valued Observation must round-trip through assert_valid_fhir.

    Before the fix, ``ObservationBuilder.build()`` emitted a default
    ``value_quantity`` even when the caller had poked ``_data["value_string"]``
    via the bridge workaround, producing an Observation with a string
    ``valueQuantity.value`` — which fhir.resources rejects. This test guards
    the corrected builder path.
    """
    from app.models.fhir import Observation
    from app.services.fhir_helpers import assert_valid_fhir

    obs_create = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("coll", "Sleep Stage")
        .set_value_string("REM")
        .set_effective_date(_a_tz())
        .build()
    )
    orm = Observation(**obs_create.model_dump(exclude_unset=True))

    fhir_dict = assert_valid_fhir(orm)
    assert fhir_dict["resourceType"] == "Observation"
    assert fhir_dict.get("valueString") == "REM"
    # The validator must not have synthesized a valueQuantity
    assert "valueQuantity" not in fhir_dict


# ---------------------------------------------------------------------------
# Bridge regression — the workaround site now uses the public method
# ---------------------------------------------------------------------------


def test_bridge_categorical_workaround_no_longer_silently_drops():
    """Reproduces the exact pattern the bridge provider used.

    Before: ``obs_builder._data["value_string"] = record.value_string``
    was a no-op because ``build()`` never read ``_data["value_string"]``.

    After: the bridge calls ``set_value_string()`` and the value survives.
    """
    record_value_string = "POSITIVE"

    # New (fixed) bridge path:
    fixed = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("9b4c8f", "SARS-CoV-2 PCR")
        .set_effective_date(_a_tz())
    )
    fixed.set_value_string(record_value_string)
    fixed_obs = fixed.build()
    assert fixed_obs.value_string == record_value_string

    # Old (buggy) bridge path — emulate it to prove it would have lost data:
    buggy = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("9b4c8f", "SARS-CoV-2 PCR")
        .set_effective_date(_a_tz())
    )
    buggy._data["value_string"] = record_value_string  # the old workaround
    buggy_obs = buggy.build()
    assert buggy_obs.value_string is None, (
        "If the old workaround now survives, build() is reading _data directly "
        "and the public set_value_string() method is no longer the canonical path."
    )
