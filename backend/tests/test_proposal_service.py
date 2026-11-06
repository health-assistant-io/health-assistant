"""The one HITL proposal store — persistence behavior (plan 5 W1/W2/W4).

Covers the chat upsert path (proactive save then final save = one row per
card), card serialization (ADR-0015 wire shape), final-payload validation
(the trust boundary), the expiry sweep, and supersession.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.converters import utcnow
from app.core.database import AsyncSessionLocal
from app.models.chat_model import ChatSession
from app.models.enums import ProposalSource, ProposalStatus
from app.models.proposal import Proposal
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.chat.chat_session_service import ChatSessionService
from app.services.proposals import proposal_service


@pytest_asyncio.fixture
async def chat_scope():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Proposal T.", slug=f"prop-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"prop-{user_id.hex[:6]}@test.local",
                password_hash="x",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(ChatSession(id=session_id, tenant_id=tenant_id, user_id=user_id, title="t"))
        await db.commit()
    try:
        yield {"tenant_id": tenant_id, "user_id": user_id, "session_id": session_id}
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                ChatSession.__table__.delete().where(ChatSession.__table__.c.id == session_id)
            )
            await db.execute(
                UserModel.__table__.delete().where(UserModel.__table__.c.id == user_id)
            )
            await db.execute(
                TenantModel.__table__.delete().where(TenantModel.__table__.c.id == tenant_id)
            )
            await db.commit()


def _card(task_type="add_medication", title="Add Medication", status="proposed", **payload):
    return {
        "schema_version": 1,
        "proposal_id": str(uuid.uuid4()),
        "task_type": task_type,
        "title": title,
        "status": status,
        "proposed_payload": {"code": {"text": "Metformin"}, **payload},
        "context": {"patient_id": None},
    }


# ---------------------------------------------------------------------------
# chat upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_upsert_is_idempotent_across_proactive_and_final_save(chat_scope):
    card = _card()
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        message = await svc.save_message(
            session_id=chat_scope["session_id"],
            role="assistant",
            content={"text": ""},
            tasks=[card],
        )
        # The graph's final save patches the same card set.
        await svc.update_message_fields(
            message,
            content={"text": "final answer"},
            tasks=[{**card, "title": "Add Medication (final)"}],
        )
        rows = (
            (
                await db.execute(
                    select(Proposal).where(
                        Proposal.source == ProposalSource.CHAT,
                        Proposal.source_ref == message.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        await db.commit()

    assert len(rows) == 1
    assert rows[0].id == uuid.UUID(card["proposal_id"])
    assert rows[0].ordinal == 0
    assert rows[0].payload["title"] == "Add Medication (final)"
    assert rows[0].status == ProposalStatus.PROPOSED
    assert rows[0].tenant_id == chat_scope["tenant_id"]
    assert rows[0].user_id == chat_scope["user_id"]


@pytest.mark.asyncio
async def test_chat_upsert_preserves_card_order(chat_scope):
    cards = [_card(title="One"), _card(title="Two"), _card(title="Three")]
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        message = await svc.save_message(
            session_id=chat_scope["session_id"],
            role="assistant",
            content={"text": ""},
            tasks=cards,
        )
        rows = (
            (
                await db.execute(
                    select(Proposal)
                    .where(Proposal.source_ref == message.id)
                    .order_by(Proposal.ordinal)
                )
            )
            .scalars()
            .all()
        )
        await db.commit()
    assert [row.ordinal for row in rows] == [0, 1, 2]
    assert [row.payload["title"] for row in rows] == ["One", "Two", "Three"]


@pytest.mark.asyncio
async def test_later_save_does_not_reset_a_resolved_card(chat_scope):
    card = _card()
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        message = await svc.save_message(
            session_id=chat_scope["session_id"],
            role="assistant",
            content={"text": ""},
            tasks=[card],
        )
        row = (
            await db.execute(select(Proposal).where(Proposal.id == uuid.UUID(card["proposal_id"])))
        ).scalar_one()
        row.status = ProposalStatus.APPLIED
        await db.commit()

        await svc.update_message_fields(message, tasks=[card])
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(Proposal).where(Proposal.id == uuid.UUID(card["proposal_id"])))
        ).scalar_one()
    assert row.status == ProposalStatus.APPLIED


@pytest.mark.asyncio
async def test_unknown_card_variant_is_a_typed_error(chat_scope):
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        with pytest.raises(proposal_service.UnknownProposalKind):
            await svc.save_message(
                session_id=chat_scope["session_id"],
                role="assistant",
                content={"text": ""},
                tasks=[_card(task_type="not_a_kind")],
            )


# ---------------------------------------------------------------------------
# card serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_card_dict_matches_the_adr_0015_wire_shape(chat_scope):
    card = _card(task_type="create_clinical_event", title="Pregnancy")
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        message = await svc.save_message(
            session_id=chat_scope["session_id"],
            role="assistant",
            content={"text": ""},
            tasks=[card],
        )
        row = (
            await db.execute(select(Proposal).where(Proposal.source_ref == message.id))
        ).scalar_one()
        wire = proposal_service.card_dict(row)

    assert set(wire) == {
        "schema_version",
        "proposal_id",
        "task_type",
        "title",
        "status",
        "proposed_payload",
        "context",
        "created_at",
        "resolved",
    }
    assert wire["task_type"] == "create_clinical_event", (
        "the wire keeps the ADR-0015 card key, not the kind id"
    )
    assert wire["status"] == "proposed"
    assert wire["proposed_payload"] == {"code": {"text": "Metformin"}}
    assert wire["resolved"] is None


@pytest.mark.asyncio
async def test_card_dict_carries_the_resolution_block(chat_scope):
    card = _card()
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        message = await svc.save_message(
            session_id=chat_scope["session_id"],
            role="assistant",
            content={"text": ""},
            tasks=[card],
        )
        row = (
            await db.execute(select(Proposal).where(Proposal.source_ref == message.id))
        ).scalar_one()
        row.status = ProposalStatus.APPLIED
        row.final_payload = {"code": {"text": "Metformin"}, "dosage": "500 mg"}
        row.resolution_trace = {
            "resolved_by": str(chat_scope["user_id"]),
            "result": {"id": "med-1"},
            "at": "2026-10-21T12:00:00+00:00",
            "error": None,
        }
        await db.commit()
        wire = proposal_service.card_dict(row)

    assert wire["status"] == "confirmed"
    assert wire["resolved"]["final_payload"]["dosage"] == "500 mg"
    assert wire["resolved"]["result"] == {"id": "med-1"}
    assert wire["resolved"]["confirmed_by"] == str(chat_scope["user_id"])


# ---------------------------------------------------------------------------
# final_payload validation (trust boundary)
# ---------------------------------------------------------------------------


def test_validate_final_payload_rejects_unknown_fields_for_patient_record_kinds():
    with pytest.raises(proposal_service.ProposalPayloadInvalid):
        proposal_service.validate_final_payload(
            "add_medication",
            {"code": {"text": "X"}, "unexpected": "field"},
        )


def test_validate_final_payload_rejects_missing_required_value_shape():
    with pytest.raises(proposal_service.ProposalPayloadInvalid):
        proposal_service.validate_final_payload(
            "add_biomarker_to_examination",
            {"biomarker_id": str(uuid.uuid4()), "code": {"text": "X"}},
        )


def test_validate_final_payload_normalizes_to_json():
    got = proposal_service.validate_final_payload(
        "add_medication",
        {"status": "active", "code": {"text": "Metformin"}},
    )
    assert got["status"] == "ACTIVE"
    assert isinstance(got["code"], dict)


def test_validate_final_payload_rejects_unknown_kind():
    with pytest.raises(proposal_service.UnknownProposalKind):
        proposal_service.validate_final_payload("no_such_kind", {})


# ---------------------------------------------------------------------------
# expiry + supersession
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_sweep_expires_only_past_due_proposed_rows(chat_scope):
    tenant_id = chat_scope["tenant_id"]
    now = utcnow()
    async with AsyncSessionLocal() as db:
        stale, _ = await proposal_service.create_proposal(
            db,
            tenant_id=tenant_id,
            kind_code="create_event",
            source="automation",
            source_ref=None,
            proposed_payload={"title": "Old"},
            expires_at=now - timedelta(minutes=1),
        )
        fresh, _ = await proposal_service.create_proposal(
            db,
            tenant_id=tenant_id,
            kind_code="create_event",
            source="automation",
            source_ref=None,
            proposed_payload={"title": "Fresh"},
            expires_at=now + timedelta(days=1),
        )
        applied, _ = await proposal_service.create_proposal(
            db,
            tenant_id=tenant_id,
            kind_code="create_event",
            source="automation",
            source_ref=None,
            proposed_payload={"title": "Done"},
            expires_at=now - timedelta(minutes=1),
        )
        applied.status = ProposalStatus.APPLIED
        await db.commit()

        count = await proposal_service.expire_stale_proposals(db, now=now)
        await db.commit()

        await db.refresh(stale)
        await db.refresh(fresh)
        await db.refresh(applied)

    assert count == 1
    assert stale.status == ProposalStatus.EXPIRED
    assert stale.resolved_at is not None
    assert stale.resolution_trace["action"] == "expire"
    assert fresh.status == ProposalStatus.PROPOSED
    assert applied.status == ProposalStatus.APPLIED


@pytest.mark.asyncio
async def test_supersede_marks_terminal_and_links_the_replacement(chat_scope):
    tenant_id = chat_scope["tenant_id"]
    async with AsyncSessionLocal() as db:
        row, _ = await proposal_service.create_proposal(
            db,
            tenant_id=tenant_id,
            kind_code="create_event",
            source="automation",
            source_ref=None,
            proposed_payload={"title": "Old"},
        )
        replacement, _ = await proposal_service.create_proposal(
            db,
            tenant_id=tenant_id,
            kind_code="create_event",
            source="automation",
            source_ref=None,
            proposed_payload={"title": "New"},
        )
        await db.commit()

        await proposal_service.supersede_proposal(
            db, row, by_id=replacement.id, actor_id=chat_scope["user_id"], note="replaced"
        )
        await db.commit()
        await db.refresh(row)

    assert row.status == ProposalStatus.SUPERSEDED
    assert row.superseded_by == replacement.id
    assert row.resolution_trace["note"] == "replaced"
