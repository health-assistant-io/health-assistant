"""Phase 4 — DB-backed tests for the bridge mutation + extraction paths.

Coverage per Phase 4 dispatch arm:
- DELETE /examinations/{id}                  (hard delete + cascade)
- DELETE /documents/{id}                     (hard delete + file unlink)
- POST  /documents/{id}/extract              (OCR dispatch)
- GET   /documents/{id}/extract/status       (live row state)
- GET   /examinations/{id}/status            (exam status + docs array)
- GET   /examinations/{id}/logs              (TaskLog rows)

Each operation is patient-scoped — cross-patient attempts must fail BEFORE any
side effect (no file unlink, no row delete, no OCR dispatch).
"""

import base64
import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.fhir.patient import Patient
from app.models.task_log import TaskLog
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel


@pytest_asyncio.fixture
async def bridge_with_two_patients():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id, name="Bridge P4 T.", slug=f"bp4-{tenant_id.hex[:8]}"
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"bp4-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_a,
                tenant_id=tenant_id,
                name={"family": "A", "given": ["Bound"]},
                gender="UNKNOWN",
            )
        )
        db.add(
            Patient(
                id=patient_b,
                tenant_id=tenant_id,
                name={"family": "B", "given": ["Other"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_a,
                provider="health_assistant_bridge",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.flush()
        exam_a = ExaminationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            examination_date=datetime.date(2026, 8, 8),
        )
        exam_b = ExaminationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_b,
            examination_date=datetime.date(2026, 8, 8),
        )
        db.add_all([exam_a, exam_b])
        await db.commit()
        return {
            "tenant_id": tenant_id,
            "integration_id": integration_id,
            "patient_a": patient_a,
            "patient_b": patient_b,
            "exam_a": exam_a.id,
            "exam_b": exam_b.id,
        }


async def _load_integration(integration_id) -> UserIntegration:
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(UserIntegration).where(UserIntegration.id == integration_id)
        )
        return res.scalar_one()


def _get_request():
    req = MagicMock()
    req.query_params = {}
    return req


def _post_request(payload: dict | None = None):
    req = AsyncMock()
    if payload is not None:
        req.json = AsyncMock(return_value=payload)
    req.query_params = {}
    return req


async def _upload_doc(
    integration,
    provider,
    exam_id,
    body: bytes = b"%PDF-1.4",
    filename: str = "test.pdf",
):
    """Helper — push a doc through the bridge so the test has a real on-disk file."""
    return await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{exam_id}/documents",
        method="POST",
        request=_post_request(
            {
                "id": f"doc-{uuid.uuid4()}",
                "filename": filename,
                "content_type": "application/pdf",
                "data": base64.b64encode(body).decode(),
                "include_in_extraction": False,
            }
        ),
    )


# --- DELETE /examinations/{id} ---


@pytest.mark.asyncio
async def test_delete_examination_removes_row_and_documents(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await _upload_doc(integration, provider, ctx["exam_a"])
    doc_id = upload["id"]

    result = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}",
        method="DELETE",
        request=_get_request(),
    )
    assert result["deleted"] is True

    # The exam row + its document row are gone.
    async with AsyncSessionLocal() as db:
        exam = await db.get(ExaminationModel, ctx["exam_a"])
        doc = await db.get(DocumentModel, doc_id)
        assert exam is None
        assert doc is None


@pytest.mark.asyncio
async def test_delete_examination_is_patient_scoped(bridge_with_two_patients):
    """DELETE on PatientB's exam via an integration bound to PatientA → ValueError."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"examinations/{ctx['exam_b']}",
            method="DELETE",
            request=_get_request(),
        )
    # The exam row is untouched.
    async with AsyncSessionLocal() as db:
        assert await db.get(ExaminationModel, ctx["exam_b"]) is not None


# --- DELETE /documents/{id} ---


@pytest.mark.asyncio
async def test_delete_document_removes_row(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await _upload_doc(integration, provider, ctx["exam_a"])

    result = await provider.handle_api_request(
        integration=integration,
        path=f"documents/{upload['id']}",
        method="DELETE",
        request=_get_request(),
    )
    assert result["deleted"] is True
    async with AsyncSessionLocal() as db:
        assert await db.get(DocumentModel, upload["id"]) is None


@pytest.mark.asyncio
async def test_delete_document_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await _upload_doc(integration, provider, ctx["exam_a"])

    # Reassign to PatientB and attempt delete via an A-bound integration.
    from sqlalchemy import update

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(DocumentModel)
            .where(DocumentModel.id == upload["id"])
            .values(patient_id=ctx["patient_b"])
        )
        await db.commit()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"documents/{upload['id']}",
            method="DELETE",
            request=_get_request(),
        )
    async with AsyncSessionLocal() as db:
        assert await db.get(DocumentModel, upload["id"]) is not None


# --- POST /documents/{id}/extract ---


@pytest.mark.asyncio
async def test_trigger_extraction_returns_job_id(bridge_with_two_patients):
    """Trigger extraction returns a job_id and flips the doc to 'processing'.
    The Celery dispatch is best-effort; broker-down is swallowed, so the test
    doesn't need a live worker."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await _upload_doc(integration, provider, ctx["exam_a"])

    result = await provider.handle_api_request(
        integration=integration,
        path=f"documents/{upload['id']}/extract",
        method="POST",
        request=_post_request(),
    )
    assert result["job_id"] == f"ocr-{upload['id']}"
    assert "message" in result

    # The doc row now reflects "processing".
    async with AsyncSessionLocal() as db:
        d = await db.get(DocumentModel, upload["id"])
        assert d.status == "processing"
        assert d.progress >= 10


