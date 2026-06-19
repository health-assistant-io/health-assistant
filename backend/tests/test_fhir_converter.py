import pytest
import uuid
from app.services import fhir_converter as fc
from app.models.enums import ExportScope


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
        "biomarker_slug": "glucose-fasting",
        "raw_value": 95.0,
        "normalized_value": 95.0,
        "relative_score": 0.55,
        "method": "enzymatic",
        "examination_id": None,
        "document_id": None,
    }


def _medication_orm(patient_id):
    return {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "tenant_id": str(uuid.uuid4()),
        "status": "ACTIVE",
        "code": {"text": "Aspirin", "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "1191"}]},
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


# ---------- scope + meta helpers ----------

def test_scope_to_smart():
    assert fc.scope_to_smart(ExportScope.PATIENT) == "patient/*.rs"
    assert fc.scope_to_smart(ExportScope.GROUP) == "system/*.rs"
    assert fc.scope_to_smart(ExportScope.SYSTEM) == "system/*.cruds"


def test_build_meta_defaults():
    meta = fc.build_meta(version_id="2")
    assert meta["versionId"] == "2"
    assert "lastUpdated" in meta
    assert meta["source"] == fc.PROVENANCE_SYSTEM
    assert meta["tag"][0]["code"] == "ha-export"


def test_build_meta_no_provenance():
    meta = fc.build_meta(provenance=False)
    assert "tag" not in meta


# ---------- Patient ----------

def test_patient_orm_to_fhir_strips_app_fields_and_lowercases_gender():
    f = fc.patient_to_fhir(_patient_orm())
    assert f["resourceType"] == "Patient"
    assert f["gender"] == "female"
    assert f["birthDate"] == "1990-05-12"
    assert f["identifier"] == [{"system": "urn:healthassistant:mrn", "value": "MRN-123"}]
    assert "tenant_id" not in f
    assert "user_id" not in f
    assert "age" not in f
    assert "dashboard_layout" not in f
    assert "emergency_contact" not in f
    assert f["meta"]["tag"][0]["code"] == "ha-export"


def test_patient_round_trip_through_fhir_resources():
    f = fc.patient_to_fhir(_patient_orm())
    ok, errs = fc.validate_resource(f)
    assert ok, errs


def test_patient_round_trip_to_orm():
    f = fc.patient_to_fhir(_patient_orm())
    orm = fc.fhir_to_patient_orm(f)
    assert orm["gender"] == "FEMALE"
    assert orm["birth_date"] == "1990-05-12"
    assert orm["mrn"] == "MRN-123"
    assert orm["name"] == [{"family": "Doe", "given": ["Jane"]}]


# ---------- Observation ----------

def test_observation_orm_to_fhir_maps_camel_case_and_keeps_subject():
    pid = str(uuid.uuid4())
    o = fc.observation_to_fhir(_observation_orm(pid))
    assert o["resourceType"] == "Observation"
    assert o["status"] == "final"
    assert o["effectiveDateTime"] == "2026-06-18T10:00:00+00:00"
    assert o["valueQuantity"] == {"value": 95, "unit": "mg/dL"}
    assert o["subject"] == {"reference": f"Patient/{pid}"}
    assert o["performer"] == [{"reference": "Organization/abc"}]
    assert o["method"] == {"text": "enzymatic"}
    assert "biomarker_id" not in o
    assert "normalized_value" not in o
    assert "tenant_id" not in o


def test_observation_validates_with_fhir_resources():
    o = fc.observation_to_fhir(_observation_orm(str(uuid.uuid4())))
    ok, errs = fc.validate_resource(o)
    assert ok, errs


def test_observation_round_trip_to_orm_extracts_patient_id():
    pid = str(uuid.uuid4())
    o = fc.observation_to_fhir(_observation_orm(pid))
    orm = fc.fhir_to_observation_orm(o)
    assert orm["patient_id"] == pid
    assert orm["status"] == "final"
    assert orm["effective_datetime"] == "2026-06-18T10:00:00+00:00"
    assert orm["value_quantity"] == {"value": 95, "unit": "mg/dL"}
    assert orm["method"] == "enzymatic"


# ---------- Medication ----------

def test_medication_orm_to_fhir_becomes_medication_statement():
    pid = str(uuid.uuid4())
    m = fc.medication_to_fhir(_medication_orm(pid))
    assert m["resourceType"] == "MedicationStatement"
    assert m["status"] == "active"
    assert m["medicationCodeableConcept"]["text"] == "Aspirin"
    assert m["subject"] == {"reference": f"Patient/{pid}"}
    assert m["effectivePeriod"]["start"] == "2026-01-01"
    assert m["dosage"][0]["text"] == "100 mg daily"
    assert m["dosage"][0]["timing"]["repeat"]["timeOfDay"] == ["08:00:00"]
    assert m["reasonCode"][0]["text"] == "Pain"
    assert m["note"][0]["text"] == "with food"
    assert "tenant_id" not in m
    assert "patient_id" not in m


def test_medication_validates():
    m = fc.medication_to_fhir(_medication_orm(str(uuid.uuid4())))
    ok, errs = fc.validate_resource(m)
    assert ok, errs


def test_medication_round_trip_to_orm():
    pid = str(uuid.uuid4())
    m = fc.medication_to_fhir(_medication_orm(pid))
    orm = fc.fhir_to_medication_orm(m)
    assert orm["patient_id"] == pid
    assert orm["status"] == "ACTIVE"
    assert orm["dosage"] == "100 mg daily"
    assert orm["reason"] == "Pain"
    assert orm["start_date"] == "2026-01-01"


# ---------- AllergyIntolerance ----------

def test_allergy_orm_to_fhir_maps_statuses_to_codings():
    pid = str(uuid.uuid4())
    a = fc.allergy_to_fhir(_allergy_orm(pid))
    assert a["resourceType"] == "AllergyIntolerance"
    assert a["clinicalStatus"]["coding"][0]["code"] == "active"
    assert a["verificationStatus"]["coding"][0]["code"] == "confirmed"
    assert a["category"] == ["food"]
    assert a["criticality"] == "high"
    assert a["patient"] == {"reference": f"Patient/{pid}"}
    assert a["reaction"][0]["manifestation"][0]["text"] == "Hives"
    assert a["reaction"][0]["severity"] == "mild"
    assert a["reaction"][0]["onset"] == "2026-05-01"
    assert "tenant_id" not in a


def test_allergy_validates():
    a = fc.allergy_to_fhir(_allergy_orm(str(uuid.uuid4())))
    ok, errs = fc.validate_resource(a)
    assert ok, errs


def test_allergy_round_trip_to_orm():
    pid = str(uuid.uuid4())
    a = fc.allergy_to_fhir(_allergy_orm(pid))
    orm = fc.fhir_to_allergy_orm(a)
    assert orm["patient_id"] == pid
    assert orm["clinical_status"] == "ACTIVE"
    assert orm["verification_status"] == "confirmed"
    assert orm["category"] == "FOOD"
    assert orm["criticality"] == "HIGH"
    assert orm["reactions"][0]["manifestation"] == "Hives"
    assert orm["reactions"][0]["severity"] == "MILD"


# ---------- Organization + Practitioner ----------

def test_organization_orm_to_fhir_maps_part_of():
    org = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "active": True,
        "type": {"coding": [{"code": "team"}]},
        "org_type": "HOUSEHOLD",
        "name": "Doe Household",
        "alias": None,
        "telecom": None,
        "address": None,
        "part_of_id": str(uuid.uuid4()),
        "contact": None,
    }
    o = fc.organization_to_fhir(org)
    assert o["resourceType"] == "Organization"
    assert o["active"] is True
    assert o["partOf"]["reference"].startswith("Organization/")
    assert "org_type" not in o
    assert "tenant_id" not in o
    ok, errs = fc.validate_resource(o)
    assert ok, errs


def test_practitioner_orm_to_fhir_splits_name_and_builds_qualification():
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "user_id": None,
        "name": "Alice Smith",
        "specialty": "Cardiology",
        "license_number": "LIC-9",
        "email": "alice@example.org",
        "phone": "+30 555",
        "telecom": None,
        "address": None,
        "office_number": "12",
        "office_details": None,
    }
    p = fc.practitioner_to_fhir(doc)
    assert p["resourceType"] == "Practitioner"
    assert p["name"][0]["given"] == ["Alice"]
    assert p["name"][0]["family"] == "Smith"
    assert p["qualification"][0]["code"]["text"] == "Cardiology"
    assert p["qualification"][0]["identifier"][0]["value"] == "LIC-9"
    assert any(t["system"] == "email" for t in p["telecom"])
    assert "tenant_id" not in p
    assert "office_number" not in p
    ok, errs = fc.validate_resource(p)
    assert ok, errs


