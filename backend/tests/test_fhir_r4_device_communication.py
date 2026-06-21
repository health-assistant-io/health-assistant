"""Tests for Device + Communication models (audit C9 + C15).

Covers:
- DeviceModel.to_fhir_dict() emits valid FHIR R4 Device
- CommunicationModel.to_fhir_dict() emits valid FHIR R4 Communication
- fhir_to_device_orm() + fhir_to_communication_orm() reverse converters
- Soft-delete mixin applied (Device, Communication)
- Round-trip integrity
"""
import datetime as _dt
from uuid import uuid4

import pytest

from app.models.fhir.device import DeviceModel
from app.models.fhir.communication import CommunicationModel
from app.services.fhir_converter import (
    fhir_to_communication_orm,
    fhir_to_device_orm,
    validate_resource,
)
from app.services.fhir_helpers import parse_fhir_resource


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

def _make_device(**overrides) -> DeviceModel:
    defaults = dict(
        id=str(uuid4()),
        tenant_id=str(uuid4()),
        type={"text": "Wearable"},
        status="active",
        created_at=_dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc),
    )
    defaults.update(overrides)
    return DeviceModel(**defaults)


def test_device_minimal_to_fhir_dict():
    dev = _make_device()
    fhir = dev.to_fhir_dict()
    assert fhir["resourceType"] == "Device"
    assert fhir["status"] == "active"


def test_device_validates():
    dev = _make_device()
    fhir = dev.to_fhir_dict()
    parsed = parse_fhir_resource("Device", fhir)
    assert parsed.__resource_type__ == "Device"


def test_device_patient_reference():
    pid = str(uuid4())
    dev = _make_device(patient_id=pid)
    fhir = dev.to_fhir_dict()
    assert fhir["patient"]["reference"] == f"Patient/{pid}"


def test_device_owner_reference():
    iid = str(uuid4())
    dev = _make_device(owner_integration_id=iid)
    fhir = dev.to_fhir_dict()
    assert fhir["owner"]["reference"] == f"Integration/{iid}"


def test_device_serial_number_single_string():
    """Device.serialNumber is a single string in R4B (0..1), not a list."""
    dev = _make_device(serial_number="SN-123")
    fhir = dev.to_fhir_dict()
    assert fhir["serialNumber"] == "SN-123"


def test_device_has_soft_delete_mixin():
    """DeviceModel should be soft-deletable (deleted_at column)."""
    assert hasattr(DeviceModel, "deleted_at")


def _canonical_device(**overrides) -> dict:
    base = {
        "resourceType": "Device",
        "id": str(uuid4()),
        "status": "active",
        "patient": {"reference": "Patient/p1"},
        "owner": {"reference": "Integration/i1"},
        "manufacturer": "Apple",
        "modelNumber": "Watch-9",
        "serialNumber": "X123",  # single string in R4B
    }
    base.update(overrides)
    return base


def test_fhir_to_device_orm_basic():
    fhir = _canonical_device()
    orm = fhir_to_device_orm(fhir)
    assert orm["patient_id"] == "p1"
    assert orm["owner_integration_id"] == "i1"
    assert orm["serial_number"] == "X123"
    assert orm["manufacturer"] == "Apple"


def test_canonical_device_validates():
    fhir = _canonical_device()
    ok, errs = validate_resource(fhir)
    assert ok, f"Expected valid Device: {errs}"


def test_device_round_trip():
    dev = _make_device(
        type={"text": "Smartwatch"},
        manufacturer="Garmin",
        model_number="Venu 2",
        serial_number="SN-456",
    )
    fhir = dev.to_fhir_dict()
    orm = fhir_to_device_orm(fhir)

    assert orm["manufacturer"] == "Garmin"
    assert orm["model_number"] == "Venu 2"
    assert orm["serial_number"] == "SN-456"


# ---------------------------------------------------------------------------
# Communication
# ---------------------------------------------------------------------------

def _make_comm(**overrides) -> CommunicationModel:
    defaults = dict(
        id=str(uuid4()),
        tenant_id=str(uuid4()),
        status="completed",
        payload=[{"contentString": "Hello"}],
        sent=_dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc),
        created_at=_dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc),
        updated_at=_dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc),
    )
    defaults.update(overrides)
    return CommunicationModel(**defaults)


def test_communication_minimal_to_fhir_dict():
    comm = _make_comm()
    fhir = comm.to_fhir_dict()
    assert fhir["resourceType"] == "Communication"
    assert fhir["status"] == "completed"


def test_communication_validates():
    comm = _make_comm()
    fhir = comm.to_fhir_dict()
    parsed = parse_fhir_resource("Communication", fhir)
    assert parsed.__resource_type__ == "Communication"


def test_communication_subject_reference():
    pid = str(uuid4())
    comm = _make_comm(subject_patient_id=pid)
    fhir = comm.to_fhir_dict()
    assert fhir["subject"]["reference"] == f"Patient/{pid}"


def test_communication_encounter_reference():
    eid = str(uuid4())
    comm = _make_comm(encounter_id=eid)
    fhir = comm.to_fhir_dict()
    assert fhir["encounter"]["reference"] == f"Encounter/{eid}"


def test_communication_sent_iso_format():
    comm = _make_comm(sent=_dt.datetime(2024, 6, 1, 12, 0, tzinfo=_dt.timezone.utc))
    fhir = comm.to_fhir_dict()
    assert fhir["sent"].startswith("2024-06-01")
    assert fhir["sent"].endswith("Z")


def test_communication_has_soft_delete_mixin():
    assert hasattr(CommunicationModel, "deleted_at")


def _canonical_communication(**overrides) -> dict:
    base = {
        "resourceType": "Communication",
        "id": str(uuid4()),
        "status": "completed",
        "subject": {"reference": "Patient/p1"},
        "sent": "2024-01-01T00:00:00Z",
        "payload": [{"contentString": "Message text"}],
    }
    base.update(overrides)
    return base


def test_fhir_to_communication_orm_basic():
    fhir = _canonical_communication()
    orm = fhir_to_communication_orm(fhir)
    assert orm["status"] == "completed"
    assert orm["subject_patient_id"] == "p1"
    assert orm["sent"].year == 2024


def test_canonical_communication_validates():
    fhir = _canonical_communication()
    ok, errs = validate_resource(fhir)
    assert ok, f"Expected valid Communication: {errs}"


def test_communication_round_trip():
    pid = str(uuid4())
    comm = _make_comm(
        subject_patient_id=pid,
        status="in-progress",
        priority="urgent",
        payload=[{"contentString": "Round-trip test"}],
    )
    fhir = comm.to_fhir_dict()
    orm = fhir_to_communication_orm(fhir)
    assert orm["status"] == "in-progress"
    assert orm["priority"] == "urgent"
    assert orm["subject_patient_id"] == pid
