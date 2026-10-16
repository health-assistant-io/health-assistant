"""Bridge examination enrichment (2026-10-10) — DB-backed tests for the
mobile "next-gen exam pages" wire additions: `GET /examinations` list items
carry the card fields (category, lab, diagnoses, progress, per-section
counts), and `GET /examinations/{id}` carries the full detail projection
(observations with flattened reference ranges, doctors, medications, and the
linked clinical events / health journeys). Mirrors the phase-4 seeding
pattern (cross-patient isolation included)."""

import datetime
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.extensions.builtin.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from app.models.clinical_event import ClinicalEvent, ClinicalEventStatus, EventExaminationLink
from app.models.concept_model import Concept
from app.models.doctor_model import DoctorModel
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.fhir.medication import Medication, MedicationIntent, MedicationStatus
from app.models.fhir.organization import OrganizationModel
from app.models.fhir.patient import Observation, Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel


@pytest_asyncio.fixture
async def exam_ctx():
    """Tenant + user + PatientA/PatientB + integration bound to A + one exam
    per patient. PatientA's exam carries the full enrichment payload: a
    category concept, a lab organization, one observation with a flat
    reference range, one linked doctor, one linked medication, and one linked
    clinical event (health journey)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Exam enrich", slug=f"ex-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"ex-{user_id.hex[:6]}@test.local",
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
        category = Concept(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            slug=f"lab-{tenant_id.hex[:6]}",
            name="Biochemical Tests",
        )
        lab = OrganizationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name="City General Laboratory",
        )
        db.add_all([category, lab])
        await db.flush()

        exam_a = ExaminationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            examination_date=datetime.date(2026, 10, 2),
            external_id="client-ext-exam",
            category_concept_id=category.id,
            organization_id=lab.id,
            diagnoses=["E78.5 Hyperlipidemia"],
            extraction_status="completed",
            extraction_progress=100,
        )
        exam_b = ExaminationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_b,
            examination_date=datetime.date(2026, 10, 2),
        )
        exam_dateless = ExaminationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
        )
        exam_deleted = ExaminationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            examination_date=datetime.date(2026, 9, 1),
            deleted_at=datetime.datetime.now(datetime.UTC),
        )
        db.add_all([exam_a, exam_b, exam_dateless, exam_deleted])
        await db.flush()

        obs = Observation(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            examination_id=exam_a.id,
            status="final",
            code={
                "coding": [{"system": "http://loinc.org", "code": "13457-7"}],
                "text": "LDL Cholesterol",
            },
            subject={"reference": f"Patient/{patient_a}"},
            effective_datetime=datetime.datetime(2026, 10, 2, 8, 30, tzinfo=datetime.UTC),
            raw_value=3.9,
            normalized_value=3.9,
            reference_range={"low": 0.0, "high": 3.0},
            interpretation="HIGH",
        )
        # FHIR-imported rows store the list shape — the detail read must
        # flatten it to {low, high} like the flat bridge-written rows.
        obs_fhir_range = Observation(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            examination_id=exam_a.id,
            status="final",
            code={
                "coding": [{"system": "http://loinc.org", "code": "2093-3"}],
                "text": "Cholesterol Total",
            },
            subject={"reference": f"Patient/{patient_a}"},
            effective_datetime=datetime.datetime(2026, 10, 2, 8, 35, tzinfo=datetime.UTC),
            raw_value=5.2,
            normalized_value=5.2,
            reference_range=[
                {"low": {"value": 0.0, "unit": "mmol/L"}, "high": {"value": 5.0, "unit": "mmol/L"}}
            ],
        )
        obs_deleted = Observation(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            examination_id=exam_a.id,
            status="final",
            code={
                "coding": [{"system": "http://loinc.org", "code": "13457-7"}],
                "text": "LDL (retracted)",
            },
            subject={"reference": f"Patient/{patient_a}"},
            raw_value=9.9,
            deleted_at=datetime.datetime.now(datetime.UTC),
        )
        doctor = DoctorModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name="Dr. Maria Papadopoulou",
        )
        medication = Medication(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            examination_id=exam_a.id,
            status=MedicationStatus.ACTIVE,
            intent=MedicationIntent.PROPOSAL,
            code={"text": "Atorvastatin 20 mg"},
            dosage="1 tablet",
        )
        doc_deleted = DocumentModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            examination_id=exam_a.id,
            owner_id=user_id,
            filename="retracted.pdf",
            file_path=f"uploads/{tenant_id.hex}/retracted.pdf",
            deleted_at=datetime.datetime.now(datetime.UTC),
        )
        db.add_all([obs, obs_fhir_range, obs_deleted, doctor, medication, doc_deleted])
        await db.flush()
        # Link the doctor via the association row directly — appending to the
        # relationship would lazy-load the collection (MissingGreenlet).
        from sqlalchemy import insert

        from app.models.associations import examination_doctors

        await db.execute(
            insert(examination_doctors).values(
                examination_id=exam_a.id,
                doctor_id=doctor.id,
            )
        )

        journey = ClinicalEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_a,
            title="Hyperlipidemia follow-up",
            status=ClinicalEventStatus.ACTIVE,
        )
        db.add(journey)
        await db.flush()
        db.add(
            EventExaminationLink(
                id=uuid.uuid4(),
                event_id=journey.id,
                examination_id=exam_a.id,
                reason="Annual lipid panel",
            )
        )
        await db.commit()
        return {
            "tenant_id": tenant_id,
            "integration_id": integration_id,
            "exam_a": exam_a.id,
            "exam_b": exam_b.id,
            "exam_dateless": exam_dateless.id,
            "exam_deleted": exam_deleted.id,
        }


async def _load_integration(integration_id) -> UserIntegration:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        return res.scalar_one()


def _get_request():
    from unittest.mock import MagicMock

    req = MagicMock()
    req.query_params = {}
    return req


@pytest.mark.asyncio
async def test_exam_list_items_carry_card_fields(exam_ctx):
    ctx = exam_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="examinations",
        method="GET",
        request=_get_request(),
    )

    ids = [e["id"] for e in result["data"]]
    assert str(ctx["exam_deleted"]) not in ids
    assert str(ctx["exam_b"]) not in ids
    item = next((e for e in result["data"] if e["id"] == str(ctx["exam_a"])), None)
    assert item is not None
    assert item["category"] == "Biochemical Tests"
    assert item["lab_name"] == "City General Laboratory"
    assert item["diagnoses"] == ["E78.5 Hyperlipidemia"]
    assert item["extraction_progress"] == 100
    assert item["observation_count"] == 2
    assert item["medication_count"] == 1
    assert item["document_count"] == 0
    assert item["created_at"]
    assert item["updated_at"]


@pytest.mark.asyncio
async def test_exam_list_orders_dateless_exams_last(exam_ctx):
    """Offline-created exams have no date yet — Postgres DESC defaults to
    NULLS FIRST, so without nullslast they would jump the newest-first queue."""
    ctx = exam_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="examinations",
        method="GET",
        request=_get_request(),
    )

    ids = [e["id"] for e in result["data"]]
    assert ids[0] == str(ctx["exam_a"])
    assert ids[-1] == str(ctx["exam_dateless"])
    assert result["data"][-1]["examination_date"] is None


@pytest.mark.asyncio
async def test_exam_detail_carries_results_doctors_medications_journeys(exam_ctx):
    ctx = exam_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path=f"examinations/{ctx['exam_a']}",
        method="GET",
        request=_get_request(),
    )

    assert result["id"] == str(ctx["exam_a"])
    assert result["category"] == "Biochemical Tests"
    assert result["lab_name"] == "City General Laboratory"
    assert result["extraction_progress"] == 100
    assert result["observation_count"] == 2
    assert result["document_count"] == 0

    obs = next(o for o in result["observations"] if o["code"]["text"] == "LDL Cholesterol")
    assert obs["raw_value"] == 3.9
    assert obs["reference_range"] == {"low": 0.0, "high": 3.0}
    assert obs["interpretation"] == "HIGH"
    assert obs["code"]["text"] == "LDL Cholesterol"
    assert obs["effective_datetime"] is not None

    # FHIR-imported rows store the list shape — the detail read flattens it.
    fhir_obs = next(o for o in result["observations"] if o["code"]["text"] == "Cholesterol Total")
    assert fhir_obs["reference_range"] == {"low": 0.0, "high": 5.0}
    assert all(o["code"]["text"] != "LDL (retracted)" for o in result["observations"])

    assert result["doctors"][0]["name"] == "Dr. Maria Papadopoulou"
    assert result["medications"][0]["code"]["text"] == "Atorvastatin 20 mg"

    journey = result["clinical_events"][0]
    assert journey["title"] == "Hyperlipidemia follow-up"
    assert journey["status"] == "ACTIVE"
    assert journey["reason"] == "Annual lipid panel"


@pytest.mark.asyncio
async def test_enriched_detail_is_patient_scoped(exam_ctx):
    """PatientB's exam via an integration bound to PatientA → ValueError
    (the enrichment must not widen the isolation envelope)."""
    ctx = exam_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"examinations/{ctx['exam_b']}",
            method="GET",
            request=_get_request(),
        )


@pytest.mark.asyncio
async def test_soft_deleted_exam_is_hidden_from_list_and_detail(exam_ctx):
    """A soft-deleted exam is invisible to the bridge reads, exactly like a
    foreign-patient one — `_bound_examination` is the single chokepoint."""
    ctx = exam_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    listed = await provider.handle_api_request(
        integration=integration,
        path="examinations",
        method="GET",
        request=_get_request(),
    )
    assert str(ctx["exam_deleted"]) not in [e["id"] for e in listed["data"]]

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"examinations/{ctx['exam_deleted']}",
            method="GET",
            request=_get_request(),
        )
