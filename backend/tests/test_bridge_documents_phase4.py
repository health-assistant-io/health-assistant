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
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.examination_model import ExaminationModel
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel


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
        db.add(
            TenantModel(
                id=tenant_id, name="Bridge Phase4 T.", slug=f"bp4-{tenant_id.hex[:8]}"
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
    assert first["id"] == second["id"], (
        "re-upload of the same client id must be idempotent"
    )


@pytest.mark.asyncio
async def test_documents_list_returns_uploaded_doc(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(
            {
                "id": f"doc-{uuid.uuid4()}",
                "filename": "lab.pdf",
                "content_type": "application/pdf",
                "data": base64.b64encode(b"%PDF-1.4").decode(),
                "include_in_extraction": False,
            }
        ),
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


# --- Phase 1: documents content / detail / preview / patient-wide list ---


@pytest.mark.asyncio
async def test_documents_list_payload_is_enriched(bridge_with_two_patients):
    """`GET /examinations/{id}/documents` items now carry content_type,
    file_size, and examination_id (Phase 1 enrichment)."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(
            {
                "id": f"doc-{uuid.uuid4()}",
                "filename": "lab.pdf",
                "content_type": "application/pdf",
                "data": base64.b64encode(b"%PDF-1.4 enriched payload").decode(),
                "include_in_extraction": False,
            }
        ),
    )
    result = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="GET",
        request=_get_request(),
    )
    item = result["data"][0]
    assert item["content_type"] == "application/pdf"
    assert item["file_size"] == len(b"%PDF-1.4 enriched payload")
    assert item["examination_id"] == str(ctx["exam_a"])


@pytest.mark.asyncio
async def test_documents_all_list_returns_patient_docs(bridge_with_two_patients):
    """`GET /documents` (patient-wide) returns the bound patient's docs,
    optionally filtered by `?examination_id=`."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    # Upload two docs on exam_a.
    for i in range(2):
        await provider.handle_api_request(
            integration=integration,
            path=f"examinations/{ctx['exam_a']}/documents",
            method="POST",
            request=_post_request(
                {
                    "id": f"doc-{uuid.uuid4()}",
                    "filename": f"lab-{i}.pdf",
                    "content_type": "application/pdf",
                    "data": base64.b64encode(b"%PDF-1.4 body").decode(),
                    "include_in_extraction": False,
                }
            ),
        )

    result = await provider.handle_api_request(
        integration=integration,
        path="documents",
        method="GET",
        request=_get_request(),
    )
    assert len(result["data"]) >= 2
    # The filter narrows to one exam.
    req_exam = _get_request()
    req_exam.query_params = {"examination_id": str(ctx["exam_a"])}
    by_exam = await provider.handle_api_request(
        integration=integration, path="documents", method="GET", request=req_exam
    )
    assert all(d["examination_id"] == str(ctx["exam_a"]) for d in by_exam["data"])


@pytest.mark.asyncio
async def test_document_detail_returns_enriched_payload(bridge_with_two_patients):
    """`GET /documents/{id}` returns the same shape as a list item."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(
            {
                "id": f"doc-{uuid.uuid4()}",
                "filename": "report.pdf",
                "content_type": "application/pdf",
                "data": base64.b64encode(b"%PDF-1.4 detail body").decode(),
                "include_in_extraction": False,
            }
        ),
    )
    detail = await provider.handle_api_request(
        integration=integration,
        path=f"documents/{upload['id']}",
        method="GET",
        request=_get_request(),
    )
    assert detail["id"] == upload["id"]
    assert detail["filename"] == "report.pdf"
    assert detail["content_type"] == "application/pdf"
    assert detail["file_size"] == len(b"%PDF-1.4 detail body")
    assert detail["examination_id"] == str(ctx["exam_a"])


@pytest.mark.asyncio
async def test_document_detail_is_patient_scoped(bridge_with_two_patients):
    """`GET /documents/{id}` for a doc owned by another patient → ValueError.
    We upload under exam_a (PatientA), then move the doc to PatientB's row id
    via a direct DB write to simulate "doc exists but belongs to another
    patient" without setting up a second bridge instance."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(
            {
                "id": f"doc-{uuid.uuid4()}",
                "filename": "scope.pdf",
                "content_type": "application/pdf",
                "data": base64.b64encode(b"%PDF-1.4 scope").decode(),
                "include_in_extraction": False,
            }
        ),
    )
    # Reassign the doc to PatientB directly; the integration is bound to A.
    from app.models.document_model import DocumentModel

    async with AsyncSessionLocal() as db:
        from sqlalchemy import update

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
            method="GET",
            request=_get_request(),
        )


@pytest.mark.asyncio
async def test_document_content_returns_uploaded_bytes(bridge_with_two_patients):
    """`GET /documents/{id}/content` streams the exact bytes that were uploaded."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    body = b"%PDF-1.4 the actual binary payload bytes"
    upload = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(
            {
                "id": f"doc-{uuid.uuid4()}",
                "filename": "bytes.pdf",
                "content_type": "application/pdf",
                "data": base64.b64encode(body).decode(),
                "include_in_extraction": False,
            }
        ),
    )
    response = await provider.handle_api_request(
        integration=integration,
        path=f"documents/{upload['id']}/content",
        method="GET",
        request=_get_request(),
    )
    from starlette.responses import Response

    assert isinstance(response, Response)
    assert response.body == body
    assert response.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_document_content_is_patient_scoped(bridge_with_two_patients):
    """Cross-patient content fetch → ValueError (the `_bound_document`
    patient filter rejects it)."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(
            {
                "id": f"doc-{uuid.uuid4()}",
                "filename": "isolated.pdf",
                "content_type": "application/pdf",
                "data": base64.b64encode(b"%PDF-1.4 isolated").decode(),
                "include_in_extraction": False,
            }
        ),
    )
    from sqlalchemy import update

    from app.models.document_model import DocumentModel

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
            path=f"documents/{upload['id']}/content",
            method="GET",
            request=_get_request(),
        )


