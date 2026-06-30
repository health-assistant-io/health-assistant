"""Stage 1.1 — write-time FHIR validation.

These tests cover the write-time gate (`assert_valid_fhir`) and the Medication
status hardening that makes pre-flush validation possible. They construct ORM
objects directly (no DB needed) and assert that:
  - valid shapes pass (including the minimal `code={}` create-flow default),
  - genuinely malformed data is rejected (the gate that prevents the bug class
    that caused the export/patient-creation failures),
  - the legacy bug shapes round-trip to valid FHIR via the read-side normalizers,
  - Medication.to_fhir_dict works with both raw-string and enum status.
"""
import uuid

import pytest

from app.models.enums import Gender, MedicationStatus
from app.models.fhir.medication import Medication
from app.models.fhir.patient import DiagnosticReport, Observation, Patient
from app.services.fhir_helpers import (
    FhirSerializationError,
    _enum_value,
    assert_valid_fhir,
    validate_and_filter_observations,
)


def _pid():
    return str(uuid.uuid4())


# ---------- assert_valid_fhir: valid shapes pass ----------

@pytest.mark.parametrize("ctor", [
    lambda: Patient(id=uuid.uuid4(), name={"given": ["A"], "family": "B"}, gender=Gender.MALE),
    lambda: Patient(id=uuid.uuid4(), name=[{"given": ["A"], "family": "B"}], gender=Gender.FEMALE),
    lambda: Observation(id=uuid.uuid4(), status="final", code={}, subject={"reference": f"Patient/{_pid()}"}),
    lambda: Observation(id=uuid.uuid4(), status="final", code={"text": "HR"}, subject={"reference": f"Patient/{_pid()}"}),
    lambda: DiagnosticReport(id=uuid.uuid4(), status="final", code={}, subject={"reference": f"Patient/{_pid()}"}),
    lambda: Medication(id=uuid.uuid4(), patient_id=uuid.uuid4(), code={"text": "Aspirin"}, status="ACTIVE",
                       subject={"reference": f"Patient/{_pid()}"}),
])
def test_valid_shapes_pass_validation(ctor):
    # Should not raise
    fhir = assert_valid_fhir(ctor())
    assert isinstance(fhir, dict)
    assert "resourceType" in fhir


# ---------- assert_valid_fhir: genuinely malformed data is rejected ----------

def test_observation_value_quantity_as_string_rejected():
    # valueQuantity must be an object, not a string — _clean_quantity passes
    # non-dicts through unchanged so fhir.resources rejects it.
    obs = Observation(
        id=uuid.uuid4(), status="final", code={"text": "X"},
        subject={"reference": f"Patient/{_pid()}"},
        value_quantity="72 bpm",
    )
    with pytest.raises(FhirSerializationError):
        assert_valid_fhir(obs)


def test_observation_code_as_string_rejected():
    obs = Observation(
        id=uuid.uuid4(), status="final", code="HeartRate",
        subject={"reference": f"Patient/{_pid()}"},
    )
    with pytest.raises(FhirSerializationError):
        assert_valid_fhir(obs)


def test_assert_valid_fhir_rejects_object_without_serializer():
    class NoFhir:
        pass
    with pytest.raises(FhirSerializationError):
        assert_valid_fhir(NoFhir())


# ---------- legacy bug shapes round-trip to valid FHIR (normalized) ----------

def test_legacy_empty_value_quantity_code_normalizes_and_passes():
    # The exact shape that broke export: empty-string code/unit. The read-side
    # _clean_quantity normalizer drops them so the projection is valid FHIR.
    obs = Observation(
        id=uuid.uuid4(), status="final", code={"text": "HR"},
        subject={"reference": f"Patient/{_pid()}"},
        value_quantity={"value": 72, "unit": "", "system": "http://unitsofmeasure.org", "code": ""},
    )
    fhir = assert_valid_fhir(obs)
    assert "code" not in fhir["valueQuantity"]  # empty code dropped
    assert fhir["valueQuantity"]["value"] == 72


def test_legacy_name_as_dict_normalizes_and_passes():
    # The shape that broke export: name stored as a single dict. _coerce_human_name_list
    # wraps it into the FHIR List[HumanName].
    p = Patient(id=uuid.uuid4(), name={"given": ["John"], "family": "Wich"}, gender=Gender.MALE)
    fhir = assert_valid_fhir(p)
    assert fhir["name"] == [{"given": ["John"], "family": "Wich"}]


