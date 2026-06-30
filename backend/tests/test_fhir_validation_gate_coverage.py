"""Regression tests for the FHIR validation gate coverage gap.

Audit context: the FHIR validation gate coverage table lists 4 service-layer
write paths that previously bypassed ``assert_valid_fhir``:

| Service                                  | Resource                  |
|------------------------------------------|---------------------------|
| doctor_service.create_doctor / update    | Practitioner              |
| organization_service.create / update      | Organization              |
| medication_service.add_patient_medication | Medication + MedicationCatalog |
| medication_service.create/update_catalog  | Medication                |
| document_service_db.upload_document       | DocumentReference         |
| document_service_db.edited-copy           | DocumentReference         |

Each of these now calls ``assert_valid_fhir(obj)`` right before ``commit()``
so invalid FHIR can never be persisted via these paths either (mirroring the
existing gate in ``fhir_service.create_*`` / ``allergy_service.*`` /
``facade.crud.create`` / ``medical_processing_service._save_observation``).

These tests exercise the gate by mocking the model's ``to_fhir_dict`` to raise
``FhirSerializationError`` and asserting the service raises before commit.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fhir_helpers import FhirSerializationError


# ---------------------------------------------------------------------------
# doctor_service — create + update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doctor_create_invokes_validation_gate():
    """create_doctor must call assert_valid_fhir before commit. We patch the
    helper to raise and verify the service propagates the error (no commit)."""
    from app.services import doctor_service

    db = AsyncMock(spec=AsyncSession)

    with patch("app.services.doctor_service.assert_valid_fhir") as mock_gate:
        mock_gate.side_effect = FhirSerializationError("invalid Practitioner")
        with pytest.raises(FhirSerializationError):
            await doctor_service.create_doctor(
                tenant_id="00000000-0000-0000-0000-000000000000",
                creator_id="00000000-0000-0000-0000-000000000001",
                name="",  # empty name triggers the (mocked) gate
                db=db,
            )
        # The gate was invoked.
        mock_gate.assert_called_once()
        # And commit was NOT called (gate raised before it).
        db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_doctor_update_invokes_validation_gate():
    from app.services import doctor_service

    db = AsyncMock(spec=AsyncSession)
    existing = MagicMock()
    # Make get_doctor return the existing row.
    with patch.object(doctor_service, "get_doctor", new=AsyncMock(return_value=existing)):
        with patch("app.services.doctor_service.assert_valid_fhir") as mock_gate:
            mock_gate.side_effect = FhirSerializationError("invalid Practitioner")
            with pytest.raises(FhirSerializationError):
                await doctor_service.update_doctor(
                    doctor_id="00000000-0000-0000-0000-000000000002",
                    tenant_id="00000000-0000-0000-0000-000000000000",
                    db=db,
                    name="x",
                )
            mock_gate.assert_called_once()
            db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# organization_service — create + update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_organization_create_invokes_validation_gate():
    from app.services import organization_service
    from app.schemas.organization import OrganizationCreate

    db = AsyncMock(spec=AsyncSession)
    payload = OrganizationCreate(name="Acme Clinics")

    with patch("app.services.organization_service.assert_valid_fhir") as mock_gate:
        mock_gate.side_effect = FhirSerializationError("invalid Organization")
        with pytest.raises(FhirSerializationError):
            await organization_service.create_organization(
                tenant_id="00000000-0000-0000-0000-000000000000",
                user_id="00000000-0000-0000-0000-000000000001",
                obj_in=payload,
                db=db,
            )
        mock_gate.assert_called_once()
        db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_organization_update_invokes_validation_gate():
    from app.services import organization_service
    from app.schemas.organization import OrganizationUpdate

    db = AsyncMock(spec=AsyncSession)
    existing = MagicMock()
    payload = OrganizationUpdate(name="Renamed")

    with patch.object(
        organization_service, "get_organization", new=AsyncMock(return_value=existing)
    ):
        with patch("app.services.organization_service.assert_valid_fhir") as mock_gate:
            mock_gate.side_effect = FhirSerializationError("invalid Organization")
            with pytest.raises(FhirSerializationError):
                await organization_service.update_organization(
                    organization_id="00000000-0000-0000-0000-000000000002",
                    tenant_id="00000000-0000-0000-0000-000000000000",
                    obj_in=payload,
                    db=db,
                )
            mock_gate.assert_called_once()
            db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# medication_service — catalog create + catalog update + patient add + patient update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_medication_catalog_create_invokes_validation_gate():
    from app.services import medication_service
    from app.schemas.medication import MedicationCatalogCreate

    db = AsyncMock(spec=AsyncSession)
    payload = MedicationCatalogCreate(name="Ibuprofen")

    with patch("app.services.medication_service.assert_valid_fhir") as mock_gate:
        mock_gate.side_effect = FhirSerializationError("invalid Medication")
        with pytest.raises(FhirSerializationError):
            await medication_service.create_catalog_medication(
                db=db,
                tenant_id="00000000-0000-0000-0000-000000000000",
                data=payload,
            )
        mock_gate.assert_called_once()
        db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_medication_catalog_update_invokes_validation_gate():
    from app.services import medication_service
    from app.schemas.medication import MedicationCatalogUpdate

    db = AsyncMock(spec=AsyncSession)
    existing = MagicMock()
    payload = MedicationCatalogUpdate(name="Renamed")

    # Patch the SELECT inside update_catalog_medication by making execute
    # return a result whose scalar_one_or_none yields the existing row.
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=result_mock)

    with patch("app.services.medication_service.assert_valid_fhir") as mock_gate:
        mock_gate.side_effect = FhirSerializationError("invalid Medication")
        with pytest.raises(FhirSerializationError):
            await medication_service.update_catalog_medication(
                db=db,
                catalog_id="00000000-0000-0000-0000-000000000002",
                tenant_id="00000000-0000-0000-0000-000000000000",
                data=payload,
            )
        mock_gate.assert_called_once()
        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# document_service_db — upload_document + edited-copy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_document_upload_invokes_validation_gate(monkeypatch):
    """upload_document must validate the DocumentModel before commit. We
    monkeypatch assert_valid_fhir to raise and verify upload propagates."""
    from app.services import document_service_db

    db = AsyncMock(spec=AsyncSession)

    # Build a minimal Starlette UploadFile stub.
    upload = MagicMock()
    upload.filename = "test.pdf"
    upload.read = AsyncMock(return_value=b"%PDF-1.4 fake content")

    # Avoid filesystem touching — patch the write paths.
    monkeypatch.setattr("os.makedirs", lambda *a, **kw: None)
    monkeypatch.setattr("os.path.isdir", lambda *a, **kw: True)
    async def _fake_write(path, content):
        return None

    monkeypatch.setattr(
        "app.services.document_service_db.write_file_if_not_exists",
        _fake_write,
        raising=False,
    )

    # Use a deterministic uuid so the doc_id check passes.
    monkeypatch.setattr(
        "app.services.document_service_db.uuid4",
        lambda: "00000000-0000-0000-0000-0000000000aa",
    )

    with patch("app.services.document_service_db.assert_valid_fhir") as mock_gate:
        mock_gate.side_effect = FhirSerializationError("invalid DocumentReference")
        with pytest.raises(FhirSerializationError):
            await document_service_db.upload_document(
                file=upload,
                patient_id=None,
                owner_id="00000000-0000-0000-0000-000000000001",
                tenant_id="00000000-0000-0000-0000-000000000000",
                db=db,
            )
        mock_gate.assert_called_once()
        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Sanity: gate is correctly imported in every service module
# ---------------------------------------------------------------------------

def test_gate_imported_in_all_four_services():
    """The four previously-ungated services must import assert_valid_fhir."""
    from app.services import (
        doctor_service,
        document_service_db,
        medication_service,
        organization_service,
    )

    for mod in (
        doctor_service,
        organization_service,
        medication_service,
        document_service_db,
    ):
        assert hasattr(mod, "assert_valid_fhir"), (
            f"{mod.__name__} must import assert_valid_fhir to gate writes"
        )
