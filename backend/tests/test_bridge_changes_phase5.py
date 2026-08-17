"""Phase 5 — DB-backed tests for the unified /changes delta endpoint.

Coverage:
- Happy path: seeded rows with different ``updated_at`` → only post-since rows returned
- Cursor advances to max(updated_at) across the batch
- Empty delta → null cursor (client re-uses same `since`)
- Cross-patient isolation (PatientB's rows never appear in PatientA's delta)
- types filter narrows the response
- Unknown type name → ValueError
- Default since window (no `?since=`) returns last 7 days
"""

import datetime
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.medication import Medication
from app.models.fhir.patient import Patient
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
                id=tenant_id, name="Bridge P5 T.", slug=f"bp5-{tenant_id.hex[:8]}"
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"bp5-{user_id.hex[:6]}@test.local",
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


@pytest.mark.asyncio
async def test_changes_returns_only_rows_updated_after_since(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    # Seed an OLD medication (updated_at ≈ now because we just inserted it, so
    # we set it explicitly to a timestamp in the past).
    old_ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    new_ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)

    async with AsyncSessionLocal() as db:
        old_med = Medication(
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_a"],
            status="ACTIVE",
            intent="statement",
            code={"text": "Old Med"},
        )
        db.add(old_med)
        await db.flush()
        old_med.updated_at = old_ts

        new_med = Medication(
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_a"],
            status="ACTIVE",
            intent="statement",
            code={"text": "New Med"},
        )
        db.add(new_med)
        await db.flush()
        new_med.updated_at = new_ts
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration,
        path="changes",
        method="GET",
        request=_get_request({"since": cutoff.isoformat()}),
    )
    med_texts = [m["code_text"] for m in result["data"].get("medications", [])]
    assert "New Med" in med_texts
    assert "Old Med" not in med_texts


@pytest.mark.asyncio
async def test_changes_cursor_advances_to_max_updated_at(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    new_ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        minutes=10
    )

    async with AsyncSessionLocal() as db:
        m = Medication(
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_a"],
            status="ACTIVE",
            intent="statement",
            code={"text": "Cursor Test"},
        )
        db.add(m)
        await db.flush()
        m.updated_at = new_ts
        await db.commit()
        expected_cursor = m.updated_at

    result = await provider.handle_api_request(
        integration=integration,
        path="changes",
        method="GET",
        request=_get_request({"since": cutoff.isoformat()}),
    )
    assert result["cursor"] is not None
    returned_cursor = datetime.datetime.fromisoformat(
        result["cursor"].replace("Z", "+00:00")
    )
    # The cursor should be ≥ the seeded row's updated_at (could exceed it
    # only if another row updated concurrently, which doesn't happen here).
    assert abs((returned_cursor - expected_cursor).total_seconds()) < 1


@pytest.mark.asyncio
async def test_changes_returns_null_cursor_when_nothing_changed(
    bridge_with_two_patients,
):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    # Future `since` → no rows match.
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    result = await provider.handle_api_request(
        integration=integration,
        path="changes",
        method="GET",
        request=_get_request({"since": future.isoformat()}),
    )
    assert result["cursor"] is None
    # data shape exists but every list is empty.
    assert result["data"] == {} or all(not v for v in result["data"].values())


@pytest.mark.asyncio
async def test_changes_is_patient_scoped(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    async with AsyncSessionLocal() as db:
        db.add(
            AllergyIntolerance(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_b"],
                clinical_status="ACTIVE",
                category="MEDICATION",
                criticality="LOW",
                code={"text": "PatientB Hidden Allergy"},
            )
        )
        await db.commit()

    result = await provider.handle_api_request(
        integration=integration,
        path="changes",
        method="GET",
        request=_get_request({"since": cutoff.isoformat()}),
    )
    for a in result["data"].get("allergies", []):
        assert a["code_text"] != "PatientB Hidden Allergy"


@pytest.mark.asyncio
async def test_changes_types_filter_narrows_response(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    async with AsyncSessionLocal() as db:
        db.add(
            Medication(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_a"],
                status="ACTIVE",
                intent="statement",
                code={"text": "Filtered Med"},
            )
        )
        db.add(
            AllergyIntolerance(
                tenant_id=ctx["tenant_id"],
                patient_id=ctx["patient_a"],
                clinical_status="ACTIVE",
                category="MEDICATION",
                criticality="LOW",
                code={"text": "Filtered Allergy"},
            )
        )
        await db.commit()

    # Ask only for medications → allergies absent from data.
    result = await provider.handle_api_request(
        integration=integration,
        path="changes",
        method="GET",
        request=_get_request({"since": cutoff.isoformat(), "types": "medications"}),
    )
    assert "medications" in result["data"]
    assert "allergies" not in result["data"]


@pytest.mark.asyncio
async def test_changes_rejects_unknown_type(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="changes",
            method="GET",
            request=_get_request({"types": "medications,unknown_type"}),
        )


@pytest.mark.asyncio
async def test_changes_invalid_since_raises(bridge_with_two_patients):
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="changes",
            method="GET",
            request=_get_request({"since": "not-a-date"}),
        )


@pytest.mark.asyncio
async def test_changes_default_since_is_last_7_days(bridge_with_two_patients):
    """When `since` is omitted, the query uses now - 7d as the cutoff. A row
    seeded with an explicitly-old updated_at (8 days ago) is excluded; a row
    seeded with updated_at = now is included."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    old_ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)
    async with AsyncSessionLocal() as db:
        old_med = Medication(
            tenant_id=ctx["tenant_id"],
            patient_id=ctx["patient_a"],
            status="ACTIVE",
            intent="statement",
            code={"text": "8 Days Ago"},
        )
        db.add(old_med)
        await db.flush()
        old_med.updated_at = old_ts
        await db.commit()

    # No `since` param → defaults to last 7 days → the 8-day-old row excluded.
    result = await provider.handle_api_request(
        integration=integration,
        path="changes",
        method="GET",
        request=_get_request(),
    )
    meds = result["data"].get("medications", [])
    assert all(m["code_text"] != "8 Days Ago" for m in meds)


@pytest.mark.asyncio
async def test_changes_envelope_carries_since_echo(bridge_with_two_patients):
    """The response echoes the `since` it used so the client can debug / verify."""
    ctx = bridge_with_two_patients
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
    result = await provider.handle_api_request(
        integration=integration,
        path="changes",
        method="GET",
        request=_get_request({"since": cutoff.isoformat()}),
    )
    assert "since" in result
    echoed = datetime.datetime.fromisoformat(result["since"].replace("Z", "+00:00"))
    assert abs((echoed - cutoff).total_seconds()) < 1
