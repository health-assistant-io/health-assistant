"""Tests for Condition projection from ClinicalEvent.

Covers:
- ClinicalEvent.to_fhir_dict() emits valid FHIR R4 Condition
- All ClinicalEventStatus values map to valid HL7 condition-clinical codes
- Coding systems (LOINC, SNOMED, CUSTOM) project to correct system URLs
- fhir_to_condition_orm() reverse conversion (canonical FHIR → ORM dict)
- Round-trip: ORM → FHIR → ORM preserves identity
- Validation: invalid data rejected via build_fhir_resource
"""
import datetime as _dt
from uuid import uuid4

import pytest

from app.models.clinical_event import ClinicalEvent
from app.models.enums import ClinicalEventStatus, CodingSystem
from app.services.fhir_converter import fhir_to_condition_orm, validate_resource
from app.services.fhir_helpers import FhirSerializationError, parse_fhir_resource


def _make_event(**overrides) -> ClinicalEvent:
    defaults = dict(
        id=str(uuid4()),
        patient_id=str(uuid4()),
        status=ClinicalEventStatus.ACTIVE,
        title="Test Condition",
        description=None,
        onset_date=None,
        resolved_date=None,
        code=None,
        coding_system=None,
        created_at=_dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc),
    )
    defaults.update(overrides)
    return ClinicalEvent(**defaults)


# ---------------------------------------------------------------------------
# to_fhir_dict — basic projection
# ---------------------------------------------------------------------------

def test_condition_minimal_to_fhir_dict():
    event = _make_event(title="Headache")
    fhir = event.to_fhir_dict()

    assert fhir["resourceType"] == "Condition"
    assert fhir["code"]["text"] == "Headache"
    assert "subject" in fhir
    assert "clinicalStatus" in fhir
    assert fhir["clinicalStatus"]["coding"][0]["code"] == "active"


def test_condition_validates_against_fhir_resources():
    event = _make_event(title="Test")
    fhir = event.to_fhir_dict()
    # Should not raise.
    parsed = parse_fhir_resource("Condition", fhir)
    assert parsed.__resource_type__ == "Condition"


def test_condition_status_mapping():
    """Each ClinicalEventStatus must map to a valid HL7 condition-clinical code."""
    cases = [
        (ClinicalEventStatus.ACTIVE, "active"),
        (ClinicalEventStatus.RESOLVED, "resolved"),
        (ClinicalEventStatus.ON_HOLD, "active"),  # ON_HOLD has no FHIR equivalent; falls back to active
        (ClinicalEventStatus.UNKNOWN, "active"),
    ]
    for status, expected_code in cases:
        event = _make_event(status=status)
        fhir = event.to_fhir_dict()
        actual = fhir["clinicalStatus"]["coding"][0]["code"]
        assert actual == expected_code, f"{status} should map to {expected_code}, got {actual}"


def test_condition_with_snomed_code():
    event = _make_event(
        title="Type 2 Diabetes",
        code="44054006",
        coding_system=CodingSystem.SNOMED,
    )
    fhir = event.to_fhir_dict()
    coding = fhir["code"]["coding"][0]
    assert coding["system"] == "http://snomed.info/sct"
    assert coding["code"] == "44054006"
    assert fhir["code"]["text"] == "Type 2 Diabetes"


def test_condition_with_loinc_code():
    event = _make_event(
        title="HbA1c",
        code="4548-4",
        coding_system=CodingSystem.LOINC,
    )
    fhir = event.to_fhir_dict()
    coding = fhir["code"]["coding"][0]
    assert coding["system"] == "http://loinc.org"


def test_condition_with_custom_code():
    event = _make_event(
        title="Symptom",
        code="abc",
        coding_system=CodingSystem.CUSTOM,
    )
    fhir = event.to_fhir_dict()
    coding = fhir["code"]["coding"][0]
    assert coding["system"] == "urn:uuid:health-assistant:custom-biomarker"


def test_condition_onset_and_abatement():
    onset = _dt.datetime(2020, 1, 15, tzinfo=_dt.timezone.utc)
    resolved = _dt.datetime(2024, 6, 1, tzinfo=_dt.timezone.utc)
    event = _make_event(
        status=ClinicalEventStatus.RESOLVED,
        onset_date=onset,
        resolved_date=resolved,
    )
    fhir = event.to_fhir_dict()
    assert fhir["onsetDateTime"].startswith("2020-01-15")
    assert fhir["abatementDateTime"].startswith("2024-06-01")
    assert fhir["onsetDateTime"].endswith("Z")  # canonical UTC form


def test_condition_description_in_note():
    event = _make_event(description="Patient reports intermittent symptoms")
    fhir = event.to_fhir_dict()
    assert fhir["note"][0]["text"] == "Patient reports intermittent symptoms"


def test_condition_recorded_date_from_created_at():
    event = _make_event(created_at=_dt.datetime(2024, 3, 15, 12, 30, tzinfo=_dt.timezone.utc))
    fhir = event.to_fhir_dict()
    assert fhir["recordedDate"].startswith("2024-03-15")


def test_condition_subject_reference():
    pid = str(uuid4())
    event = _make_event(patient_id=pid)
    fhir = event.to_fhir_dict()
    assert fhir["subject"]["reference"] == f"Patient/{pid}"


def test_condition_iso_format_normalizes_naive_datetime():
    """If onset_date has no tzinfo, fhir_isoformat should assume UTC."""
    event = _make_event(onset_date=_dt.datetime(2024, 1, 1))  # naive
    fhir = event.to_fhir_dict()
    # Should end in 'Z' (UTC normalized).
    assert fhir["onsetDateTime"].endswith("Z")


