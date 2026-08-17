"""Phase 3 — DB-backed tests for the bridge clinical-record read paths.

Each new path (medications / allergies / vaccines / clinical-events /
clinical-events/{id} / doctors) is covered by:
- a happy-path round-trip (seed a row, GET, assert it lands in the envelope)
- a cross-patient isolation check (the bridge is bound to PatientA; a row
  created under PatientB must NOT appear).

Mirrors the seeding + assertion pattern of test_bridge_documents_phase4.py.
"""

import datetime
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.clinical_event import ClinicalEvent
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.medication import Medication
from app.models.fhir.patient import Patient
from app.models.fhir.vaccine import PatientImmunization
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel


@pytest_asyncio.fixture
async def bridge_with_two_patients():
    """Tenant + ADMIN owner + PatientA + PatientB + a bridge integration bound
    to PatientA. Returns ids for cross-patient isolation tests."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id, name="Bridge P3 T.", slug=f"bp3-{tenant_id.hex[:8]}"
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"bp3-{user_id.hex[:6]}@test.local",
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
        await db.commit()
        return {
            "tenant_id": tenant_id,
            "integration_id": integration_id,
            "patient_a": patient_a,
            "patient_b": patient_b,
        }


async def _load_integration(integration_id) -> UserIntegration:
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(UserIntegration).where(UserIntegration.id == integration_id)
        )
        return res.scalar_one()


def _get_request(query: dict | None = None):
    req = MagicMock()
    req.query_params = query or {}
    return req


# --- Medications ---


@pytest.mark.asyncio
async def test_medications_list_returns_patient_rows(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(
            Medication(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_a"],
                status="ACTIVE",
                intent="statement",
                code={"text": "Lisinopril"},
                dosage="10mg",
                start_date=datetime.date(2026, 7, 1),
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration,
        path="medications",
        method="GET",
        request=_get_request(),
    )
    assert any(m["dosage"] == "10mg" for m in result["data"])


@pytest.mark.asyncio
async def test_medications_list_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(
            Medication(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_b"],
                status="ACTIVE",
                intent="statement",
                code={"text": "Secret Med B"},
                dosage="5mg",
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration,
        path="medications",
        method="GET",
        request=_get_request(),
    )
    assert all(m["code"].get("text") != "Secret Med B" for m in result["data"])


# --- Allergies ---


@pytest.mark.asyncio
async def test_allergies_list_returns_active_only_by_default(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(
            AllergyIntolerance(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_a"],
                clinical_status="ACTIVE",
                category="MEDICATION",
                criticality="LOW",
                code={"text": "Penicillin"},
            )
        )
        db.add(
            AllergyIntolerance(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_a"],
                clinical_status="RESOLVED",
                category="MEDICATION",
                criticality="LOW",
                code={"text": "Old Aspirin Allergy"},
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration, path="allergies", method="GET", request=_get_request()
    )
    statuses = {a["clinical_status"] for a in result["data"]}
    assert "ACTIVE" in statuses
    assert "RESOLVED" not in statuses

    # The ?active=false flag returns the full history.
    result_all = await provider.handle_api_request(
        integration=integration,
        path="allergies",
        method="GET",
        request=_get_request({"active": "false"}),
    )
    statuses_all = {a["clinical_status"] for a in result_all["data"]}
    assert "RESOLVED" in statuses_all


@pytest.mark.asyncio
async def test_allergies_list_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(
            AllergyIntolerance(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_b"],
                clinical_status="ACTIVE",
                category="MEDICATION",
                criticality="LOW",
                code={"text": "PatientB Only Allergy"},
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration, path="allergies", method="GET", request=_get_request()
    )
    assert all(a["code"].get("text") != "PatientB Only Allergy" for a in result["data"])


# --- Vaccines ---


@pytest.mark.asyncio
async def test_vaccines_list_returns_patient_rows(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(
            PatientImmunization(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_a"],
                status="completed",
                vaccine_code={"text": "COVID-19 mRNA"},
                administered_at=datetime.datetime(
                    2026, 7, 15, tzinfo=datetime.timezone.utc
                ),
                dose_number="1",
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration, path="vaccines", method="GET", request=_get_request()
    )
    assert any(v["vaccine_code"].get("text") == "COVID-19 mRNA" for v in result["data"])


@pytest.mark.asyncio
async def test_vaccines_list_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(
            PatientImmunization(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_b"],
                status="completed",
                vaccine_code={"text": "PatientB Hidden Vaccine"},
                administered_at=datetime.datetime(
                    2026, 1, 1, tzinfo=datetime.timezone.utc
                ),
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration, path="vaccines", method="GET", request=_get_request()
    )
    assert all(
        v["vaccine_code"].get("text") != "PatientB Hidden Vaccine"
        for v in result["data"]
    )


# --- Clinical events ---


@pytest.mark.asyncio
async def test_clinical_events_list_returns_patient_rows(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(
            ClinicalEvent(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_a"],
                status="ACTIVE",
                title="Hypertension diagnosis",
                onset_date=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration,
        path="clinical-events",
        method="GET",
        request=_get_request(),
    )
    assert any(e["title"] == "Hypertension diagnosis" for e in result["data"])
    item = next(e for e in result["data"] if e["title"] == "Hypertension diagnosis")
    assert item["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_clinical_events_list_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(
            ClinicalEvent(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_b"],
                status="ACTIVE",
                title="PatientB Hidden Condition",
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration,
        path="clinical-events",
        method="GET",
        request=_get_request(),
    )
    assert all(e["title"] != "PatientB Hidden Condition" for e in result["data"])


@pytest.mark.asyncio
async def test_clinical_event_detail_returns_to_dict_shape(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        event = ClinicalEvent(
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_a"],
            status="ACTIVE",
            title="Detail test",
            onset_date=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        event_id = event.id

    detail = await provider.handle_api_request(
        integration=integration,
        path=f"clinical-events/{event_id}",
        method="GET",
        request=_get_request(),
    )
    assert detail["id"] == str(event_id)
    assert detail["title"] == "Detail test"
    # The detail response carries the full to_dict() shape — observations,
    # examinations, type_details, etc. (empty for a freshly-seeded event)
    assert "observations" in detail
    assert "examinations" in detail


@pytest.mark.asyncio
async def test_clinical_event_detail_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        event_b = ClinicalEvent(
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_b"],
            status="ACTIVE",
            title="PatientB Event",
        )
        db.add(event_b)
        await db.commit()
        await db.refresh(event_b)
        event_id = event_b.id

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"clinical-events/{event_id}",
            method="GET",
            request=_get_request(),
        )


# --- Doctors ---


@pytest.mark.asyncio
async def test_doctors_list_returns_tenant_rows(bridge_with_two_patients):
    """Doctors are tenant-wide (not patient-scoped); the bridge returns the
    bound owner's tenant address book."""
    from app.models.doctor_model import DoctorModel

    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(DoctorModel(tenant_id=ctx["tenant_id"], name="Dr. House"))
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration, path="doctors", method="GET", request=_get_request()
    )
    assert any(d["name"] == "Dr. House" for d in result["data"])


@pytest.mark.asyncio
async def test_doctors_list_is_tenant_scoped(bridge_with_two_patients):
    """A doctor in another tenant must not appear."""
    from app.models.doctor_model import DoctorModel

    ctx = bridge_with_two_patients
    other_tenant = uuid.uuid4()
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=other_tenant, name="Other", slug=f"other-{other_tenant.hex[:8]}"
            )
        )
        await db.flush()
        db.add(DoctorModel(tenant_id=other_tenant, name="Dr. Other Tenant"))
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration, path="doctors", method="GET", request=_get_request()
    )
    assert all(d["name"] != "Dr. Other Tenant" for d in result["data"])