# ---------- Medication status hardening (string | enum) ----------

def test_medication_to_dict_accepts_string_status():
    m = Medication(id=uuid.uuid4(), patient_id=uuid.uuid4(), code={"text": "A"}, status="ACTIVE")
    # Pre-flush state holds a raw string; to_dict must not crash on .value
    assert m.to_dict()["status"] == "ACTIVE"


def test_medication_to_dict_accepts_enum_status():
    m = Medication(id=uuid.uuid4(), patient_id=uuid.uuid4(), code={"text": "A"}, status=MedicationStatus.ACTIVE)
    assert m.to_dict()["status"] == "ACTIVE"


def test_medication_to_fhir_dict_lowercases_status_for_fhir():
    m = Medication(id=uuid.uuid4(), patient_id=uuid.uuid4(), code={"text": "A"}, status="ACTIVE")
    fhir = m.to_fhir_dict()
    assert fhir["status"] == "active"  # FHIR MedicationStatement.status is lowercase


# ---------- _enum_value helper ----------

def test_enum_value_passthrough_for_string():
    assert _enum_value("ACTIVE") == "ACTIVE"


def test_enum_value_extracts_value_for_enum():
    assert _enum_value(MedicationStatus.ACTIVE) == "ACTIVE"


def test_enum_value_default_for_none():
    assert _enum_value(None, default="active") == "active"


# ---------- Observation.category canonical-list shape (strict) ----------

def _obs_with_category(category):
    return Observation(
        id=uuid.uuid4(),
        status="final",
        code={"text": "HR"},
        subject={"reference": f"Patient/{_pid()}"},
        category=category,
    )


def test_observation_category_as_canonical_list_passes():
    category = [{
        "coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "laboratory",
        }]
    }]
    fhir = assert_valid_fhir(_obs_with_category(category))
    assert fhir["category"][0]["coding"][0]["code"] == "laboratory"


def test_observation_category_as_dict_is_rejected():
    # The legacy drift shape (single dict instead of a list). Strict mode: the
    # write-gate must reject it so non-canonical data can never persist.
    with pytest.raises(FhirSerializationError):
        assert_valid_fhir(_obs_with_category({
            "coding": [{"code": "laboratory"}]
        }))


# ---------- validate_and_filter_observations (batch skip-and-log gate) ----------

def test_validate_and_filter_observations_drops_invalid_keeps_valid():
    good = _obs_with_category([{"coding": [{"code": "laboratory"}]}])
    bad = _obs_with_category({"coding": [{"code": "laboratory"}]})  # dict, not list
    observations = [good, bad]
    valid, dropped = validate_and_filter_observations(observations)
    assert dropped == 1
    assert valid == [good]  # only the valid one is in the returned list


def test_validate_and_filter_observations_does_not_mutate_input():
    """I5: the input list is no longer mutated in place."""
    good = _obs_with_category([{"coding": [{"code": "laboratory"}]}])
    bad = _obs_with_category({"coding": [{"code": "laboratory"}]})
    observations = [good, bad]
    valid, dropped = validate_and_filter_observations(observations)
    assert dropped == 1
    assert valid == [good]
    # The original list is unchanged — callers holding a reference still see both
    assert observations == [good, bad]
    assert len(observations) == 2


def test_validate_and_filter_observations_all_valid_drops_none():
    a = _obs_with_category([{"coding": [{"code": "laboratory"}]}])
    b = Observation(
        id=uuid.uuid4(), status="final", code={"text": "X"},
        subject={"reference": f"Patient/{_pid()}"},
    )
    observations = [a, b]
    valid, dropped = validate_and_filter_observations(observations)
    assert dropped == 0
    assert valid == [a, b]


def test_validate_and_filter_observations_logs_when_logger_given():
    records = []

    class CaptureLogger:
        def warning(self, fmt, *args):
            records.append(fmt % args)

    bad = _obs_with_category({"coding": [{"code": "laboratory"}]})
    valid, dropped = validate_and_filter_observations([bad], logger=CaptureLogger())
    assert dropped == 1
    assert valid == []
    assert len(records) == 1
    assert "Skipping invalid Observation" in records[0]
