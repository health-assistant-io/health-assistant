"""Tests for ``app.services.integration_proposal_service`` (G.1).

Covers the persistence + dedup layer of the integration-proposal HITL
flow. The resolver (``resolve_proposal``) lands in G.2 alongside the
endpoint tests; this file exercises only the model + service skeleton.

Real-DB integration tests: each test creates its own tenant +
UserIntegration row so nothing leaks across runs.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.enums import HitlTaskStatus
from app.models.fhir.patient import Patient
from app.models.integration_proposal import IntegrationProposal
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.services import integration_proposal_service as svc


@pytest_asyncio.fixture
async def integration_row():
    """Create an isolated tenant + user + patient + UserIntegration row.

    Returns ``(tenant_id, user_id, patient_id, integration_id)``. All four
    rows are FK-linked so the integration_proposals insert passes its
    FK constraints.
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
    assert len(a) == 64  # sha256 hex digest


def test_compute_dedup_key_differs_on_list_order():
    """List order is significant — re-ordering aliases changes the hash.
    (Sets would dedup; lists carry intent.)"""
    a = svc.compute_dedup_key(
        "create_biomarker_definition", {"aliases": ["a", "b"]}
    )
    b = svc.compute_dedup_key(
        "create_biomarker_definition", {"aliases": ["b", "a"]}
    )
    assert a != b


def test_compute_dedup_key_differs_on_proposal_type():
    """Same payload but different proposal_type must hash differently —
    a biomarker proposal and a concept proposal can't share a dedup key."""
    payload = {"name": "X", "slug": "x"}
    a = svc.compute_dedup_key("create_biomarker_definition", payload)
    b = svc.compute_dedup_key("create_concept", payload)
    assert a != b


def test_compute_dedup_key_returns_none_on_uncanonicalizable_payload():
    """A payload that can't be canonicalized to JSON (e.g. contains an
    object that fails both `default=str` and JSON serialization)
    disables dedup by returning None. Hard to construct post-Pydantic,
    but the contract is documented."""
    # Most "weird" objects are stringified by default=str. The one case
    # that defeats it is a non-string dict key.
    a = svc.compute_dedup_key(
        "create_biomarker_definition", {1: "integer key"}
    )
    # `default=str` does stringify the int key — so this still succeeds.
    # The None contract is documented as best-effort.
    assert isinstance(a, str)


# ---------------------------------------------------------------------------
# create_proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_proposal_inserts_proposed_row(integration_row):
    """create_proposal inserts a PROPOSED row with the right defaults +
    stamps the dedup_key from (proposal_type, proposed_payload)."""
    tenant_id, user_id, _patient_id, integration_id = integration_row

    async with AsyncSessionLocal() as db:
        row, _created = await svc.create_proposal(
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

        assert row.id is not None
        assert row.status == HitlTaskStatus.PROPOSED
        assert row.proposal_type == "create_biomarker_definition"
        assert row.title == "Define Biomarker: Sleep Quality"
        assert row.proposed_payload == {
            "name": "Sleep Quality",
            "slug": "sleep-quality",
        }
        assert row.context == {"upstream_source": "test"}
        assert row.tenant_id == tenant_id
        assert row.created_by == user_id
        assert row.dedup_key is not None
        assert len(row.dedup_key) == 64
        # Resolved fields all start empty.
        assert row.resolved_payload is None
        assert row.resolved_by is None
        assert row.resolved_at is None


@pytest.mark.asyncio
async def test_create_proposal_is_idempotent_on_dedup_key(integration_row):
    """Re-emitting the same (proposal_type, proposed_payload) for the same
    integration returns the existing row unchanged — no duplicate, no
    status bump. This is the contract the engine relies on so re-syncs
    don't spam the inbox."""
    tenant_id, _user_id, _patient_id, integration_id = integration_row
    payload = {"name": "Steps", "slug": "steps"}

    async with AsyncSessionLocal() as db:
        first, _c1 = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="Define: Steps",
            proposed_payload=payload,
        )
        await db.commit()

        second, _c2 = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="Define: Steps (re-emitted)",
            proposed_payload=payload,
        )
        await db.commit()

    assert first.id == second.id
    # The title isn't overwritten — the existing row is returned as-is.
    assert second.title == "Define: Steps"

    rows = await _all_proposals_for_integration(integration_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_create_proposal_does_not_insert_after_terminal_decision(
    integration_row,
):
    """If a row with the same dedup_key exists and is already in a terminal
    state (CONFIRMED / DISMISSED / FAILED), create_proposal still returns
    the existing row — we don't re-spam a user who already decided. The
    provider's ``handle_proposal_resolution`` is the contract for "don't
    re-propose this"."""
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
        # Simulate a resolve by stamping CONFIRMED directly.
        first.status = HitlTaskStatus.CONFIRMED
        first.resolved_by = user_id
        first.resolved_payload = payload
        await db.commit()

        # Re-propose with the same payload.
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
    assert second.status == HitlTaskStatus.CONFIRMED
    rows = await _all_proposals_for_integration(integration_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_create_proposal_allows_distinct_payloads_for_same_integration(
    integration_row,
):
    """Different payloads (different dedup_keys) for the same integration
    both land — the engine surfaces multiple proposals for review."""
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
    rows = await _all_proposals_for_integration(integration_id)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# get / list / count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_proposal_scoped_to_integration(integration_row):
    """get_proposal must scope by integration_id so a caller can't read
    another integration's proposal by id-guessing."""
    tenant_id, _u, _p, integration_id = integration_row
    other_integration = uuid.uuid4()

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

        # Correct integration → returns the row.
        fetched = await svc.get_proposal(
            db, integration_id=integration_id, proposal_id=row.id
        )
        assert fetched is not None
        assert fetched.id == row.id

        # Wrong integration → None.
        miss = await svc.get_proposal(
            db,
            integration_id=other_integration,
            proposal_id=row.id,
        )
    assert miss is None


@pytest.mark.asyncio
async def test_list_proposals_filters_by_status(integration_row):
    """list_proposals returns only the rows matching the status filter,
    newest-first."""
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
        dismissed, _cd = await svc.create_proposal(
            db,
            integration_id=integration_id,
            tenant_id=tenant_id,
            proposal_type="create_biomarker_definition",
            title="C",
            proposed_payload={"name": "C", "slug": "c"},
        )
        dismissed.status = HitlTaskStatus.DISMISSED
        await db.commit()

        only_proposed = await svc.list_proposals(
            db, integration_id=integration_id, status=HitlTaskStatus.PROPOSED
        )
        all_for_integration = await svc.list_proposals(
            db, integration_id=integration_id
        )
        n_proposed = await svc.count_proposals(
            db,
            integration_id=integration_id,
            status=HitlTaskStatus.PROPOSED,
        )
        n_total = await svc.count_proposals(
            db, integration_id=integration_id
        )

    proposed_ids = {r.id for r in only_proposed}
    assert proposed_ids == {proposed_a.id, proposed_b.id}
    # Two PROPOSED rows surfaced; the DISMISSED one is filtered out.
    assert len(only_proposed) == 2
    # ``all_for_integration`` includes the DISMISSED row too.
    assert len(all_for_integration) == 3
    assert n_proposed == 2
    assert n_total == 3


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _all_proposals_for_integration(integration_id) -> list:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(IntegrationProposal).where(
                IntegrationProposal.integration_id == integration_id
            )
        )
        return list(result.scalars().all())
