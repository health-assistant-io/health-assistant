"""Integration-proposal persistence tests (plan 5: the one proposals table).

Extension proposals are rows in ``proposals`` with ``source='extension'`` and
``source_ref = integration instance id``. These tests pin the persistence +
dedup layer: the versioned card document, the seeded kind FK, tenant-scoped
dedup idempotency, and the integration-scoped read paths.

Real-DB integration tests: each test creates its own tenant + UserIntegration
row so nothing leaks across runs.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.enums import ProposalSource, ProposalStatus
from app.models.fhir.patient import Patient
from app.models.proposal import Proposal
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.services.integration import integration_proposal_service as svc


@pytest_asyncio.fixture
async def integration_row():
    """Create an isolated tenant + user + patient + UserIntegration row.

    Returns ``(tenant_id, user_id, patient_id, integration_id)``.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name="HITL Proposals T.",
                slug=f"hitl-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"hitl-owner-{user_id.hex[:6]}@test.local",
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

    return tenant_id, user_id, patient_id, integration_id


# ---------------------------------------------------------------------------
# compute_dedup_key
# ---------------------------------------------------------------------------


def test_compute_dedup_key_is_order_stable_for_dict_keys():
    """Two payloads with the same keys/values but different insertion order
    must hash to the same key — that's the whole point of canonical JSON."""
    a = svc.compute_dedup_key(
        "create_biomarker_definition",
        {"name": "Sleep Quality", "aliases": ["sq", "sleep_idx"]},
    )
    b = svc.compute_dedup_key(
        "create_biomarker_definition",
        {"aliases": ["sq", "sleep_idx"], "name": "Sleep Quality"},
    )
    assert a == b
    assert a is not None
    assert len(a) == 64


def test_compute_dedup_key_differs_on_list_order():
    a = svc.compute_dedup_key("create_biomarker_definition", {"aliases": ["a", "b"]})
    b = svc.compute_dedup_key("create_biomarker_definition", {"aliases": ["b", "a"]})
    assert a != b


def test_compute_dedup_key_differs_on_kind():
    payload = {"name": "X", "slug": "x"}
    a = svc.compute_dedup_key("create_biomarker_definition", payload)
    b = svc.compute_dedup_key("create_concept", payload)
    assert a != b


# ---------------------------------------------------------------------------
# create_proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_proposal_inserts_proposed_row(integration_row):
    tenant_id, user_id, _patient_id, integration_id = integration_row

    async with AsyncSessionLocal() as db:
        row, created = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="Define Biomarker: Sleep Quality",
            proposed_payload={"name": "Sleep Quality", "slug": "sleep-quality"},
            patient_id=None,
            context={"upstream_source": "test"},
            created_by=user_id,
        )
        await db.commit()

        assert created is True
        assert row.id is not None
        assert row.status == ProposalStatus.PROPOSED
        assert row.source == ProposalSource.EXTENSION
        assert row.source_ref == integration_id
        assert row.kind.code == "create_biomarker_definition"
        assert row.kind.card_variant == "create_biomarker_definition"
        assert row.payload == {
            "title": "Define Biomarker: Sleep Quality",
            "context": {"upstream_source": "test"},
            "proposed_payload": {"name": "Sleep Quality", "slug": "sleep-quality"},
        }
        assert row.payload_schema_version == 1
        assert row.tenant_id == tenant_id
        assert row.user_id == user_id
        assert row.created_by == user_id
        assert row.dedup_key is not None
        assert len(row.dedup_key) == 64
        assert row.expires_at is not None
        assert row.final_payload is None
        assert row.resolved_at is None
        assert row.applied_at is None
        assert row.resolution_trace == {}


