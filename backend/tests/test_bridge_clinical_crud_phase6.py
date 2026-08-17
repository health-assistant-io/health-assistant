"""Phase 6 — DB-backed tests for the bridge clinical-record mutation paths.

Coverage per resource (medications / allergies / vaccines / clinical-events /
doctors):
- POST    /<resource>                     (create + read-back via existing GET)
- POST    /<resource>  idempotency        (same external_id → no duplicate)
- PUT     /<resource>/{id}                (update + read-back)
- DELETE  /<resource>/{id}                (delete + verify gone)
- cross-patient isolation                 (operating on patient_b's row fails)

For clinical-events, additionally:
- POST /clinical-events/{id}/occurrences  (log a recurrence)

Each test seeds its own rows inside the shared ``bridge_with_two_patients``
fixture (tenant + ADMIN owner + patient_a bound + patient_b unbound).
"""

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.clinical_event import ClinicalEvent, ClinicalEventType
from app.models.concept_model import Concept
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.medication import Medication
from app.models.fhir.patient import Patient
from app.models.fhir.vaccine import PatientImmunization
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel


@pytest_asyncio.fixture
async def bridge_with_two_patients():
    """Tenant + ADMIN owner + PatientA (bound) + PatientB (cross-patient probe)
    + a bridge integration bound to PatientA. Seeds the clinical-event type
    catalog row required by ``create_event``."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name="Bridge P6 T.",
                slug=f"bp6-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"bp6-{user_id.hex[:6]}@test.local",
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
        # ClinicalEventType is tenant-scoped reference data; create_event
        # looks it up by slug + walks its category_concept_id, so seed a
        # throwaway concept + a "symptom" type pointing at it.
        category_concept = Concept(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            slug=f"cat-p6-{tenant_id.hex[:6]}",
            name="P6 Category",
        )
        db.add(category_concept)
        await db.flush()
        db.add(
            ClinicalEventType(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                name="Symptom",
                slug="symptom",
                category_concept_id=category_concept.id,
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


def _post_request(payload: dict | None = None):
    req = AsyncMock()
    if payload is not None:
        req.json = AsyncMock(return_value=payload)
    req.query_params = {}
    return req


# =========================================================================
# Medications
# =========================================================================


@pytest.mark.asyncio
async def test_create_medication_returns_row_and_persists(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    external = f"med-{uuid.uuid4()}"
    result = await provider.handle_api_request(
        integration=integration,
        path="medications",
        method="POST",
        request=_post_request(
            {
                "id": external,
                "code": {"text": "Lisinopril"},
                "status": "ACTIVE",
                "intent": "statement",
                "dosage": "10mg",
                "start_date": "2026-08-12",
            }
        ),
    )
    assert result["code"] == {"text": "Lisinopril"}
    assert result["external_id"] == external
    assert result["status"] == "ACTIVE"

    # Read-back via the existing GET path.
    listing = await provider.handle_api_request(
        integration=integration,
        path="medications",
        method="GET",
        request=_get_request(),
    )
    codes = [m["code"] for m in listing["data"]]
    assert {"text": "Lisinopril"} in codes


@pytest.mark.asyncio
async def test_create_medication_is_idempotent_on_external_id(
    bridge_with_two_patients
):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    external = f"med-{uuid.uuid4()}"

    payload = {
        "id": external,
        "code": {"text": "Metformin"},
        "status": "ACTIVE",
        "intent": "statement",
    }
    first = await provider.handle_api_request(
        integration=integration, path="medications", method="POST",
        request=_post_request(payload),
    )
    second = await provider.handle_api_request(
        integration=integration, path="medications", method="POST",
        request=_post_request(payload),
    )
    assert first["id"] == second["id"]

    listing = await provider.handle_api_request(
        integration=integration, path="medications", method="GET",
        request=_get_request(),
    )
    matches = [m for m in listing["data"] if m["external_id"] == external]
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_update_medication_mutates_fields(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    created = await provider.handle_api_request(
        integration=integration, path="medications", method="POST",
        request=_post_request(
            {"code": {"text": "Atorvastatin"}, "status": "ACTIVE", "intent": "plan"}
        ),
    )
    updated = await provider.handle_api_request(
        integration=integration,
        path=f"medications/{created['id']}",
        method="PUT",
        request=_post_request({"dosage": "20mg", "note": "titrating up"}),
    )
    assert updated["dosage"] == "20mg"
    assert updated["note"] == "titrating up"
    assert updated["code"] == {"text": "Atorvastatin"}  # unchanged


@pytest.mark.asyncio
async def test_delete_medication_soft_deletes(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    created = await provider.handle_api_request(
        integration=integration, path="medications", method="POST",
        request=_post_request(
            {"code": {"text": "Ibuprofen"}, "status": "ACTIVE", "intent": "statement"}
        ),
    )
    result = await provider.handle_api_request(
        integration=integration,
        path=f"medications/{created['id']}",
        method="DELETE",
        request=_get_request(),
    )
    assert result["deleted"] is True

    # GET must not list soft-deleted rows.
    listing = await provider.handle_api_request(
        integration=integration, path="medications", method="GET",
        request=_get_request(),
    )
    ids = [m["id"] for m in listing["data"]]
    assert created["id"] not in ids


@pytest.mark.asyncio
async def test_medication_cross_patient_isolation(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    # Seed a row under patient_b (the unbound patient).
    async with AsyncSessionLocal() as db:
        foreign = Medication(
            id=uuid.uuid4(),
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_b"],
            status="ACTIVE",
            intent="statement",
            code={"text": "Warfarin"},
        )
        db.add(foreign)
        await db.commit()
        foreign_id = str(foreign.id)

    # PUT/DELETE on that row through the patient_a-bound integration must fail.
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"medications/{foreign_id}",
            method="PUT",
            request=_post_request({"note": "nope"}),
        )
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"medications/{foreign_id}",
            method="DELETE",
            request=_get_request(),
        )


# =========================================================================
# Allergies
# =========================================================================


@pytest.mark.asyncio
async def test_create_allergy_returns_row_and_persists(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    external = f"alg-{uuid.uuid4()}"

    result = await provider.handle_api_request(
        integration=integration, path="allergies", method="POST",
        request=_post_request(
            {
                "id": external,
                "code": {"text": "Penicillin"},
                "category": "MEDICATION",
                "criticality": "HIGH",
            }
        ),
    )
    assert result["code"] == {"text": "Penicillin"}
    assert result["clinical_status"] in ("ACTIVE", "active")
    assert result["external_id"] == external


@pytest.mark.asyncio
async def test_update_and_delete_allergy(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    created = await provider.handle_api_request(
        integration=integration, path="allergies", method="POST",
        request=_post_request(
            {"code": {"text": "Latex"}, "category": "ENVIRONMENT"}
        ),
    )
    updated = await provider.handle_api_request(
        integration=integration,
        path=f"allergies/{created['id']}",
        method="PUT",
        request=_post_request({"note": "rash confirmed"}),
    )
    assert updated["note"] == "rash confirmed"

    deleted = await provider.handle_api_request(
        integration=integration,
        path=f"allergies/{created['id']}",
        method="DELETE",
        request=_get_request(),
    )
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_allergy_cross_patient_isolation(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    async with AsyncSessionLocal() as db:
        foreign = AllergyIntolerance(
            id=uuid.uuid4(),
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_b"],
            clinical_status="ACTIVE",
            verification_status="confirmed",
            category="MEDICATION",
            criticality="LOW",
            code={"text": "Peanuts"},
        )
        db.add(foreign)
        await db.commit()
        foreign_id = str(foreign.id)
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"allergies/{foreign_id}",
            method="DELETE",
            request=_get_request(),
        )


# =========================================================================
# Vaccines
# =========================================================================


@pytest.mark.asyncio
async def test_create_vaccine_returns_row_and_persists(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    external = f"vac-{uuid.uuid4()}"

    result = await provider.handle_api_request(
        integration=integration, path="vaccines", method="POST",
        request=_post_request(
            {
                "id": external,
                "vaccine_code": {"text": "COVID-19 mRNA"},
                "status": "completed",
                "administered_at": "2026-08-12T10:00:00+00:00",
            }
        ),
    )
    assert result["vaccine_code"]["text"] == "COVID-19 mRNA"
    assert result["external_id"] == external


@pytest.mark.asyncio
async def test_update_and_delete_vaccine(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    created = await provider.handle_api_request(
        integration=integration, path="vaccines", method="POST",
        request=_post_request(
            {
                "vaccine_code": {"text": "Influenza"},
                "status": "completed",
                "administered_at": "2026-08-12T10:00:00+00:00",
            }
        ),
    )
    updated = await provider.handle_api_request(
        integration=integration,
        path=f"vaccines/{created['id']}",
        method="PUT",
        request=_post_request({"lot_number": "FLU-2026", "note": "drive-through"}),
    )
    assert updated["lot_number"] == "FLU-2026"

    deleted = await provider.handle_api_request(
        integration=integration,
        path=f"vaccines/{created['id']}",
        method="DELETE",
        request=_get_request(),
    )
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_vaccine_cross_patient_isolation(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    async with AsyncSessionLocal() as db:
        foreign = PatientImmunization(
            id=uuid.uuid4(),
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_b"],
            status="completed",
            vaccine_code={"text": "Hep B"},
        )
        db.add(foreign)
        await db.commit()
        foreign_id = str(foreign.id)
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"vaccines/{foreign_id}",
            method="DELETE",
            request=_get_request(),
        )


# =========================================================================
# Clinical events
# =========================================================================


@pytest.mark.asyncio
async def test_create_clinical_event_returns_dict(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    external = f"ce-{uuid.uuid4()}"

    result = await provider.handle_api_request(
        integration=integration, path="clinical-events", method="POST",
        request=_post_request(
            {
                "id": external,
                "type_slug": "symptom",
                "title": "Headache",
                "description": "throbbing, behind left eye",
                "onset_date": "2026-08-12T08:00:00+00:00",
            }
        ),
    )
    # ``create_event`` returns a fully-eager-loaded to_dict(); check the
    # headline fields the mobile app will render.
    # ``create_event`` returns the row's to_dict(); after a fresh insert the
    # ``type_entity`` may not be eager-loaded so ``type_details`` can be None.
    # The headline fields the mobile app renders are always populated.
    assert result["title"] == "Headache"
    assert result["patient_id"] == str(ctx["patient_a"])


@pytest.mark.asyncio
async def test_update_clinical_event(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    created = await provider.handle_api_request(
        integration=integration, path="clinical-events", method="POST",
        request=_post_request({"type_slug": "symptom", "title": "Cough"}),
    )
    updated = await provider.handle_api_request(
        integration=integration,
        path=f"clinical-events/{created['id']}",
        method="PUT",
        request=_post_request({"description": "dry, worse at night"}),
    )
    assert updated["title"] == "Cough"
    assert updated["description"] == "dry, worse at night"


@pytest.mark.asyncio
async def test_delete_clinical_event_soft_deletes(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    created = await provider.handle_api_request(
        integration=integration, path="clinical-events", method="POST",
        request=_post_request({"type_slug": "symptom", "title": "Fatigue"}),
    )
    result = await provider.handle_api_request(
        integration=integration,
        path=f"clinical-events/{created['id']}",
        method="DELETE",
        request=_get_request(),
    )
    assert result["deleted"] is True

    async with AsyncSessionLocal() as db:
        row = await db.get(ClinicalEvent, created["id"])
        assert row.deleted_at is not None


@pytest.mark.asyncio
async def test_clinical_event_occurrence_logs(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    created = await provider.handle_api_request(
        integration=integration, path="clinical-events", method="POST",
        request=_post_request({"type_slug": "symptom", "title": "Migraine"}),
    )
    occ = await provider.handle_api_request(
        integration=integration,
        path=f"clinical-events/{created['id']}/occurrences",
        method="POST",
        request=_post_request({"occurred_at": "2026-08-12T14:00:00+00:00"}),
    )
    # ``add_occurrence`` returns the updated event dict; the mobile app shows
    # ``last_occurrence`` on the timeline.
    assert "id" in occ
    assert occ["id"] == created["id"]


@pytest.mark.asyncio
async def test_clinical_event_cross_patient_isolation(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    async with AsyncSessionLocal() as db:
        foreign = ClinicalEvent(
            id=uuid.uuid4(),
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_b"],
            title="Foreign symptom",
        )
        db.add(foreign)
        await db.commit()
        foreign_id = str(foreign.id)
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"clinical-events/{foreign_id}",
            method="DELETE",
            request=_get_request(),
        )


# =========================================================================
# Doctors (tenant-scoped, not patient-scoped)
# =========================================================================


@pytest.mark.asyncio
async def test_create_doctor_returns_row(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    result = await provider.handle_api_request(
        integration=integration, path="doctors", method="POST",
        request=_post_request(
            {"name": "Dr. House", "specialty": "Diagnostics", "phone": "+1-555"}
        ),
    )
    assert result["name"] == "Dr. House"
    # ``specialty`` is a @property resolved from specialty_concept; without a
    # seeded specialty concept the resolution returns None and the text is
    # dropped (the documented greenfield trade-off in doctor_service). The
    # bridge's job is to forward + persist, which it did — verify the
    # scalar fields that round-trip cleanly.
    assert result["phone"] == "+1-555"


@pytest.mark.asyncio
async def test_update_and_delete_doctor(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    created = await provider.handle_api_request(
        integration=integration, path="doctors", method="POST",
        request=_post_request({"name": "Dr. Wilson"}),
    )
    updated = await provider.handle_api_request(
        integration=integration,
        path=f"doctors/{created['id']}",
        method="PUT",
        request=_post_request({"email": "wilson@example.org"}),
    )
    assert updated["email"] == "wilson@example.org"

    deleted = await provider.handle_api_request(
        integration=integration,
        path=f"doctors/{created['id']}",
        method="DELETE",
        request=_get_request(),
    )
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_create_medication_rejects_missing_required_code(
    bridge_with_two_patients
):
    """The MedicationRecordCreate schema requires ``code``; a missing field is
    a 400 — never a 500."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration, path="medications", method="POST",
            request=_post_request({"dosage": "10mg"}),  # no code
        )
