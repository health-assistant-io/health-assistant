"""R4 (2026-08-17) — DB-backed tests for the mobile rich-content bridge reads:
biomarker `info`, `GET /documents/{id}/text`, and the examination-detail
category/lab_name/external_id fields. Mirror of the phase-4 seeding pattern
(cross-patient isolation included)."""

import datetime
import uuid

import pytest
import pytest_asyncio
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel


@pytest_asyncio.fixture
async def rich_content_ctx():
    """Tenant + user + PatientA/PatientB + integration bound to A + one exam
    per patient + one document each (with extracted_text) + one biomarker
    definition carrying Markdown `info`."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id, name="Bridge R4 T.", slug=f"br4-{tenant_id.hex[:8]}"
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"br4-{user_id.hex[:6]}@test.local",
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
            examination_date=datetime.date(2026, 8, 17),
            external_id="client-ext-1",
        )
        exam_b = ExaminationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_b,
            examination_date=datetime.date(2026, 8, 17),
        )
        db.add_all([exam_a, exam_b])
        await db.flush()
        doc_a = DocumentModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            examination_id=exam_a.id,
            filename="panel.pdf",
            file_path="/tmp/br4-panel.pdf",
            owner_id=user_id,
            status="completed",
            extracted_text="## Lipid panel\n- **LDL** 3.9 mmol/L",
        )
        doc_b = DocumentModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_b,
            examination_id=exam_b.id,
            filename="other.pdf",
            file_path="/tmp/br4-other.pdf",
            owner_id=user_id,
            status="completed",
            extracted_text="patient b text",
        )
        db.add_all([doc_a, doc_b])
        await db.flush()
        biomarker = BiomarkerDefinition(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name="LDL Cholesterol",
            slug=f"ldl-{tenant_id.hex[:6]}",
            code="13457-7",
            coding_system="loinc",
            info="Low-density lipoprotein — the **bad** cholesterol.",
        )
        db.add(biomarker)
        await db.commit()
        return {
            "tenant_id": tenant_id,
            "integration_id": integration_id,
            "exam_a": exam_a.id,
            "exam_b": exam_b.id,
            "doc_a": doc_a.id,
            "doc_b": doc_b.id,
            "biomarker_id": biomarker.id,
        }


async def _load_integration(integration_id) -> UserIntegration:
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(UserIntegration).where(UserIntegration.id == integration_id)
        )
        return res.scalar_one()


def _get_request():
    from unittest.mock import MagicMock

    req = MagicMock()
    req.query_params = {}
    return req


@pytest.mark.asyncio
async def test_biomarkers_include_markdown_info(rich_content_ctx):
    ctx = rich_content_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="biomarkers",
        method="GET",
        request=_get_request(),
    )

    ldl = next(
        (b for b in result["data"] if b.get("id") == str(ctx["biomarker_id"])), None
    )
    assert ldl is not None
    assert ldl["info"] == "Low-density lipoprotein — the **bad** cholesterol."


@pytest.mark.asyncio
async def test_document_text_returns_extracted_markdown(rich_content_ctx):
    ctx = rich_content_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path=f"documents/{ctx['doc_a']}/text",
        method="GET",
        request=_get_request(),
    )

    assert result["id"] == str(ctx["doc_a"])
    assert "**LDL**" in result["extracted_text"]
    assert result["status"] == "completed"
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_document_text_is_patient_scoped(rich_content_ctx):
    """PatientB's document via an integration bound to PatientA → ValueError."""
    ctx = rich_content_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"documents/{ctx['doc_b']}/text",
            method="GET",
            request=_get_request(),
        )


@pytest.mark.asyncio
async def test_examination_detail_carries_category_lab_external_id(rich_content_ctx):
    ctx = rich_content_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}",
        method="GET",
        request=_get_request(),
    )

    assert result["id"] == str(ctx["exam_a"])
    assert result["external_id"] == "client-ext-1"
    assert "category" in result
    assert "lab_name" in result
    assert result["category"] is None
    assert result["lab_name"] is None
