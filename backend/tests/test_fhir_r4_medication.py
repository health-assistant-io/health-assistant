"""Tests for Medication intent discriminator (audit C11, C12).

Covers:
- MedicationCatalog.to_fhir_dict() emits valid FHIR R4B Medication
- Medication.to_fhir_dict() emits MedicationStatement when intent=statement
- Medication.to_fhir_dict() emits MedicationRequest when intent in {order, plan, proposal}
- fhir_to_medication_orm() preserves intent=statement
- fhir_to_medication_request_orm() handles intent routing
- Status mapping for both resources
"""
import datetime as _dt
from uuid import uuid4

import pytest

from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.enums import MedicationIntent, MedicationStatus
from app.services.fhir_converter import (
    fhir_to_medication_orm,
    fhir_to_medication_request_orm,
    validate_resource,
)
from app.services.fhir_helpers import parse_fhir_resource


def _make_med(**overrides) -> Medication:
    defaults = dict(
        id=str(uuid4()),
        patient_id=str(uuid4()),
        tenant_id=str(uuid4()),
        status=MedicationStatus.ACTIVE,
        intent=MedicationIntent.STATEMENT,
        code={"text": "Aspirin"},
        start_date=None,
        end_date=None,
        dosage=None,
        frequency=None,
        reason=None,
        note=None,
        created_at=_dt.datetime(2024, 3, 1, tzinfo=_dt.timezone.utc),
    )
    defaults.update(overrides)
    return Medication(**defaults)


# ---------------------------------------------------------------------------
# MedicationStatement projection (intent=statement, default)
# ---------------------------------------------------------------------------

def test_med_statement_minimal_to_fhir_dict():
    med = _make_med()
    fhir = med.to_fhir_dict()
    assert fhir["resourceType"] == "MedicationStatement"


def test_med_statement_validates():
    med = _make_med()
    fhir = med.to_fhir_dict()
    parsed = parse_fhir_resource("MedicationStatement", fhir)
    assert parsed.__resource_type__ == "MedicationStatement"


def test_med_statement_subject():
    pid = str(uuid4())
    med = _make_med(patient_id=pid)
    fhir = med.to_fhir_dict()
    assert fhir["subject"]["reference"] == f"Patient/{pid}"


def test_med_statement_with_dosage():
    med = _make_med(dosage="81 mg daily", frequency={"repeat": {"frequency": 1, "period": 1, "periodUnit": "d"}})
    fhir = med.to_fhir_dict()
    assert fhir["dosage"][0]["text"] == "81 mg daily"
    assert "timing" in fhir["dosage"][0]


def test_med_statement_status_lowercase():
    med = _make_med(status=MedicationStatus.COMPLETED)
    fhir = med.to_fhir_dict()
    assert fhir["status"] == "completed"


# ---------------------------------------------------------------------------
# MedicationRequest projection (intent in {order, plan, proposal})
# ---------------------------------------------------------------------------

def test_med_request_order_emits_medication_request():
    med = _make_med(intent=MedicationIntent.ORDER)
    fhir = med.to_fhir_dict()
    assert fhir["resourceType"] == "MedicationRequest"
    assert fhir["intent"] == "order"


def test_med_request_plan_emits_medication_request():
    med = _make_med(intent=MedicationIntent.PLAN)
    fhir = med.to_fhir_dict()
    assert fhir["resourceType"] == "MedicationRequest"
    assert fhir["intent"] == "plan"


def test_med_request_proposal_emits_medication_request():
    med = _make_med(intent=MedicationIntent.PROPOSAL)
    fhir = med.to_fhir_dict()
    assert fhir["intent"] == "proposal"


def test_med_request_validates():
    med = _make_med(intent=MedicationIntent.ORDER)
    fhir = med.to_fhir_dict()
    parsed = parse_fhir_resource("MedicationRequest", fhir)
    assert parsed.__resource_type__ == "MedicationRequest"


def test_med_request_status_mapping():
    cases = [
        (MedicationStatus.ACTIVE, "active"),
        (MedicationStatus.COMPLETED, "completed"),
        (MedicationStatus.CANCELLED, "cancelled"),
        (MedicationStatus.STOPPED, "stopped"),
        (MedicationStatus.ON_HOLD, "on-hold"),
        (MedicationStatus.ENTERED_IN_ERROR, "entered-in-error"),
        (MedicationStatus.UNKNOWN, "unknown"),
        (MedicationStatus.INTENDED, "draft"),
    ]
    for app_status, mr_status in cases:
        med = _make_med(intent=MedicationIntent.ORDER, status=app_status)
        fhir = med.to_fhir_dict()
        assert fhir["status"] == mr_status, f"{app_status} should map to {mr_status}"


def test_med_request_dosage_instruction():
    med = _make_med(
        intent=MedicationIntent.ORDER,
        dosage="500mg TID",
        frequency={"repeat": {"frequency": 3, "period": 1, "periodUnit": "d"}},
    )
    fhir = med.to_fhir_dict()
    assert fhir["dosageInstruction"][0]["text"] == "500mg TID"


