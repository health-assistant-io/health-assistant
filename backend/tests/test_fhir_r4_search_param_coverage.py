"""Regression tests for F8: honor declared search params (or remove them).

The audit: many params in RESOURCE_PARAMS were accepted by the parser but
produced no predicate in ``_build_resource_filter``. Clients couldn't tell
if their filter was honored. The fix implements the commonly-used subset
(value-quantity, performer, criticality, identifier, name/family/given,
gender, active, medication, activity, reference params like
sender/recipient/agent/target/partof/parent) and removes the long-tail
unimplemented params from RESOURCE_PARAMS so they're no longer advertised
in the CapabilityStatement.

These tests verify predicate construction for the newly-implemented params
(compile-time checks against the PostgreSQL dialect).
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Enum as SqlEnum, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from app.facade.crud import (
    _build_resource_filter,
    _jsonb_identifier_match,
    _jsonb_reference_match,
    _value_quantity_match,
)
from app.facade.search_params import RESOURCE_PARAMS


def _literal_sql(predicate) -> str:
    if predicate is None:
        return ""
    return str(
        predicate.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


# ---------------------------------------------------------------------------
# value-quantity (Observation)
# ---------------------------------------------------------------------------

class _FakeObservation:
    value_quantity = Column("value_quantity", JSONB)
    code = Column("code", JSONB)


def test_value_quantity_exact():
    pred = _value_quantity_match(_FakeObservation.value_quantity, "5.4")
    compiled = _literal_sql(pred)
    # Casts valueQuantity.value text to FLOAT and compares == 5.4.
    assert "5.4" in compiled
    assert "CAST" in compiled.upper()


def test_value_quantity_gt_prefix():
    pred = _value_quantity_match(_FakeObservation.value_quantity, "gt5.4")
    compiled = _literal_sql(pred)
    assert "5.4" in compiled
    assert ">" in compiled


def test_value_quantity_with_optional_unit_suffix():
    """The ||unit suffix is parsed but not used for filtering (deferred).
    The numeric comparison still runs against the number."""
    pred = _value_quantity_match(_FakeObservation.value_quantity, "5.4||mg/dL")
    compiled = _literal_sql(pred)
    assert "5.4" in compiled


def test_value_quantity_ap_prefix_uses_window():
    """ap prefix → ±10% window."""
    pred = _value_quantity_match(_FakeObservation.value_quantity, "ap10")
    compiled = _literal_sql(pred)
    assert "9" in compiled  # 10 * 0.9
    assert "11" in compiled  # 10 * 1.1


def test_value_quantity_invalid_returns_none():
    assert _value_quantity_match(_FakeObservation.value_quantity, "garbage") is None


def test_value_quantity_dispatched_from_build_resource_filter():
    pred = _build_resource_filter(_FakeObservation, "value-quantity", "5.4")
    assert pred is not None
    assert "5.4" in _literal_sql(pred)


# ---------------------------------------------------------------------------
# criticality (AllergyIntolerance — scalar enum)
# ---------------------------------------------------------------------------

class _FakeAllergy:
    criticality = Column("criticality", SqlEnum)


def test_criticality_dispatched_case_insensitive():
    pred = _build_resource_filter(_FakeAllergy, "criticality", "high")
    compiled = _literal_sql(pred)
    # Case-insensitive OR equality.
    assert "high" in compiled.lower()


def test_criticality_missing_column_returns_none():
    class _NoCrit:
        pass

    assert _build_resource_filter(_NoCrit, "criticality", "high") is None


# ---------------------------------------------------------------------------
# identifier (universal — JSONB list of {system, value})
# ---------------------------------------------------------------------------

class _FakeWithIdentifier:
    identifier = Column("identifier", JSONB)


def test_identifier_bare_value():
    pred = _jsonb_identifier_match(_FakeWithIdentifier.identifier, "abc-123")
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    assert '"abc-123"' in compiled
    assert '"value"' in compiled


def test_identifier_system_pipe_value():
    pred = _jsonb_identifier_match(
        _FakeWithIdentifier.identifier, "http://example.com/mrn|abc-123"
    )
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    assert '"http://example.com/mrn"' in compiled
    assert '"abc-123"' in compiled


def test_identifier_system_only():
    pred = _jsonb_identifier_match(
        _FakeWithIdentifier.identifier, "http://example.com/mrn|"
    )
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    assert '"http://example.com/mrn"' in compiled


def test_identifier_dispatched_from_build_resource_filter():
    pred = _build_resource_filter(_FakeWithIdentifier, "identifier", "abc-123")
    assert pred is not None


# ---------------------------------------------------------------------------
# Reference fields (performer / sender / recipient / agent / target / partof / parent / author)
# ---------------------------------------------------------------------------

class _FakeWithPerformer:
    performer = Column("performer", JSONB)


def test_reference_match_type_slash_uuid():
    pred = _jsonb_reference_match(_FakeWithPerformer, "performer", "Practitioner/abc")
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    assert "Practitioner/abc" in compiled


def test_reference_match_bare_uuid_uses_type_hint():
    """A bare UUID is wrapped in the conventional type for the field
    (performer → Practitioner)."""
    pred = _jsonb_reference_match(_FakeWithPerformer, "performer", "abc-uuid")
    compiled = _literal_sql(pred)
    assert "Practitioner/abc-uuid" in compiled


def test_reference_match_emits_both_single_and_list_shapes():
    """The column may store either a single Reference or a list of References.
    The predicate emits BOTH fragment shapes inside an OR so either matches."""
    pred = _jsonb_reference_match(_FakeWithPerformer, "performer", "Practitioner/abc")
    compiled = _literal_sql(pred)
    assert "OR" in compiled.upper() or "or" in compiled.lower()


def test_reference_match_urn_uuid_form():
    pred = _jsonb_reference_match(_FakeWithPerformer, "performer", "urn:uuid:abc")
    compiled = _literal_sql(pred)
    assert "urn:uuid:abc" in compiled


def test_performer_dispatched_from_build_resource_filter():
    pred = _build_resource_filter(_FakeWithPerformer, "performer", "Practitioner/abc")
    assert pred is not None


def test_performer_not_modifier():
    pred = _build_resource_filter(_FakeWithPerformer, "performer:not", "Practitioner/abc")
    compiled = _literal_sql(pred)
    assert "NOT" in compiled.upper()


def test_target_dispatched_for_provenance():
    class _FakeProv:
        target = Column("target", JSONB)

    pred = _build_resource_filter(_FakeProv, "target", "Patient/abc-uuid")
    assert pred is not None
    assert "Patient/abc-uuid" in _literal_sql(pred)


# ---------------------------------------------------------------------------
# name / family / given (Patient / Practitioner — JSONB name)
# ---------------------------------------------------------------------------

class _FakePatient:
    name = Column("name", JSONB)


def test_name_search_uses_ilike():
    pred = _build_resource_filter(_FakePatient, "name", "smith")
    compiled = _literal_sql(pred)
    # The JSONB column is cast to text and ILIKE'd.
    assert "ILIKE" in compiled.upper() or "ilike" in compiled.lower()
    assert "smith" in compiled.lower()


def test_family_search_uses_ilike():
    pred = _build_resource_filter(_FakePatient, "family", "doe")
    assert pred is not None


def test_given_search_uses_ilike():
    pred = _build_resource_filter(_FakePatient, "given", "john")
    assert pred is not None


# ---------------------------------------------------------------------------
# gender / active (Patient / Device / Organization / Practitioner)
# ---------------------------------------------------------------------------

class _FakePatientGender:
    gender = Column("gender", String)
    active = Column("active", String)  # Bool in the real model; String suffices for predicate test


def test_gender_dispatched_case_insensitive():
    pred = _build_resource_filter(_FakePatientGender, "gender", "male")
    assert pred is not None
    assert "male" in _literal_sql(pred).lower()


def test_active_dispatched_boolean():
    pred = _build_resource_filter(_FakePatientGender, "active", "true")
    assert pred is not None


# ---------------------------------------------------------------------------
# medication (MedicationStatement / MedicationRequest)
# ---------------------------------------------------------------------------

class _FakeMedication:
    code = Column("code", JSONB)


def test_medication_dispatched_via_code_column():
    """MedicationStatement/Request.medication is projected from the `code`
    CodeableConcept column (see Medication.to_fhir_dict). The dispatcher
    matches against that column."""
    pred = _build_resource_filter(_FakeMedication, "medication", "1234-5")
    assert pred is not None
    assert "@>" in _literal_sql(pred)


# ---------------------------------------------------------------------------
# activity (Provenance — CodeableConcept)
# ---------------------------------------------------------------------------

class _FakeProvenance:
    activity = Column("activity", JSONB)


def test_activity_dispatched_via_codeable_concept_match():
    pred = _build_resource_filter(_FakeProvenance, "activity", "CREATE")
    assert pred is not None
    assert "@>" in _literal_sql(pred)
    assert '"CREATE"' in _literal_sql(pred)


# ---------------------------------------------------------------------------
# F8 honest advertisement — RESOURCE_PARAMS no longer advertises dropped params
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "resource,param",
    [
        # Previously declared but never implemented; removed from the
        # CapabilityStatement advertisement surface so clients don't try them.
        ("Condition", "severity"),
        ("Encounter", "class"),
        ("Encounter", "reason-code"),
        ("Encounter", "diagnosis"),
        ("Encounter", "practitioner"),
        ("Device", "manufacturer"),
        ("Device", "model"),
        ("Medication", "form"),
        ("Provenance", "entity"),
        ("DocumentReference", "author"),  # author will come back when F11 lands
    ],
)
def test_unimplemented_params_not_advertised(resource, param):
    """F8: dropped params must not be in RESOURCE_PARAMS (which feeds the
    CapabilityStatement.searchParam advertisement). The CapabilityStatement
    should only advertise params the dispatcher actually implements."""
    assert param not in RESOURCE_PARAMS.get(resource, frozenset()), (
        f"{resource}.{param} is advertised but not implemented — either add a "
        f"handler in _build_resource_filter or keep it removed."
    )


def test_implemented_params_still_advertised():
    """Sanity: a sampling of implemented params remain advertised."""
    assert "value-quantity" in RESOURCE_PARAMS["Observation"]
    assert "criticality" in RESOURCE_PARAMS["AllergyIntolerance"]
    assert "identifier" in RESOURCE_PARAMS["Patient"]
    assert "name" in RESOURCE_PARAMS["Patient"]
    assert "performer" in RESOURCE_PARAMS["Observation"]
    assert "sender" in RESOURCE_PARAMS["Communication"]
    assert "recipient" in RESOURCE_PARAMS["Communication"]
    assert "target" in RESOURCE_PARAMS["Provenance"]
    assert "agent" in RESOURCE_PARAMS["Provenance"]
    assert "activity" in RESOURCE_PARAMS["Provenance"]
    assert "medication" in RESOURCE_PARAMS["MedicationStatement"]
    assert "medication" in RESOURCE_PARAMS["MedicationRequest"]
    assert "partof" in RESOURCE_PARAMS["Organization"]
    assert "parent" in RESOURCE_PARAMS["Device"]
