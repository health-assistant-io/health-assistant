"""Tests for Encounter projection from ExaminationModel.

Covers:
- ExaminationModel.to_fhir_dict() emits valid FHIR R4 Encounter
- Status defaults to 'finished', class defaults to 'AMB'
- examination_date → Encounter.period (full-day range)
- diagnoses JSONB → Encounter.diagnosis[] (display only)
- organization_id → Encounter.serviceProvider
- fhir_to_encounter_orm() reverse conversion
- Round-trip preserves key fields
"""
import datetime as _dt
from uuid import uuid4

import pytest

from app.models.examination_model import ExaminationModel
from app.services.fhir_converter import fhir_to_encounter_orm, validate_resource
from app.services.fhir_helpers import FhirSerializationError, parse_fhir_resource


def _make_exam(**overrides) -> ExaminationModel:
    defaults = dict(
        id=str(uuid4()),
        patient_id=str(uuid4()),
        tenant_id=str(uuid4()),
        examination_date=_dt.date(2024, 3, 15),
        notes=None,
        patient_notes=None,
        diagnoses=None,
        impressions=None,
        organization_id=None,
        created_at=_dt.datetime(2024, 3, 15, 10, 0, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2024, 3, 15, 10, 0, tzinfo=_dt.timezone.utc),
    )
    defaults.update(overrides)
    return ExaminationModel(**defaults)


# ---------------------------------------------------------------------------
# to_fhir_dict — basic projection
# ---------------------------------------------------------------------------

def test_encounter_minimal_to_fhir_dict():
    exam = _make_exam(notes=None)
    fhir = exam.to_fhir_dict()
    assert fhir["resourceType"] == "Encounter"
    assert fhir["status"] == "finished"
    assert "class" in fhir
    assert fhir["class"]["code"] == "AMB"


def test_encounter_validates_against_fhir_resources():
    exam = _make_exam()
    fhir = exam.to_fhir_dict()
    parsed = parse_fhir_resource("Encounter", fhir)
    assert parsed.__resource_type__ == "Encounter"


def test_encounter_subject_reference():
    pid = str(uuid4())
    exam = _make_exam(patient_id=pid)
    fhir = exam.to_fhir_dict()
    assert fhir["subject"]["reference"] == f"Patient/{pid}"


def test_encounter_period_from_examination_date():
    exam = _make_exam(examination_date=_dt.date(2024, 6, 1))
    fhir = exam.to_fhir_dict()
    assert fhir["period"]["start"].startswith("2024-06-01T00:00:00")
    assert fhir["period"]["end"].startswith("2024-06-01T23:59:59")
    # Should be UTC-normalized.
    assert fhir["period"]["start"].endswith("Z")
    assert fhir["period"]["end"].endswith("Z")


def test_encounter_period_none_when_no_date():
    exam = _make_exam(examination_date=None)
    fhir = exam.to_fhir_dict()
    assert "period" not in fhir or fhir.get("period") is None


def test_encounter_class_act_code_system():
    exam = _make_exam()
    fhir = exam.to_fhir_dict()
    assert fhir["class"]["system"] == "http://terminology.hl7.org/CodeSystem/v3-ActCode"


def test_encounter_reason_code_from_notes():
    exam = _make_exam(notes="Routine checkup")
    fhir = exam.to_fhir_dict()
    assert fhir["reasonCode"][0]["text"] == "Routine checkup"


def test_encounter_reason_code_from_patient_notes_fallback():
    exam = _make_exam(notes=None, patient_notes="Patient reports pain")
    fhir = exam.to_fhir_dict()
    assert fhir["reasonCode"][0]["text"] == "Patient reports pain"


def test_encounter_diagnosis_from_jsonb():
    exam = _make_exam(diagnoses=[{"text": "Hypertension"}, {"text": "Diabetes"}])
    fhir = exam.to_fhir_dict()
    assert len(fhir["diagnosis"]) == 2
    displays = [d["condition"]["display"] for d in fhir["diagnosis"]]
    assert "Hypertension" in displays
    assert "Diabetes" in displays


def test_encounter_service_provider_reference():
    org_id = str(uuid4())
    exam = _make_exam(organization_id=org_id)
    fhir = exam.to_fhir_dict()
    assert fhir["serviceProvider"]["reference"] == f"Organization/{org_id}"


