"""End-to-end API tests for the integration-proposal HITL endpoints (G.2).

Exercises the full HTTP path — auth, ownership scoping, RBAC, resolver
state transitions, and the provider-callback contract. Uses real DB +
``async_client`` so the test database runs through the actual FastAPI
stack (matching the convention in ``test_concept_fk_integration.py``).
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.biomarker_model import BiomarkerDefinition
from app.models.enums import HitlTaskStatus, Role
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.services import integration_proposal_service as proposal_svc
from integrations.sdk.proposals import biomarker_hitl_proposal


@pytest_asyncio.fixture
async def owner_headers_and_integration():
    """Create an isolated tenant + ADMIN owner + patient + integration.

    Returns ``(headers, tenant_id, user_id, integration_id)``. Headers
    carry a real JWT so the auth path runs end-to-end.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name="HITL API T.",
                slug=f"hitlapi-{tenant_id.hex[:8]}",
            )
        )
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

    token = create_access_token(
        {
            "sub": f"hitl-api-{user_id.hex[:6]}@test.local",
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "role": "ADMIN",
        }
    )
    return {"Authorization": f"Bearer {token}"}, tenant_id, user_id, integration_id


@pytest_asyncio.fixture
async def other_user_headers(owner_headers_and_integration):
    """Headers for a different user in a different tenant — used to prove
    the ownership check refuses cross-tenant reads / writes."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id, name="Other T.", slug=f"oth-{tenant_id.hex[:8]}"
            )
        )
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
    token = create_access_token(
        {
            "sub": f"other-{user_id.hex[:6]}@test.local",
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "role": "ADMIN",
        }
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_proposal(integration_id, tenant_id, user_id, spec=None):
    """Insert a PROPOSED proposal directly via the service. Returns the row."""
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
            proposal_type=spec.proposal_type,
            title=spec.title,
            proposed_payload=spec.proposed_payload,
            context=spec.context,
            created_by=user_id,
        )
        await db.commit()
    return row


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_proposals_returns_owner_proposals(
    async_client, owner_headers_and_integration
):
    """The owner can list their integration's proposals; an unrelated user
    gets 404 (the ownership check refuses before any row is read)."""
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    await _seed_proposal(integration_id, tenant_id, user_id)

    resp = await async_client.get(
        f"/api/v1/integrations/instance/{integration_id}/proposals",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["proposal_type"] == "create_biomarker_definition"
    assert data[0]["status"] == HitlTaskStatus.PROPOSED.value


@pytest.mark.asyncio
async def test_list_proposals_status_filter(
    async_client, owner_headers_and_integration
):
    """``?status=proposed`` filters out terminal rows."""
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    a = await _seed_proposal(integration_id, tenant_id, user_id)
    b = await _seed_proposal(integration_id, tenant_id, user_id)
    # Flip b to DISMISSED directly. Re-fetch within the session so the
    # mutation actually persists (b was loaded in _seed_proposal's now-
    # closed session; mutating the detached ORM object wouldn't commit).
    async with AsyncSessionLocal() as db:
        from app.models.integration_proposal import IntegrationProposal

        fresh_b = (
            await db.execute(
                select(IntegrationProposal).where(
                    IntegrationProposal.id == b.id
                )
            )
        ).scalar_one()
        fresh_b.status = HitlTaskStatus.DISMISSED
        await db.commit()

    resp = await async_client.get(
        f"/api/v1/integrations/instance/{integration_id}/proposals",
        headers=headers,
        params={"status": "proposed"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(a.id)


@pytest.mark.asyncio
async def test_list_proposals_rejects_unknown_status(
    async_client, owner_headers_and_integration
):
    """Unknown ``status`` value → 400 (not 500)."""
    headers, _t, _u, integration_id = owner_headers_and_integration
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
    """A user in a different tenant gets 404, not the proposals list."""
    _h, _t, _u, integration_id = owner_headers_and_integration
    resp = await async_client.get(
        f"/api/v1/integrations/instance/{integration_id}/proposals",
        headers=other_user_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Get-one endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_proposal_returns_full_row(
    async_client, owner_headers_and_integration
):
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    resp = await async_client.get(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(row.id)
    assert data["proposed_payload"] == row.proposed_payload


@pytest.mark.asyncio
async def test_get_proposal_404_for_unknown_id(
    async_client, owner_headers_and_integration
):
    headers, *_ = owner_headers_and_integration
    bogus = uuid.uuid4()
    resp = await async_client.get(
        f"/api/v1/integrations/instance/{uuid.uuid4()}/proposals/{bogus}",
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Resolve endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_approve_creates_biomarker_and_marks_confirmed(
    async_client, owner_headers_and_integration
):
    """The canonical happy path: approve → ``catalog_proposal_service.apply_proposal``
    writes the biomarker → status transitions to CONFIRMED with
    ``applied_entity_id`` set."""
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == HitlTaskStatus.CONFIRMED.value
    assert data["resolved_by"] == str(user_id)
    assert data["resolved_at"] is not None
    # The applied biomarker id is surfaced both in resolved_payload and as
    # the top-level applied_entity_id field.
    assert data["applied_entity_id"] is not None
    assert data["resolved_payload"]["_applied_entity_id"] == data["applied_entity_id"]

    # The biomarker actually landed in the DB.
    async with AsyncSessionLocal() as db:
        bio = (
            await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == uuid.UUID(
                        data["applied_entity_id"]
                    )
                )
            )
        ).scalar_one()
    assert bio.tenant_id == tenant_id
    assert bio.created_by == user_id
    assert bio.meta_data == {"_provenance": "integration"}


@pytest.mark.asyncio
async def test_resolve_approve_with_edited_payload(
    async_client, owner_headers_and_integration
):
    """The user can edit the payload before approving — ``payload`` in the
    request body overrides the original ``proposed_payload``."""
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    row = await _seed_proposal(integration_id, tenant_id, user_id)
    original_name = row.proposed_payload["name"]
    edited_name = f"{original_name} (edited)"

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={
            "action": "approve",
            "payload": {**row.proposed_payload, "name": edited_name},
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    async with AsyncSessionLocal() as db:
        bio = (
            await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == uuid.UUID(
                        data["applied_entity_id"]
                    )
                )
            )
        ).scalar_one()
    assert bio.name == edited_name


@pytest.mark.asyncio
async def test_resolve_reject_marks_dismissed_without_applying(
    async_client, owner_headers_and_integration
):
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    row = await _seed_proposal(integration_id, tenant_id, user_id)
    # Capture the proposed payload so we can verify nothing landed.
    proposed_slug = row.proposed_payload.get("slug")

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "reject", "note": "duplicate"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == HitlTaskStatus.DISMISSED.value
    assert data["resolution_note"] == "duplicate"
    assert data["applied_entity_id"] is None

    # No biomarker landed.
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.slug == proposed_slug
                )
            )
        ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_resolve_cancel_marks_dismissed(
    async_client, owner_headers_and_integration
):
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "cancel"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == HitlTaskStatus.DISMISSED.value


@pytest.mark.asyncio
async def test_resolve_on_terminal_proposal_returns_409(
    async_client, owner_headers_and_integration
):
    """Re-resolve from a terminal state must return 409 — idempotent
    contract so the UI doesn't let a user double-approve."""
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
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
async def test_resolve_user_role_forbidden(
    async_client, owner_headers_and_integration
):
    """USER role can list + view but not approve catalog proposals (which
    require ADMIN+ under the catalog policy)."""
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    user_token = create_access_token(
        {
            "sub": "user-role@test.local",
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "role": Role.USER.value,
        }
    )
    user_headers = {"Authorization": f"Bearer {user_token}"}

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=user_headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 403
    # The proposal row was not mutated.
    async with AsyncSessionLocal() as db:
        fresh = await proposal_svc.get_proposal(
            db, integration_id=integration_id, proposal_id=row.id
        )
    assert fresh.status == HitlTaskStatus.PROPOSED