def test_practitioner_round_trip_to_orm():
    doc = {
        "id": str(uuid.uuid4()),
        "name": "Alice Smith",
        "specialty": "Cardiology",
        "license_number": "LIC-9",
        "email": "alice@example.org",
        "phone": "+30 555",
        "telecom": None,
        "address": None,
    }
    p = fc.practitioner_to_fhir(doc)
    orm = fc.fhir_to_practitioner_orm(p)
    assert orm["name"] == "Alice Smith"
    assert orm["specialty"] == "Cardiology"
    assert orm["license_number"] == "LIC-9"
    assert orm["email"] == "alice@example.org"
    assert orm["phone"] == "+30 555"


# ---------- Bundle + validation ----------

def test_build_bundle_structure():
    pid = str(uuid.uuid4())
    p = fc.patient_to_fhir(_patient_orm())
    o = fc.observation_to_fhir(_observation_orm(pid))
    bundle = fc.build_bundle(
        [
            (f"urn:uuid:{p['id']}", p, "POST"),
            (f"urn:uuid:{o['id']}", o, "POST"),
        ]
    )
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "transaction"
    assert len(bundle["entry"]) == 2
    assert bundle["entry"][0]["request"] == {"method": "POST", "url": "Patient"}
    assert bundle["entry"][1]["request"]["url"] == "Observation"
    assert bundle["identifier"]["system"] == fc.PROVENANCE_SYSTEM


def test_validate_bundle_accepts_valid_bundle():
    pid = str(uuid.uuid4())
    p = fc.patient_to_fhir(_patient_orm())
    o = fc.observation_to_fhir(_observation_orm(pid))
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


def test_orm_to_fhir_dispatch_unsupported():
    with pytest.raises(ValueError):
        fc.orm_to_fhir("NotAType", {})


def test_fhir_to_orm_dispatch_unsupported():
    with pytest.raises(ValueError):
        fc.fhir_to_orm("NotAType", {})


def test_extract_patient_id():
    assert fc._extract_patient_id({"reference": "Patient/abc"}) == "abc"
    assert fc._extract_patient_id(None) is None
    assert fc._extract_patient_id({"reference": "Organization/x"}) == "x"