def test_encounter_long_notes_truncated():
    long_text = "x" * 500
    exam = _make_exam(notes=long_text)
    fhir = exam.to_fhir_dict()
    # reasonCode.text is capped at 200 chars.
    assert len(fhir["reasonCode"][0]["text"]) <= 200


# ---------------------------------------------------------------------------
# Reverse: fhir_to_encounter_orm
# ---------------------------------------------------------------------------

def _canonical_encounter(**overrides) -> dict:
    base = {
        "resourceType": "Encounter",
        "id": str(uuid4()),
        "status": "finished",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
        },
        "subject": {"reference": "Patient/pat-1"},
        "period": {"start": "2024-03-15T00:00:00Z", "end": "2024-03-15T23:59:59Z"},
        "serviceProvider": {"reference": "Organization/org-1"},
        "reasonCode": [{"text": "Annual checkup"}],
        "diagnosis": [
            {
                "condition": {"display": "Hypertension"},
                "use": {"coding": [{"code": "AD"}]},
            }
        ],
    }
    base.update(overrides)
    return base


def test_fhir_to_encounter_orm_basic():
    fhir = _canonical_encounter()
    orm = fhir_to_encounter_orm(fhir)

    assert orm["patient_id"] == "pat-1"
    assert orm["organization_id"] == "org-1"
    assert orm["examination_date"] == _dt.date(2024, 3, 15)
    assert orm["notes"] == "Annual checkup"


def test_fhir_to_encounter_orm_diagnosis_to_jsonb():
    fhir = _canonical_encounter(
        diagnosis=[
            {"condition": {"display": "HTN"}},
            {"condition": {"display": "Diabetes"}},
        ]
    )
    orm = fhir_to_encounter_orm(fhir)
    assert orm["diagnoses"] == [{"text": "HTN"}, {"text": "Diabetes"}]


def test_fhir_to_encounter_orm_no_period():
    fhir = _canonical_encounter()
    del fhir["period"]
    orm = fhir_to_encounter_orm(fhir)
    assert orm.get("examination_date") is None


def test_fhir_to_encounter_orm_drops_unknown_fields():
    fhir = _canonical_encounter()
    fhir["hospitalization"] = {"dischargeDisposition": {"text": "Home"}}
    fhir["priority"] = [{"text": "ASAP"}]
    orm = fhir_to_encounter_orm(fhir)
    assert "hospitalization" not in orm
    assert "priority" not in orm


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_round_trip_orm_to_fhir_to_orm():
    org_id = str(uuid4())
    exam = _make_exam(
        notes="Annual physical",
        diagnoses=[{"text": "Hyperlipidemia"}],
        organization_id=org_id,
    )
    fhir = exam.to_fhir_dict()
    orm = fhir_to_encounter_orm(fhir)

    assert orm["patient_id"] == str(exam.patient_id)
    assert orm["examination_date"] == exam.examination_date
    assert orm["organization_id"] == org_id
    assert orm["notes"] == "Annual physical"
    assert orm["diagnoses"] == [{"text": "Hyperlipidemia"}]


def test_round_trip_fhir_to_orm_to_fhir():
    fhir_in = _canonical_encounter()
    orm = fhir_to_encounter_orm(fhir_in)

    exam = ExaminationModel(
        id=orm.get("id"),
        patient_id=orm.get("patient_id"),
        tenant_id=str(uuid4()),
        examination_date=orm.get("examination_date"),
        notes=orm.get("notes"),
        diagnoses=orm.get("diagnoses"),
        organization_id=orm.get("organization_id"),
    )
    fhir_out = exam.to_fhir_dict()

    assert fhir_out["subject"]["reference"] == fhir_in["subject"]["reference"]
    assert fhir_out["period"]["start"].startswith("2024-03-15")
    if fhir_in.get("diagnosis"):
        displays = [d["condition"]["display"] for d in fhir_out["diagnosis"]]
        assert "Hypertension" in displays


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_canonical_encounter_validates():
    fhir = _canonical_encounter()
    ok, errs = validate_resource(fhir)
    assert ok, f"Expected valid Encounter: {errs}"


def test_invalid_encounter_rejected():
    """Malformed Encounter (class is a string, not a Coding) should be rejected."""
    from app.services.fhir_helpers import build_fhir_resource

    with pytest.raises(FhirSerializationError):
        build_fhir_resource(
            "Encounter",
            {
                "resourceType": "Encounter",
                "id": "x",
                "status": "finished",
                "class": "not-a-coding",  # wrong type — must be a Coding dict
            },
        )