@pytest.mark.asyncio
async def test_create_proposal_is_idempotent_on_dedup_key(integration_row):
    tenant_id, _user_id, _patient_id, integration_id = integration_row
    payload = {"name": "Steps", "slug": "steps"}

    async with AsyncSessionLocal() as db:
        first, c1 = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="Define: Steps",
            proposed_payload=payload,
        )
        await db.commit()

        second, c2 = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="Define: Steps (re-emitted)",
            proposed_payload=payload,
        )
        await db.commit()

    assert c1 is True and c2 is False
    assert first.id == second.id
    assert second.payload["title"] == "Define: Steps"
    rows = await _all_extension_proposals(integration_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_dedup_key_is_tenant_scoped_across_integrations(integration_row):
    """Plan 5 moved dedup from per-integration to per-tenant: two integrations
    in one tenant re-emitting the same spec share one row."""
    tenant_id, user_id, patient_id, integration_id = integration_row
    other_integration = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            UserIntegration(
                id=other_integration,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="other_provider",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.commit()

    payload = {"name": "HRV", "slug": "hrv"}
    async with AsyncSessionLocal() as db:
        first, c1 = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="HRV",
            proposed_payload=payload,
        )
        second, c2 = await svc.create_proposal(
            db,
            integration_id=other_integration,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="HRV again",
            proposed_payload=payload,
        )
        await db.commit()
    assert c1 is True and c2 is False
    assert first.id == second.id


@pytest.mark.asyncio
async def test_create_proposal_does_not_insert_after_terminal_decision(integration_row):
    tenant_id, user_id, _patient_id, integration_id = integration_row
    payload = {"name": "HRV", "slug": "hrv"}

    async with AsyncSessionLocal() as db:
        first, _c1 = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="Define: HRV",
            proposed_payload=payload,
            created_by=user_id,
        )
        first.status = ProposalStatus.APPLIED
        first.final_payload = payload
        await db.commit()

        second, _c2 = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="Define: HRV (re-emitted post-decision)",
            proposed_payload=payload,
        )
        await db.commit()

    assert second.id == first.id
    assert second.status == ProposalStatus.APPLIED
    assert len(await _all_extension_proposals(integration_id)) == 1


@pytest.mark.asyncio
async def test_create_proposal_allows_distinct_payloads(integration_row):
    tenant_id, _u, _p, integration_id = integration_row

    async with AsyncSessionLocal() as db:
        a, _ca = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="A",
            proposed_payload={"name": "A", "slug": "a"},
        )
        b, _cb = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="B",
            proposed_payload={"name": "B", "slug": "b"},
        )
        await db.commit()

    assert a.id != b.id
    assert len(await _all_extension_proposals(integration_id)) == 2


# ---------------------------------------------------------------------------
# get / list / count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_proposal_scoped_to_integration(integration_row):
    tenant_id, _u, _p, integration_id = integration_row

    async with AsyncSessionLocal() as db:
        row, _created = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_concept",
            title="X",
            proposed_payload={"slug": "x", "name": "X", "kind": "disease"},
        )
        await db.commit()

        fetched = await svc.get_proposal(db, integration_id=integration_id, proposal_id=row.id)
        assert fetched is not None
        assert fetched.id == row.id

        miss = await svc.get_proposal(db, integration_id=uuid.uuid4(), proposal_id=row.id)
    assert miss is None


@pytest.mark.asyncio
async def test_list_proposals_filters_by_status(integration_row):
    tenant_id, _u, _p, integration_id = integration_row

    async with AsyncSessionLocal() as db:
        proposed_a, _ca = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="A",
            proposed_payload={"name": "A", "slug": "a"},
        )
        proposed_b, _cb = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="B",
            proposed_payload={"name": "B", "slug": "b"},
        )
        rejected, _cd = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="C",
            proposed_payload={"name": "C", "slug": "c"},
        )
        rejected.status = ProposalStatus.REJECTED
        await db.commit()

        only_proposed = await svc.list_proposals(
            db, integration_id=integration_id, status=ProposalStatus.PROPOSED
        )
        all_for_integration = await svc.list_proposals(db, integration_id=integration_id)
        n_proposed = await svc.count_proposals(
            db, integration_id=integration_id, status=ProposalStatus.PROPOSED
        )
        n_total = await svc.count_proposals(db, integration_id=integration_id)

    assert {r.id for r in only_proposed} == {proposed_a.id, proposed_b.id}
    assert len(all_for_integration) == 3
    assert n_proposed == 2
    assert n_total == 3


async def _all_extension_proposals(integration_id) -> list:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Proposal).where(
                Proposal.source == ProposalSource.EXTENSION,
                Proposal.source_ref == integration_id,
            )
        )
        return list(result.scalars().all())