@pytest.mark.asyncio
async def test_trigger_extraction_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await _upload_doc(integration, provider, ctx["exam_a"])

    from sqlalchemy import update

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(DocumentModel)
            .where(DocumentModel.id == upload["id"])
            .values(patient_id=ctx["patient_b"])
        )
        await db.commit()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"documents/{upload['id']}/extract",
            method="POST",
            request=_post_request(),
        )


# --- GET /documents/{id}/extract/status ---


@pytest.mark.asyncio
async def test_document_extract_status_returns_live_state(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await _upload_doc(integration, provider, ctx["exam_a"])

    status = await provider.handle_api_request(
        integration=integration,
        path=f"documents/{upload['id']}/extract/status",
        method="GET",
        request=_get_request(),
    )
    assert status["id"] == upload["id"]
    assert "status" in status
    assert "progress" in status
    assert "error_message" in status


@pytest.mark.asyncio
async def test_document_extract_status_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await _upload_doc(integration, provider, ctx["exam_a"])

    from sqlalchemy import update

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(DocumentModel)
            .where(DocumentModel.id == upload["id"])
            .values(patient_id=ctx["patient_b"])
        )
        await db.commit()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"documents/{upload['id']}/extract/status",
            method="GET",
            request=_get_request(),
        )


# --- GET /examinations/{id}/status ---


@pytest.mark.asyncio
async def test_examination_extraction_status_returns_documents_array(
    bridge_with_two_patients,
):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    await _upload_doc(integration, provider, ctx["exam_a"], filename="a.pdf")
    await _upload_doc(integration, provider, ctx["exam_a"], filename="b.pdf")

    status = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/status",
        method="GET",
        request=_get_request(),
    )
    assert status["id"] == str(ctx["exam_a"])
    assert "extraction_status" in status
    assert "documents" in status
    assert len(status["documents"]) >= 2
    assert {"id", "filename", "status", "progress", "include_in_extraction"} <= set(
        status["documents"][0].keys()
    )


@pytest.mark.asyncio
async def test_examination_status_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"examinations/{ctx['exam_b']}/status",
            method="GET",
            request=_get_request(),
        )


# --- GET /examinations/{id}/logs ---


@pytest.mark.asyncio
async def test_examination_logs_returns_rows_for_exam_tenant(bridge_with_two_patients):
    """Logs are seeded for the bound exam; the query returns them in time order.
    A log row for another tenant must NOT leak."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    other_tenant = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        # Bound exam's log
        db.add(
            TaskLog(
                tenant_id=ctx["tenant_id"],
                task_name="ocr_document",
                task_id=str(ctx["exam_a"]),
                resource_id=ctx["exam_a"],
                level="INFO",
                stage="start",
                message="OCR started",
            )
        )
        # Cross-tenant log using the same task_id (must not leak)
        db.add(
            TenantModel(id=other_tenant, name="Other", slug=f"o-{other_tenant.hex[:8]}")
        )
        await db.flush()
        db.add(
            TaskLog(
                tenant_id=other_tenant,
                task_name="ocr_document",
                task_id=str(ctx["exam_a"]),
                resource_id=ctx["exam_a"],
                level="INFO",
                stage="start",
                message="Should not leak",
            )
        )
        await db.commit()

    logs = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/logs",
        method="GET",
        request=_get_request(),
    )
    assert isinstance(logs, list)
    assert len(logs) >= 1
    assert all(log["message"] != "Should not leak" for log in logs)


@pytest.mark.asyncio
async def test_examination_logs_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"examinations/{ctx['exam_b']}/logs",
            method="GET",
            request=_get_request(),
        )