@pytest.mark.asyncio
async def test_resolve_records_failed_when_apply_errors(
    async_client, owner_headers_and_integration
):
    """When apply_proposal raises (e.g. a permission error), the resolver
    records status=FAILED + an error message rather than 500'ing. The
    proposal stays around for retry / re-review."""
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    # Build a proposal with an empty name → apply_proposal will raise
    # ValueError before any DB write (biomarker router's first check).
    async with AsyncSessionLocal() as db:
        row, _c = await proposal_svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="bad proposal",
            proposed_payload={"name": ""},  # invalid → apply raises
            created_by=user_id,
        )
        await db.commit()

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == HitlTaskStatus.FAILED.value
    assert data["error"] is not None
    assert "non-empty 'name'" in data["error"]


@pytest.mark.asyncio
async def test_resolve_unknown_action_returns_400(
    async_client, owner_headers_and_integration
):
    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    row = await _seed_proposal(integration_id, tenant_id, user_id)

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "frob"},
    )
    # Pydantic's Literal validation rejects unknown action values. The
    # endpoint constructs IntegrationProposalResolveRequest manually and
    # catches the ValidationError → 400.
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resolve_medication_proposal_writes_catalog(
    async_client, owner_headers_and_integration
):
    """Non-biomarker kinds route through apply_proposal too — verify with
    a medication proposal."""
    from integrations.sdk.proposals import medication_hitl_proposal

    headers, tenant_id, user_id, integration_id = (
        owner_headers_and_integration
    )
    spec = medication_hitl_proposal(
        title=f"Define Med: TestMol-{uuid.uuid4().hex[:6]}",
        name=f"TestMol-{uuid.uuid4().hex[:8]}",
    )
    async with AsyncSessionLocal() as db:
        row, _c = await proposal_svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type=spec.proposal_type,
            title=spec.title,
            proposed_payload=spec.proposed_payload,
            created_by=user_id,
        )
        await db.commit()

    resp = await async_client.post(
        f"/api/v1/integrations/instance/{integration_id}/proposals/{row.id}/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == HitlTaskStatus.CONFIRMED.value
    assert data["applied_entity_id"] is not None

    async with AsyncSessionLocal() as db:
        med = (
            await db.execute(
                select(MedicationCatalog).where(
                    MedicationCatalog.id == uuid.UUID(
                        data["applied_entity_id"]
                    )
                )
            )
        ).scalar_one()
    assert med.tenant_id == tenant_id
    assert med.created_by == user_id
