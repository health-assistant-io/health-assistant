import pytest
import uuid
from app.services import fhir_converter as fc
from app.services import fhir_helpers as fh
from app.models.enums import ExportScope


# ---------- ORM-shape dict fixtures (for reverse-direction reference) ----------

def _patient_orm():
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "user_id": None,
        "name": [{"family": "Doe", "given": ["Jane"]}],
        "gender": "FEMALE",
        "birth_date": "1990-05-12",
        "deceased_boolean": None,
        "deceased_datetime": None,
        "address": [{"line": ["1 Main St"], "city": "Athens"}],
        "telecom": [{"system": "phone", "value": "+30 555"}],
        "mrn": "MRN-123",
        "emergency_contact": None,
        "dashboard_layout": None,
    }


def _observation_orm(patient_id):
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "status": "FINAL",
        "category": [{"coding": [{"code": "laboratory"}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": "2345-7"}], "text": "Glucose"},
        "subject": {"reference": f"Patient/{patient_id}"},
        "effective_datetime": "2026-06-18T10:00:00+00:00",
        "value_quantity": {"value": 95, "unit": "mg/dL"},
        "value_string": None,
        "value_codeable_concept": None,
        "reference_range": [{"low": {"value": 70}, "high": {"value": 99}}],
        "interpretation": [{"coding": [{"code": "N"}]}],
        "comment": "fasting",
        "performer": [{"reference": "Organization/abc"}],
        "biomarker_id": str(uuid.uuid4()),
        "raw_value": 95.0,
        "normalized_value": 95.0,
        "relative_score": 0.55,
        "method": "enzymatic",
    }


def _medication_orm(patient_id):
    return {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "tenant_id": str(uuid.uuid4()),
        "status": "ACTIVE",
        "code": {"text": "Aspirin", "coding": [{"code": "1191"}]},
        "start_date": "2026-01-01",
        "end_date": None,
        "dosage": "100 mg daily",
        "frequency": {"repeat": {"timeOfDay": ["08:00"]}},
        "reason": "Pain",
        "note": "with food",
    }


def _allergy_orm(patient_id):
    return {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "tenant_id": str(uuid.uuid4()),
        "clinical_status": "ACTIVE",
        "verification_status": "confirmed",
        "category": "FOOD",
        "criticality": "HIGH",
        "code": {"text": "Peanuts"},
        "onset_date": "2020-01-01T00:00:00+00:00",
        "resolved_date": None,
        "last_occurrence": "2026-05-01T00:00:00+00:00",
        "note": "severe",
        "reactions": [{"manifestation": "Hives", "severity": "MILD", "date": "2026-05-01"}],
    }


# ---------- ORM model instance builders ----------

def _patient_model():
    import datetime
    from app.models.fhir.patient import Patient
    from app.models.enums import Gender
    return Patient(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name=[{"family": "Doe", "given": ["Jane"]}],
        gender=Gender.FEMALE,
        birth_date=datetime.date(1990, 5, 12),
        address=[{"line": ["1 Main St"], "city": "Athens"}],
        telecom=[{"system": "phone", "value": "+30 555"}],
        mrn="MRN-123",
    )


def _observation_model():
    import datetime
    from app.models.fhir.patient import Observation
    o = Observation(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        status="FINAL",
        category=[{"coding": [{"code": "laboratory"}]}],
        code={"coding": [{"system": "http://loinc.org", "code": "2345-7"}], "text": "Glucose"},
        subject={"reference": "Patient/abc"},
        effective_datetime=datetime.datetime(2026, 6, 18, 10, 0, tzinfo=datetime.timezone.utc),
        value_quantity={"value": 95, "unit": "mg/dL"},
        reference_range=[{"low": {"value": 70}, "high": {"value": 99}}],
        interpretation="High",
        comment="fasting",
        performer=[{"reference": "Organization/abc"}],
        method="enzymatic",
    )
    # Avoid lazy-load of relationships on a transient instance
    o.biomarker = None
    o.lab = None
    o.raw_unit = None
    return o


def _medication_model():
    import datetime
    from app.models.fhir.medication import Medication
    from app.models.enums import MedicationStatus
    return Medication(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        status=MedicationStatus.ACTIVE,
        code={"text": "Aspirin", "coding": [{"code": "1191"}]},
        start_date=datetime.date(2026, 1, 1),
        dosage="100 mg daily",
        frequency={"repeat": {"timeOfDay": ["08:00"]}},
        reason="Pain",
        note="with food",
    )


def _allergy_model():
    import datetime
    from app.models.fhir.allergy import AllergyIntolerance
    from app.models.enums import AllergyCategory, AllergyCriticality, AllergyClinicalStatus
    return AllergyIntolerance(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        clinical_status=AllergyClinicalStatus.ACTIVE,
        verification_status="confirmed",
        category=AllergyCategory.FOOD,
        criticality=AllergyCriticality.HIGH,
        code={"text": "Peanuts"},
        onset_date=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        last_occurrence=datetime.datetime(2026, 5, 1, tzinfo=datetime.timezone.utc),
        note="severe",
        reactions=[{"manifestation": "Hives", "severity": "MILD", "date": "2026-05-01"}],
    )


# ---------- scope + meta helpers ----------

def test_scope_to_smart():
    assert fc.scope_to_smart(ExportScope.PATIENT) == "patient/*.rs"
    assert fc.scope_to_smart(ExportScope.GROUP) == "system/*.rs"
    assert fc.scope_to_smart(ExportScope.SYSTEM) == "system/*.cruds"


def test_build_meta_defaults():
    meta = fh.build_meta(version_id="2")
    assert meta["versionId"] == "2"
    assert "lastUpdated" in meta
    assert meta["source"] == fh.PROVENANCE_SYSTEM
    assert meta["tag"][0]["code"] == "ha-export"


def test_build_meta_no_provenance():
    meta = fh.build_meta(provenance=False)
    assert "tag" not in meta


# ---------- to_fhir_dict() construction via fhir.resources ----------

def test_patient_to_fhir_dict_valid_and_strips_app_fields():
    f = _patient_model().to_fhir_dict()
    assert f["resourceType"] == "Patient"
    assert f["gender"] == "female"
    assert f["birthDate"] == "1990-05-12"
    assert f["identifier"] == [{"system": "urn:healthassistant:mrn", "value": "MRN-123"}]
    assert "tenant_id" not in f and "user_id" not in f and "age" not in f
    assert f["meta"]["tag"][0]["code"] == "ha-export"
    ok, errs = fc.validate_resource(f)
    assert ok, errs


def test_observation_to_fhir_dict_valid_and_strips_app_fields():
    o = _observation_model().to_fhir_dict()
    assert o["resourceType"] == "Observation"
    assert o["status"] == "final"
    assert o["effectiveDateTime"] == "2026-06-18T10:00:00Z"  # fhir.resources canonicalizes +00:00 → Z
    assert o["valueQuantity"] == {"value": 95, "unit": "mg/dL"}
    assert o["method"] == {"text": "enzymatic"}
    assert o["interpretation"] == [{"text": "High"}]
    assert "biomarker_id" not in o and "normalized_value" not in o and "tenant_id" not in o
    ok, errs = fc.validate_resource(o)
    assert ok, errs


def test_medication_to_fhir_dict_valid_and_strips_app_fields():
    m = _medication_model().to_fhir_dict()
    assert m["resourceType"] == "MedicationStatement"
    assert m["status"] == "active"
    assert m["medicationCodeableConcept"]["text"] == "Aspirin"
    assert m["effectivePeriod"]["start"] == "2026-01-01"
    assert m["dosage"][0]["text"] == "100 mg daily"
    assert m["dosage"][0]["timing"]["repeat"]["timeOfDay"] == ["08:00:00"]
    assert m["reasonCode"][0]["text"] == "Pain"
    assert "tenant_id" not in m and "patient_id" not in m
    ok, errs = fc.validate_resource(m)
    assert ok, errs


def test_allergy_to_fhir_dict_valid_and_strips_app_fields():
    a = _allergy_model().to_fhir_dict()
    assert a["resourceType"] == "AllergyIntolerance"
    assert a["clinicalStatus"]["coding"][0]["code"] == "active"
    assert a["verificationStatus"]["coding"][0]["code"] == "confirmed"
    assert a["category"] == ["food"]
    assert a["criticality"] == "high"
    assert a["reaction"][0]["manifestation"][0]["text"] == "Hives"
    assert a["reaction"][0]["severity"] == "mild"
    assert "tenant_id" not in a
    ok, errs = fc.validate_resource(a)
    assert ok, errs


def test_to_fhir_dict_raises_on_invalid_resource():
    """build_fhir_resource (the engine behind to_fhir_dict) must raise
    FhirSerializationError for FHIR-invalid data."""
    # invalid: gender must be a FHIR `code` (string), not an int
    with pytest.raises(fh.FhirSerializationError):
        fh.build_fhir_resource("Patient", {"resourceType": "Patient", "gender": 12345})


# ---------- orm_to_fhir dispatcher ----------

def test_orm_to_fhir_routes_orm_object_to_to_fhir_dict():
    p = _patient_model()
    out = fc.orm_to_fhir("Patient", p)
    assert out["resourceType"] == "Patient"
    assert out["birthDate"] == "1990-05-12"


def test_orm_to_fhir_rejects_plain_dict():
    """Plain dicts are no longer supported (standalone *_to_fhir retired)."""
    with pytest.raises(TypeError):
        fc.orm_to_fhir("Patient", _patient_orm())


# ---------- fhir_to_*_orm reverse (canonical FHIR in) ----------

def test_fhir_to_orm_dispatch_unsupported():
    with pytest.raises(ValueError):
        fc.fhir_to_orm("NotAType", {})


def test_extract_patient_id():
    assert fh._extract_patient_id({"reference": "Patient/abc"}) == "abc"
    assert fh._extract_patient_id(None) is None
    assert fh._extract_patient_id({"reference": "Organization/x"}) == "x"


def test_flatten_interpretation_helper():
    assert fh._flatten_interpretation([{"coding": [{"display": "High"}]}]) == "High"
    assert fh._flatten_interpretation([{"coding": [{"code": "H"}]}]) == "H"
    assert fh._flatten_interpretation([{"text": "Borderline"}]) == "Borderline"
    assert fh._flatten_interpretation("Normal") == "Normal"
    assert fh._flatten_interpretation(None) is None
    assert fh._flatten_interpretation([]) is None


def test_fhir_to_observation_orm_flattens_interpretation_list():
    out = fc.fhir_to_observation_orm({"interpretation": [{"coding": [{"display": "High"}]}]})
    assert out["interpretation"] == "High"


def test_fhir_to_observation_orm_interpretation_string_passthrough():
    out = fc.fhir_to_observation_orm({"interpretation": "Normal"})
    assert out["interpretation"] == "Normal"


def test_fhir_to_observation_orm_interpretation_empty_is_cleaned():
    out = fc.fhir_to_observation_orm({"interpretation": []})
    assert "interpretation" not in out


def test_fhir_to_diagnostic_report_orm_reads_effective_datetime():
    # Canonical FHIR (camelCase) — the only shape fhir_to_*_orm accepts now.
    out = fc.fhir_to_diagnostic_report_orm(
        {"resourceType": "DiagnosticReport", "effectiveDateTime": "2026-01-01T00:00:00Z"}
    )
    assert out["effective_datetime"] == "2026-01-01T00:00:00Z"


def test_fhir_to_medication_orm_reads_effective_period():
    out = fc.fhir_to_medication_orm(
        {
            "resourceType": "MedicationStatement",
            "effectivePeriod": {"start": "2026-02-01", "end": "2026-07-01"},
        }
    )
    assert out["start_date"] == "2026-02-01"
    assert out["end_date"] == "2026-07-01"


def test_fhir_to_medication_orm_reads_dosage_and_code():
    out = fc.fhir_to_medication_orm(
        {
            "resourceType": "MedicationStatement",
            "medicationCodeableConcept": {"text": "Metformin"},
            "dosage": [{"text": "1 tablet", "timing": {"repeat": {"count": 1}}}],
        }
    )
    assert out["code"] == {"text": "Metformin"}
    assert out["dosage"] == "1 tablet"
    assert out["frequency"] == {"repeat": {"count": 1}}


def test_fhir_to_orm_validates_canonical_fhir():
    """The dispatcher validates input via fhir.resources; non-canonical input
    (snake_case keys, app fields) is rejected with FhirSerializationError."""
    # snake_case keys are not canonical FHIR → rejected
    with pytest.raises(fh.FhirSerializationError):
        fc.fhir_to_orm("Patient", {"resourceType": "Patient", "birth_date": "1990-01-01"})
    # extra (app) fields are rejected (fhir.resources extra='forbid')
    with pytest.raises(fh.FhirSerializationError):
        fc.fhir_to_orm(
            "Patient",
            {"resourceType": "Patient", "gender": "male", "tenant_id": "x"},
        )


def test_fhir_to_orm_round_trips_canonical_patient():
    # A canonical Patient produced by to_fhir_dict() parses back cleanly.
    f = _patient_model().to_fhir_dict()
    orm = fc.fhir_to_orm("Patient", f)
    assert orm["gender"] == "FEMALE"
    assert orm["birth_date"] == "1990-05-12"
    assert orm["mrn"] == "MRN-123"


# ---------- round-trip: to_fhir_dict() -> fhir_to_*_orm ----------

def test_patient_round_trip_to_orm():
    f = _patient_model().to_fhir_dict()
    orm = fc.fhir_to_patient_orm(f)
    assert orm["gender"] == "FEMALE"
    assert orm["birth_date"] == "1990-05-12"
    assert orm["mrn"] == "MRN-123"


def test_observation_round_trip_to_orm_extracts_patient_id():
    import datetime
    from app.models.fhir.patient import Observation
    pid = str(uuid.uuid4())
    o = Observation(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        status="FINAL",
        code={"text": "Glucose"},
        subject={"reference": f"Patient/{pid}"},
        effective_datetime=datetime.datetime(2026, 6, 18, 10, 0, tzinfo=datetime.timezone.utc),
        value_quantity={"value": 95, "unit": "mg/dL"},
        method="enzymatic",
    )
    orm = fc.fhir_to_observation_orm(o.to_fhir_dict())
    assert orm["patient_id"] == pid
    assert orm["status"] == "final"
    assert orm["method"] == "enzymatic"


def test_medication_round_trip_to_orm():
    m = _medication_model().to_fhir_dict()
    orm = fc.fhir_to_medication_orm(m)
    assert orm["status"] == "ACTIVE"
    assert orm["dosage"] == "100 mg daily"
    assert orm["reason"] == "Pain"
    assert orm["start_date"] == "2026-01-01"


def test_allergy_round_trip_to_orm():
    a = _allergy_model().to_fhir_dict()
    orm = fc.fhir_to_allergy_orm(a)
    assert orm["clinical_status"] == "ACTIVE"
    assert orm["verification_status"] == "confirmed"
    assert orm["category"] == "FOOD"
    assert orm["criticality"] == "HIGH"
    assert orm["reactions"][0]["manifestation"] == "Hives"


# ---------- Bundle + validation ----------

def test_build_bundle_structure():
    p = _patient_model().to_fhir_dict()
    o = _observation_model().to_fhir_dict()
    bundle = fc.build_bundle(
        [(f"urn:uuid:{p['id']}", p, "POST"), (f"urn:uuid:{o['id']}", o, "POST")]
    )
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "transaction"
    assert len(bundle["entry"]) == 2
    assert bundle["entry"][0]["request"] == {"method": "POST", "url": "Patient"}
    assert bundle["entry"][1]["request"]["url"] == "Observation"
    assert bundle["identifier"]["system"] == fc.PROVENANCE_SYSTEM


def test_validate_bundle_accepts_valid_bundle():
    p = _patient_model().to_fhir_dict()
    o = _observation_model().to_fhir_dict()
    bundle = fc.build_bundle(
        [(f"urn:uuid:{p['id']}", p, "POST"), (f"urn:uuid:{o['id']}", o, "POST")]
    )
    ok, errs = fc.validate_bundle(bundle)
    assert ok, errs


def test_validate_bundle_rejects_invalid_resource():
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"fullUrl": "urn:uuid:1", "resource": {"resourceType": "Observation"}, "request": {"method": "POST", "url": "Observation"}}
        ],
    }
    ok, errs = fc.validate_bundle(bundle)
    assert not ok
    assert errs


def test_validate_resource_missing_type():
    ok, errs = fc.validate_resource({"foo": "bar"})
    assert not ok
    assert "resourceType" in errs[0]