# ---------------------------------------------------------------------------
# Reverse: fhir_to_condition_orm
# ---------------------------------------------------------------------------

def _canonical_condition(**overrides) -> dict:
    base = {
        "resourceType": "Condition",
        "id": str(uuid4()),
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active",
                }
            ]
        },
        "code": {
            "text": "Test Condition",
            "coding": [{"system": "http://snomed.info/sct", "code": "12345"}],
        },
        "subject": {"reference": "Patient/abc-123"},
        "onsetDateTime": "2020-01-15",
    }
    base.update(overrides)
    return base


def test_fhir_to_condition_orm_basic():
    fhir = _canonical_condition()
    orm = fhir_to_condition_orm(fhir)

    assert orm["title"] == "Test Condition"
    assert orm["code"] == "12345"
    assert orm["coding_system"] == CodingSystem.SNOMED
    assert orm["patient_id"] == "abc-123"
    assert orm["status"] == ClinicalEventStatus.ACTIVE
    assert orm["onset_date"] is not None
    assert orm["onset_date"].year == 2020


def test_fhir_to_condition_orm_resolved_status():
    fhir = _canonical_condition(
        clinicalStatus={
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "resolved",
                }
            ]
        },
        abatementDateTime="2024-06-01",
    )
    orm = fhir_to_condition_orm(fhir)
    assert orm["status"] == ClinicalEventStatus.RESOLVED
    assert orm["resolved_date"] is not None
    assert orm["resolved_date"].year == 2024


def test_fhir_to_condition_orm_loinc_system():
    fhir = _canonical_condition(
        code={"text": "x", "coding": [{"system": "http://loinc.org", "code": "4548-4"}]}
    )
    orm = fhir_to_condition_orm(fhir)
    assert orm["coding_system"] == CodingSystem.LOINC


def test_fhir_to_condition_orm_custom_system():
    fhir = _canonical_condition(
        code={"text": "x", "coding": [{"system": "urn:custom", "code": "y"}]}
    )
    orm = fhir_to_condition_orm(fhir)
    assert orm["coding_system"] == CodingSystem.CUSTOM


def test_fhir_to_condition_orm_note_to_description():
    fhir = _canonical_condition(note=[{"text": "a note"}])
    orm = fhir_to_condition_orm(fhir)
    assert orm["description"] == "a note"


def test_fhir_to_condition_orm_drops_unknown_fields():
    fhir = _canonical_condition()
    # Stage and evidence have no ClinicalEvent analog — they're silently dropped.
    fhir["stage"] = [{"summary": {"text": "Severe"}}]
    fhir["evidence"] = [{"code": [{"text": "Symptom X"}]}]
    orm = fhir_to_condition_orm(fhir)
    assert "stage" not in orm
    assert "evidence" not in orm


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_round_trip_orm_to_fhir_to_orm():
    """ORM → FHIR → ORM should preserve the key fields."""
    event = _make_event(
        status=ClinicalEventStatus.ACTIVE,
        title="Migraine",
        code="37796009",
        coding_system=CodingSystem.SNOMED,
        onset_date=_dt.datetime(2021, 5, 1, tzinfo=_dt.timezone.utc),
        description="Recurring",
    )
    fhir = event.to_fhir_dict()
    orm = fhir_to_condition_orm(fhir)

    assert orm["title"] == "Migraine"
    assert orm["code"] == "37796009"
    assert orm["coding_system"] == CodingSystem.SNOMED
    assert orm["patient_id"] == str(event.patient_id)
    assert orm["status"] == ClinicalEventStatus.ACTIVE
    assert orm["description"] == "Recurring"


def test_round_trip_fhir_to_orm_to_fhir():
    """FHIR → ORM → FHIR should produce an equivalent resource."""
    fhir_in = _canonical_condition()
    orm = fhir_to_condition_orm(fhir_in)

    event = ClinicalEvent(
        id=orm.get("id"),
        patient_id=orm.get("patient_id") or str(uuid4()),
        status=orm["status"],
        title=orm["title"],
        description=orm.get("description"),
        onset_date=orm.get("onset_date"),
        code=orm.get("code"),
        coding_system=orm.get("coding_system"),
        created_at=_dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc),
    )
    fhir_out = event.to_fhir_dict()

    assert fhir_out["code"]["coding"][0]["code"] == fhir_in["code"]["coding"][0]["code"]
    assert fhir_out["subject"]["reference"] == fhir_in["subject"]["reference"]
    assert fhir_out["code"]["text"] == fhir_in["code"]["text"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_invalid_condition_rejected_by_to_fhir_dict():
    """Malformed data should be rejected by build_fhir_resource. fhir.resources
    is lenient on cardinality (1..1 not always enforced), but it does enforce
    type-strict validation — e.g. a code field that isn't a CodeableConcept."""
    from app.services.fhir_helpers import build_fhir_resource

    # An obviously invalid Condition: `code` is a string, not a CodeableConcept.
    with pytest.raises(FhirSerializationError):
        build_fhir_resource(
            "Condition",
            {
                "resourceType": "Condition",
                "id": "x",
                "subject": {"reference": "Patient/abc"},
                "code": "not-a-codeable-concept",  # wrong type
            },
        )


def test_canonical_condition_validates():
    fhir = _canonical_condition()
    ok, errs = validate_resource(fhir)
    assert ok, f"Expected valid Condition: {errs}"