@pytest.mark.asyncio
async def test_document_preview_passthrough_for_images(bridge_with_two_patients):
    """`GET /documents/{id}/preview` for an image returns the stored bytes
    with their guessed MIME (no OCR conversion needed)."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    # 1×1 PNG (valid PNG header + IHDR + IDAT + IEND).
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c63000100000005000100"
        "0d0a2db400000000494544ae426082"
    )
    upload = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(
            {
                "id": f"doc-{uuid.uuid4()}",
                "filename": "thumb.png",
                "content_type": "image/png",
                "data": base64.b64encode(png_bytes).decode(),
                "include_in_extraction": False,
            }
        ),
    )
    response = await provider.handle_api_request(
        integration=integration,
        path=f"documents/{upload['id']}/preview",
        method="GET",
        request=_get_request(),
    )
    from starlette.responses import Response

    assert isinstance(response, Response)
    assert response.body == png_bytes
    assert response.media_type == "image/png"


@pytest.mark.asyncio
async def test_documents_excludes_soft_deleted(bridge_with_two_patients):
    """A soft-deleted doc (``deleted_at`` set) is excluded from list + 404s
    on detail (the `_bound_document` filter drops it)."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    upload = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}/documents",
        method="POST",
        request=_post_request(
            {
                "id": f"doc-{uuid.uuid4()}",
                "filename": "deleted.pdf",
                "content_type": "application/pdf",
                "data": base64.b64encode(b"%PDF-1.4 soft-deleted").decode(),
                "include_in_extraction": False,
            }
        ),
    )
    from sqlalchemy import update

    from app.models.document_model import DocumentModel

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(DocumentModel)
            .where(DocumentModel.id == upload["id"])
            .values(deleted_at=datetime.datetime.now(datetime.timezone.utc))
        )
        await db.commit()

    req = _get_request()
    req.query_params = {"examination_id": str(ctx["exam_a"])}
    result = await provider.handle_api_request(
        integration=integration, path="documents", method="GET", request=req
    )
    ids = [d["id"] for d in result["data"]]
    assert upload["id"] not in ids

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"documents/{upload['id']}",
            method="GET",
            request=_get_request(),
        )
