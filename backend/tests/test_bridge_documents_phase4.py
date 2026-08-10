"""Phase 4 — DB-backed integration tests for the bridge document paths.

Gold-standard cross-patient isolation + idempotent-upload coverage (the mock
tests in test_health_assistant_bridge.py prove the wiring; these prove the
behaviour against the real migrated schema and the real ``uq_document_integration_dedup``
index). Mirror of the ``test_document_integration_dedup.py`` seeding pattern.
"""
import base64
import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.examination_model import ExaminationModel
from app.models.fhir.patient import Patient
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider


@pytest_asyncio.fixture
async def bridge_with_two_patients():
    """Tenant + ADMIN user + PatientA + PatientB + a bridge UserIntegration bound
    to PatientA + one examination per patient. Returns the integration id + the
    two examination ids."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Bridge Phase4 T.", slug=f"bp4-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(UserModel(id=user_id, email=f"bp4-{user_id.hex[:6]}@test.local", tenant_id=tenant_id, role="ADMIN"))
        await db.flush()
        db.add(Patient(id=patient_a, tenant_id=tenant_id, name={"family": "A", "given": ["Bound"]}, gender="UNKNOWN"))
        db.add(Patient(id=patient_b, tenant_id=tenant_id, name={"family": "B", "given": ["Other"]}, gender="UNKNOWN"))
        await db.flush()
        db.add(UserIntegration(
            id=integration_id, tenant_id=tenant_id, user_id=user_id, patient_id=patient_a,
            provider="health_assistant_bridge", status="ACTIVE", user_config={},
        ))
        await db.flush()
        exam_a = ExaminationModel(id=uuid.uuid4(), tenant_id=tenant_id, patient_id=patient_a, examination_date=datetime.date(2026, 8, 8))
        exam_b = ExaminationModel(id=uuid.uuid4(), tenant_id=tenant_id, patient_id=patient_b, examination_date=datetime.date(2026, 8, 8))
        db.add_all([exam_a, exam_b])
        await db.commit()
        return {
            "tenant_id": tenant_id,
            "integration_id": integration_id,
            "exam_a": exam_a.id,
            "exam_b": exam_b.id,
        }


async def _load_integration(integration_id) -> UserIntegration:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        return res.scalar_one()


def _get_request():
    req = MagicMock()
    req.query_params = {}
    return req


def _post_request(payload: dict):
    req = AsyncMock()
    req.json = AsyncMock(return_value=payload)
    req.query_params = {}
    return req


@pytest.mark.asyncio
async def test_documents_list_is_patient_scoped(bridge_with_two_patients):
    """GET /examinations/{id}/documents on a PatientB exam via an integration
    bound to PatientA → ValueError (the exam isn't visible to this patient)."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"examinations/{ctx['exam_b']}/documents",
            method="GET",
            request=_get_request(),
        )


@pytest.mark.asyncio
async def test_document_upload_is_idempotent_via_external_id(bridge_with_two_patients):
    """POST /examinations/{id}/documents with a client id is idempotent: the
    second upload returns the SAME document (dedup on
    (tenant, patient, integration, external_id)), not a duplicate."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    client_id = f"doc-{uuid.uuid4()}"
    payload = {
        "id": client_id,
        "filename": "lab.pdf",
        "content_type": "application/pdf",
        "data": base64.b64encode(b"%PDF-1.4 test content").decode(),
        "include_in_extraction": False,
    }

    first = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(payload),
    )
    second = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(payload),
    )

    assert first["external_id"] == client_id
    assert first["id"] == second["id"], "re-upload of the same client id must be idempotent"


@pytest.mark.asyncio
async def test_documents_list_returns_uploaded_doc(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request({
            "id": f"doc-{uuid.uuid4()}",
            "filename": "lab.pdf",
            "content_type": "application/pdf",
            "data": base64.b64encode(b"%PDF-1.4").decode(),
            "include_in_extraction": False,
        }),
    )

    result = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="GET",
        request=_get_request(),
    )

    assert len(result["data"]) >= 1
    assert result["data"][0]["filename"] == "lab.pdf"
    assert "cached_at" in result
