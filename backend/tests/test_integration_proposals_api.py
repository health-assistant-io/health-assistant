"""End-to-end API tests for the extension HITL-proposal endpoints (plan 5).

Exercises the full HTTP path — auth, ownership scoping, RBAC, the unified state
machine (idempotent resolve), contract-payload validation before any write, and
the server-side resolver dispatch for catalog + patient-record kinds.

Uses the real DB + ``async_client`` so the test database runs through the actual
FastAPI stack.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.extensions.sdk.proposals import biomarker_hitl_proposal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.enums import ProposalStatus, Role
from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.proposal import Proposal
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.services.integration import integration_proposal_service as proposal_svc
from app.services.tenancy.user_service import get_user_by_id
from tests._auth_helpers import auth_headers


@pytest_asyncio.fixture
async def owner_headers_and_integration():
    """Create an isolated tenant + ADMIN owner + patient + integration."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="HITL API T.", slug=f"hitlapi-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"hitl-api-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        from app.models.fhir.patient import Patient

        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["HITL"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="test_provider",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.commit()

    user_row = await get_user_by_id(user_id)
    headers = await auth_headers(user_row)
    return headers, tenant_id, user_id, patient_id, integration_id


@pytest_asyncio.fixture
async def other_user_headers(owner_headers_and_integration):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Other T.", slug=f"oth-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"other-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.commit()
    return await auth_headers(await get_user_by_id(user_id))


async def _seed_proposal(integration_id, tenant_id, user_id, spec=None, **kwargs):
    if spec is None:
        spec = biomarker_hitl_proposal(
            title=f"Define: TestBiomarker-{uuid.uuid4().hex[:6]}",
            name=f"TestBiomarker {uuid.uuid4().hex[:6]}",
            slug=f"test-biomarker-{uuid.uuid4().hex[:8]}",
        )
    async with AsyncSessionLocal() as db:
        row, _created = await proposal_svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type=kwargs.get("proposal_type", spec.proposal_type),
            title=kwargs.get("title", spec.title),
            proposed_payload=kwargs.get("proposed_payload", spec.proposed_payload),
            context=kwargs.get("context", spec.context),
            patient_id=kwargs.get("patient_id", spec.patient_id),
            created_by=user_id,
        )
        await db.commit()
    return row


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_proposals_returns_owner_proposals(async_client, owner_headers_and_integration):
    headers, tenant_id, user_id, _patient_id, integration_id = owner_headers_and_integration
    await _seed_proposal(integration_id, tenant_id, user_id)

    resp = await async_client.get(
        f"/api/v1/integrations/instance/{integration_id}/proposals", headers=headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["kind"] == "create_biomarker_definition"
    assert data[0]["card_variant"] == "create_biomarker_definition"
    assert data[0]["status"] == ProposalStatus.PROPOSED.value
    assert data[0]["source"] == "extension"
    assert data[0]["payload"]["proposed_payload"]["slug"]


@pytest.mark.asyncio
async def test_list_proposals_status_filter(async_client, owner_headers_and_integration):
    headers, tenant_id, user_id, _patient_id, integration_id = owner_headers_and_integration
    a = await _seed_proposal(integration_id, tenant_id, user_id)
    b = await _seed_proposal(integration_id, tenant_id, user_id)
    async with AsyncSessionLocal() as db:
        fresh_b = (await db.execute(select(Proposal).where(Proposal.id == b.id))).scalar_one()
        fresh_b.status = ProposalStatus.REJECTED
        await db.commit()

    resp = await async_client.get(
        f"/api/v1/integrations/instance/{integration_id}/proposals",
        headers=headers,
        params={"status": "proposed"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert [row["id"] for row in data] == [str(a.id)]


@pytest.mark.asyncio
async def test_list_proposals_rejects_unknown_status(async_client, owner_headers_and_integration):
    headers, _t, _u, _p, integration_id = owner_headers_and_integration
    resp = await async_client.get(
        f"/api/v1/integrations/instance/{integration_id}/proposals",
        headers=headers,
        params={"status": "not_a_status"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_proposals_404_for_non_owner(
    async_client, owner_headers_and_integration, other_user_headers
):
    _h, _t, _u, _p, integration_id = owner_headers_and_integration
    resp = await async_client.get(
        f"/api/v1/integrations/instance/{integration_id}/proposals",
        headers=other_user_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Get-one endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_proposal_returns_full_row(async_client, owner_headers_and_integration):
    headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    resp = await async_client.get(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(row.id)
    assert data["payload"] == row.payload
    assert data["payload_schema_version"] == 1


@pytest.mark.asyncio
async def test_get_proposal_404_for_unknown_id(async_client, owner_headers_and_integration):
    headers, *_rest = owner_headers_and_integration
    resp = await async_client.get(
        f"/api/v1/integrations/instance/{uuid.uuid4()}/proposals/{uuid.uuid4()}",
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Resolve endpoint — catalog kinds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_approve_creates_biomarker(async_client, owner_headers_and_integration):
    headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == ProposalStatus.APPLIED.value
    assert data["applied_at"] is not None
    assert data["resolution_trace"]["resolved_by"] == str(user_id)
    assert data["applied_entity_id"] is not None
    assert data["resolution_trace"]["result"]["entity_id"] == data["applied_entity_id"]

    async with AsyncSessionLocal() as db:
        bio = (
            await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == uuid.UUID(data["applied_entity_id"])
                )
            )
        ).scalar_one()
    assert bio.tenant_id == tenant_id
    assert bio.created_by == user_id
    assert bio.meta_data == {"_provenance": "integration"}


@pytest.mark.asyncio
async def test_resolve_is_idempotent_for_the_same_outcome(
    async_client, owner_headers_and_integration
):
    """Plan 5 W4: re-resolving an applied proposal returns the stored outcome
    (200) — 409 is reserved for a conflicting decision."""
    headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    first = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert first.status_code == 200
    first_data = first.json()

    second = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert second.status_code == 200, second.text
    second_data = second.json()
    assert second_data["status"] == first_data["status"]
    assert second_data["applied_entity_id"] == first_data["applied_entity_id"]

    async with AsyncSessionLocal() as db:
        count = len(
            (
                await db.execute(
                    select(BiomarkerDefinition).where(
                        BiomarkerDefinition.slug
                        == first_data["payload"]["proposed_payload"]["slug"]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert count == 1, "the idempotent repeat must not write a second biomarker"


@pytest.mark.asyncio
async def test_resolve_conflicting_decision_returns_409(
    async_client, owner_headers_and_integration
):
    headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    first = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert first.status_code == 200

    second = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "reject"},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_resolve_approve_with_edited_payload(async_client, owner_headers_and_integration):
    headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(integration_id, tenant_id, user_id)
    original_name = row.payload["proposed_payload"]["name"]
    edited_name = f"{original_name} (edited)"

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={
            "action": "approve",
            "payload": {**row.payload["proposed_payload"], "name": edited_name},
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["final_payload"]["name"] == edited_name

    async with AsyncSessionLocal() as db:
        bio = (
            await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == uuid.UUID(data["applied_entity_id"])
                )
            )
        ).scalar_one()
    assert bio.name == edited_name


@pytest.mark.asyncio
async def test_resolve_reject_marks_rejected_without_applying(
    async_client, owner_headers_and_integration
):
    headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(integration_id, tenant_id, user_id)
    proposed_slug = row.payload["proposed_payload"].get("slug")

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "reject", "note": "duplicate"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == ProposalStatus.REJECTED.value
    assert data["resolution_trace"]["note"] == "duplicate"
    assert data["applied_entity_id"] is None

    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(BiomarkerDefinition).where(BiomarkerDefinition.slug == proposed_slug)
                )
            )
            .scalars()
            .all()
        )
    assert rows == []


@pytest.mark.asyncio
async def test_resolve_user_role_forbidden(async_client, owner_headers_and_integration):
    _headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    user_headers = await auth_headers(
        await get_user_by_id(user_id), extra_claims={"role": Role.USER.value}
    )
    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=user_headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 403
    async with AsyncSessionLocal() as db:
        fresh = await proposal_svc.get_proposal(
            db, integration_id=integration_id, proposal_id=row.id
        )
    assert fresh.status == ProposalStatus.PROPOSED


@pytest.mark.asyncio
async def test_resolve_records_failed_when_apply_errors(
    async_client, owner_headers_and_integration
):
    headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(
        integration_id,
        tenant_id,
        user_id,
        proposal_type="create_biomarker_definition",
        title="bad proposal",
        proposed_payload={"name": ""},
    )

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 400, resp.text
    assert "name" in resp.text.lower()

    async with AsyncSessionLocal() as db:
        fresh = await proposal_svc.get_proposal(
            db, integration_id=integration_id, proposal_id=row.id
        )
    assert fresh.status == ProposalStatus.PROPOSED, (
        "a payload rejected by the kind contract must never reach a write or a "
        "state transition (trust boundary)"
    )


@pytest.mark.asyncio
async def test_resolve_unknown_action_returns_400(async_client, owner_headers_and_integration):
    headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(integration_id, tenant_id, user_id)
    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "frob"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resolve_medication_definition_writes_catalog(
    async_client, owner_headers_and_integration
):
    from app.extensions.sdk.proposals import medication_hitl_proposal

    headers, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    spec = medication_hitl_proposal(
        title=f"Define Med: TestMol-{uuid.uuid4().hex[:6]}",
        name=f"TestMol-{uuid.uuid4().hex[:8]}",
    )
    row = await _seed_proposal(integration_id, tenant_id, user_id, spec=spec)

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == ProposalStatus.APPLIED.value
    assert data["applied_entity_id"] is not None

    async with AsyncSessionLocal() as db:
        med = (
            await db.execute(
                select(MedicationCatalog).where(
                    MedicationCatalog.id == uuid.UUID(data["applied_entity_id"])
                )
            )
        ).scalar_one()
    assert med.tenant_id == tenant_id
    assert med.created_by == user_id


# ---------------------------------------------------------------------------
# Resolve endpoint — patient-record kinds (plan 5 W3)
# ---------------------------------------------------------------------------


def _medication_payload(name: str) -> dict:
    return {
        "status": "active",
        "code": {"text": name},
        "dosage": "500 mg",
        "frequency": {"type": "daily", "frequency": 1, "period": 1, "period_unit": "day"},
        "reason": "test indication",
    }


@pytest.mark.asyncio
async def test_resolve_add_medication_writes_patient_record(
    async_client, owner_headers_and_integration
):
    headers, tenant_id, user_id, patient_id, integration_id = owner_headers_and_integration
    row = await _seed_proposal(
        integration_id,
        tenant_id,
        user_id,
        proposal_type="add_medication",
        title="Add Medication",
        proposed_payload=_medication_payload("Metformin"),
        patient_id=patient_id,
    )

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == ProposalStatus.APPLIED.value
    assert data["applied_entity_id"] is not None
    assert data["final_payload"]["patient_id"] == str(patient_id)

    async with AsyncSessionLocal() as db:
        medication = (
            await db.execute(
                select(Medication).where(Medication.id == uuid.UUID(data["applied_entity_id"]))
            )
        ).scalar_one()
    assert medication.patient_id == patient_id
    assert medication.tenant_id == tenant_id
    assert medication.code["text"] == "Metformin"


@pytest.mark.asyncio
async def test_adversarial_final_payload_is_rejected_before_any_write(
    async_client, owner_headers_and_integration
):
    """A malformed/unknown-field payload fails the kind contract before the
    service is ever called — no row, no status change."""
    headers, tenant_id, user_id, patient_id, integration_id = owner_headers_and_integration
    row = await _seed_proposal(
        integration_id,
        tenant_id,
        user_id,
        proposal_type="add_medication",
        title="Add Medication",
        proposed_payload=_medication_payload("BadMol"),
        patient_id=patient_id,
    )

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={
            "action": "approve",
            "payload": {**_medication_payload("BadMol"), "patient_id": str(uuid.uuid4())},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "patient" in resp.text.lower()

    async with AsyncSessionLocal() as db:
        fresh = await proposal_svc.get_proposal(
            db, integration_id=integration_id, proposal_id=row.id
        )
        meds = (
            (await db.execute(select(Medication).where(Medication.patient_id == patient_id)))
            .scalars()
            .all()
        )
    assert fresh.status == ProposalStatus.PROPOSED
    assert meds == []


@pytest.mark.asyncio
async def test_resolve_proposal_from_another_tenant_is_unresolvable(
    async_client, owner_headers_and_integration, other_user_headers
):
    _h, tenant_id, user_id, _p, integration_id = owner_headers_and_integration
    row = await _seed_proposal(integration_id, tenant_id, user_id)
    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=other_user_headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 404
    async with AsyncSessionLocal() as db:
        fresh = await proposal_svc.get_proposal(
            db, integration_id=integration_id, proposal_id=row.id
        )
    assert fresh.status == ProposalStatus.PROPOSED