def test_med_request_encounter_reference():
    eid = str(uuid4())
    med = _make_med(
        intent=MedicationIntent.ORDER,
        examination_id=eid,
    )
    fhir = med.to_fhir_dict()
    assert fhir["encounter"]["reference"] == f"Encounter/{eid}"


def test_med_request_authored_on_from_created_at():
    med = _make_med(
        intent=MedicationIntent.ORDER,
        created_at=_dt.datetime(2024, 6, 1, 12, 0, tzinfo=_dt.timezone.utc),
    )
    fhir = med.to_fhir_dict()
    assert fhir["authoredOn"].startswith("2024-06-01")


# ---------------------------------------------------------------------------
# MedicationCatalog → Medication (standalone drug definition)
# ---------------------------------------------------------------------------

def test_medication_catalog_to_fhir_dict():
    catalog = MedicationCatalog(id=str(uuid4()), name="Ibuprofen")
    fhir = catalog.to_fhir_dict()
    assert fhir["resourceType"] == "Medication"
    assert fhir["code"]["text"] == "Ibuprofen"


def test_medication_catalog_validates():
    catalog = MedicationCatalog(id=str(uuid4()), name="Test")
    fhir = catalog.to_fhir_dict()
    parsed = parse_fhir_resource("Medication", fhir)
    assert parsed.__resource_type__ == "Medication"


# ---------------------------------------------------------------------------
# Reverse converters
# ---------------------------------------------------------------------------

def _canonical_statement(**overrides) -> dict:
    base = {
        "resourceType": "MedicationStatement",
        "id": str(uuid4()),
        "status": "active",
        "medicationCodeableConcept": {"text": "Aspirin"},
        "subject": {"reference": "Patient/p1"},
    }
    base.update(overrides)
    return base


def _canonical_request(**overrides) -> dict:
    base = {
        "resourceType": "MedicationRequest",
        "id": str(uuid4()),
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"text": "Penicillin"},
        "subject": {"reference": "Patient/p1"},
    }
    base.update(overrides)
    return base


def test_fhir_to_medication_orm_tags_intent_statement():
    fhir = _canonical_statement()
    orm = fhir_to_medication_orm(fhir)
    assert orm["intent"] == "statement"


def test_fhir_to_medication_request_orm_basic():
    fhir = _canonical_request()
    orm = fhir_to_medication_request_orm(fhir)

    assert orm["intent"] == MedicationIntent.ORDER
    assert orm["status"] == "ACTIVE"
    assert orm["patient_id"] == "p1"
    assert orm["code"] == {"text": "Penicillin"}


def test_fhir_to_medication_request_orm_intent_fallback():
    """Unknown intent values fall back to ORDER."""
    fhir = _canonical_request(intent="garbage")
    orm = fhir_to_medication_request_orm(fhir)
    assert orm["intent"] == MedicationIntent.ORDER


def test_fhir_to_medication_request_orm_status_mapping():
    cases = [
        ("active", "ACTIVE"),
        ("completed", "COMPLETED"),
        ("cancelled", "CANCELLED"),
        ("entered-in-error", "ENTERED_IN_ERROR"),
        ("stopped", "STOPPED"),
        ("on-hold", "ON_HOLD"),
        ("draft", "INTENDED"),
        ("unknown", "UNKNOWN"),
    ]
    for mr_status, app_status in cases:
        fhir = _canonical_request(status=mr_status)
        orm = fhir_to_medication_request_orm(fhir)
        assert orm["status"] == app_status, f"{mr_status} should map to {app_status}"


def test_fhir_to_medication_request_orm_dosage_instruction():
    fhir = _canonical_request(dosageInstruction=[{"text": "500mg BID"}])
    orm = fhir_to_medication_request_orm(fhir)
    assert orm["dosage"] == "500mg BID"


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_round_trip_request():
    """ORM (intent=order) → FHIR → ORM preserves key fields."""
    pid = str(uuid4())
    med = _make_med(
        intent=MedicationIntent.ORDER,
        patient_id=pid,
        dosage="100mg QD",
        reason="Hypertension",
        code={"text": "Lisinopril"},
    )
    fhir = med.to_fhir_dict()
    orm = fhir_to_medication_request_orm(fhir)

    assert orm["intent"] == MedicationIntent.ORDER
    assert orm["patient_id"] == pid
    assert orm["dosage"] == "100mg QD"
    assert orm["reason"] == "Hypertension"
    assert orm["code"]["text"] == "Lisinopril"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_canonical_statement_validates():
    fhir = _canonical_statement()
    ok, errs = validate_resource(fhir)
    assert ok, f"Expected valid MedicationStatement: {errs}"


def test_canonical_request_validates():
    fhir = _canonical_request()
    ok, errs = validate_resource(fhir)
    assert ok, f"Expected valid MedicationRequest: {errs}"
