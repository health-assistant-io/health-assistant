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


def _translate_vcc(d):
    """SDK schema → ORM kwarg translation.

    The ORM column is ``value_codeableConcept`` (camelCase — predates the
    snake-case convention); the SDK + REST schemas use
    ``value_codeable_concept``. Mirrors the translation in
    ``integration_sync_service.run_sync`` so tests that build an ORM
    Observation directly from an SDK ``model_dump()`` round-trip cleanly.
    """
    vcc = d.pop("value_codeable_concept", None)
    if vcc is not None:
        d["value_codeableConcept"] = vcc
    return d


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
    orm = Observation(**_translate_vcc(obs_create.model_dump(exclude_unset=True)))

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

    # Old (buggy) bridge path — the original bug wrote the value into an
    # internal dict (``_data["value_string"]``) that ``build()`` ignored, so
    # the value was silently dropped. That dict no longer exists (Phase 3.4
    # split the builder into individual attributes), so the bug class is
    # structurally impossible: ``build()`` reads ``_value_string`` directly.
    # Emulate a caller bypassing the public setter and confirm build() picks
    # the value up via the canonical attribute.
    buggy = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("9b4c8f", "SARS-CoV-2 PCR")
        .set_effective_date(_a_tz())
    )
    buggy._value_string = record_value_string  # bypass set_value_string()
    buggy_obs = buggy.build()
    assert buggy_obs.value_string == record_value_string, (
        "build() must read the canonical _value_string attribute — if this "
        "regresses, set_value_string() is no longer the path build() honors."
    )


# ---------------------------------------------------------------------------
# set_value_codeable_concept: STATE biomarker shape (plan Step 9)
# ---------------------------------------------------------------------------

V3 = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


def test_set_value_codeable_concept_emits_value_codeable_concept():
    """The coded-categorical builder path emits value_codeable_concept (the
    proper shape for STATE biomarkers — the validator rejects value_string
    on STATE biomarkers, only valueCodeableConcept is accepted)."""
    obs = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("94500-6", "SARS-CoV-2 PCR")
        .set_value_codeable_concept("POS", V3, display="Positive")
        .set_effective_date(_a_tz())
        .build()
    )
    assert obs.value_codeable_concept == {
        "coding": [{"code": "POS", "system": V3, "display": "Positive"}]
    }
    # Mutual-exclusion: the other value[x] slots are cleared.
    assert obs.value_string is None
    assert obs.value_quantity is None
    # Numeric-derived fields are also None (categoricals are unitless).
    assert obs.raw_value is None
    assert obs.normalized_value is None
    assert obs.relative_score is None


def test_set_value_codeable_concept_clears_numeric_slot():
    """Last value-setter wins — calling set_value_codeable_concept after
    set_value clears the quantitative slot."""
    builder = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("94500-6", "PCR")
        .set_value(1.0, "x")
    )
    builder.set_value_codeable_concept("NEG", V3)
    obs = builder.set_effective_date(_a_tz()).build()
    assert obs.value_codeable_concept is not None
    assert obs.value_quantity is None


def test_set_value_clears_codeable_concept_slot():
    """Reverse direction: set_value after set_value_codeable_concept clears
    the categorical slot."""
    builder = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("2345-7", "Glucose")
        .set_value_codeable_concept("POS", V3)
    )
    builder.set_value(5.5, "mmol/L")
    obs = builder.set_effective_date(_a_tz()).build()
    assert obs.value_quantity is not None
    assert obs.value_codeable_concept is None


def test_value_codeable_concept_passes_fhir_validation():
    """The built observation round-trips through assert_valid_fhir and
    projects as a FHIR Observation with valueCodeableConcept."""
    from app.models.fhir import Observation
    from app.services.fhir_helpers import assert_valid_fhir

    obs_create = (
        ObservationBuilder(TENANT, PATIENT)
        .set_biomarker("94500-6", "SARS-CoV-2 PCR")
        .set_value_codeable_concept("POS", V3, display="Positive")
        .set_effective_date(_a_tz())
        .build()
    )
    orm = Observation(**_translate_vcc(obs_create.model_dump(exclude_unset=True)))

    fhir_dict = assert_valid_fhir(orm)
    assert fhir_dict["resourceType"] == "Observation"
    assert fhir_dict["valueCodeableConcept"]["coding"][0]["code"] == "POS"
    # No leakage of the other value[x] flavors.
    assert "valueQuantity" not in fhir_dict
    assert "valueString" not in fhir_dict
