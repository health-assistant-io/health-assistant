"""The one HITL proposal persistence service (plan 5 W1/W2/W4).

All proposal rows — chat HITL cards, extension ``pull_hitl_proposals`` specs,
automation jobs — are created, read, serialized to the ADR-0015 card shape,
and expired here. The DB shape is one table (``proposals``); the origin is
``source`` + ``source_ref`` + ``ordinal``, so a message's cards are a query,
never a JSONB array column.

The card document stored in ``payload`` is versioned by
``payload_schema_version`` (JSONB doctrine) and shaped:

.. code-block:: json

    {"title": "...", "context": {...}, "proposed_payload": {...}}

``final_payload`` is the user-approved payload, always validated against the
kind's contract schema before it is recorded or applied (the AI/user output is
untrusted — the family security standard trust boundary).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.converters import utcnow
from app.extensions.proposal_kinds import (
    ensure_proposal_kind,
    get_kind,
    kind_for_card_variant,
)
from app.extensions.sdk.proposal_schemas import schema_for
from app.models.chat_model import ChatMessage, ChatSession
from app.models.enums import ProposalSource, ProposalStatus
from app.models.proposal import Proposal
from app.services.proposals.proposal_state import card_status, transition

logger = logging.getLogger(__name__)

#: Card-document schema version recorded on every new row (bump when the
#: envelope keys change; readers branch on the column, never sniff the JSON).
CARD_SCHEMA_VERSION = 1

#: How long a proposal stays actionable. The expiry sweep closes stale rows;
#: clinical proposals should outlive a weekend but never linger indefinitely.
DEFAULT_TTL = timedelta(days=14)

_CARD_KEYS = ("title", "context", "proposed_payload")


class ProposalPayloadInvalid(ValueError):
    """``final_payload`` does not satisfy the kind's contract schema."""

    def __init__(self, kind_code: str, errors: Any):
        self.kind_code = kind_code
        self.errors = errors
        super().__init__(f"final_payload is invalid for proposal kind {kind_code!r}: {errors}")


class UnknownProposalKind(ValueError):
    """The kind is not in the seeded contract vocabulary."""

    def __init__(self, kind_code: str):
        self.kind_code = kind_code
        super().__init__(f"unknown proposal kind {kind_code!r} — not in hitl-proposal-kinds.json")


def compute_dedup_key(kind_code: str, proposed_payload: Any) -> str | None:
    """sha256 of canonical JSON over ``(kind, proposed_payload)``.

    Returns ``None`` when the payload cannot be canonicalized; the row then
    carries ``dedup_key = NULL`` and the partial unique index does not apply.
    """
    try:
        # The envelope keys are frozen schema: existing rows store keys minted
        # before the store rename, and a re-sync must still collide with them
        # (`type` = the kind code, `payload` = the declared draft).
        canonical = json.dumps(
            {"type": kind_code, "payload": proposed_payload},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("compute_dedup_key failed for kind=%s: %s", kind_code, exc)
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def card_payload(
    *,
    title: str | None,
    proposed_payload: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the versioned card document stored in ``proposals.payload``."""
    return {
        "title": title,
        "context": dict(context or {}),
        "proposed_payload": dict(proposed_payload or {}),
    }


def validate_final_payload(kind_code: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Validate (and normalize to JSON) a kind's ``final_payload``.

    Raises:
        UnknownProposalKind: the kind is not in the contract.
        ProposalPayloadInvalid: the payload fails the kind's schema — the
            caller must reject before any write.
    """
    kind = get_kind(kind_code)
    if kind is None:
        raise UnknownProposalKind(kind_code)
    model = schema_for(kind.schema_ref)
    if model is None:
        raise ProposalPayloadInvalid(kind_code, f"no schema registered for {kind.schema_ref!r}")
    try:
        validated = model.model_validate(payload or {})
    except ValidationError as exc:
        raise ProposalPayloadInvalid(kind_code, exc.errors()) from exc
    return validated.model_dump(mode="json")


async def _find_by_dedup(
    db: AsyncSession, tenant_id: UUID, dedup_key: str | None
) -> Proposal | None:
    if dedup_key is None:
        return None
    result = await db.execute(
        select(Proposal).where(
            Proposal.tenant_id == tenant_id,
            Proposal.dedup_key == dedup_key,
        )
    )
    return result.scalar_one_or_none()


async def create_proposal(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    kind_code: str,
    source: ProposalSource | str,
    source_ref: UUID | None,
    user_id: UUID | None = None,
    patient_id: UUID | None = None,
    ordinal: int | None = None,
    title: str | None = None,
    proposed_payload: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    dedup_key: str | None = None,
    expires_at: datetime | None = None,
    created_by: UUID | None = None,
    proposal_id: UUID | None = None,
) -> tuple[Proposal, bool]:
    """Insert a PROPOSED proposal row (idempotent on ``dedup_key``).

    Returns ``(row, created)``. A re-emitted spec with the same dedup key
    returns the existing row unchanged — even after a decision (providers that
    want "never re-propose after decision" advance their own cursor in
    ``handle_proposal_resolution``).

    The caller owns the commit.
    """
    kind_id = await ensure_proposal_kind(db, kind_code)
    if kind_id is None:
        raise UnknownProposalKind(kind_code)

    source_value = ProposalSource(source)
    if dedup_key is not None:
        existing = await _find_by_dedup(db, tenant_id, dedup_key)
        if existing is not None:
            return existing, False

    values: dict[str, Any] = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "created_by": created_by if created_by is not None else user_id,
        "patient_id": patient_id,
        "source": source_value,
        "source_ref": source_ref,
        "ordinal": ordinal,
        "kind_id": kind_id,
        "status": ProposalStatus.PROPOSED,
        "payload": card_payload(title=title, proposed_payload=proposed_payload, context=context),
        "payload_schema_version": CARD_SCHEMA_VERSION,
        "dedup_key": dedup_key,
        "expires_at": expires_at or (utcnow() + DEFAULT_TTL),
        "resolution_trace": {},
    }
    if proposal_id is not None:
        values["id"] = proposal_id

    row = Proposal(**values)
    db.add(row)
    try:
        await db.flush()
        await db.refresh(row)
    except IntegrityError as exc:
        await db.rollback()
        if dedup_key is None:
            raise
        raced = await _find_by_dedup(db, tenant_id, dedup_key)
        if raced is None:
            raise ValueError(
                f"create_proposal integrity failure for tenant={tenant_id} "
                f"dedup_key={dedup_key}: {exc}"
            ) from exc
        return raced, False
    return row, True


async def find_pending_extension_proposal(
    db: AsyncSession, *, integration_id: UUID, proposal_id: UUID
) -> Proposal | None:
    """One extension proposal scoped to its owning integration instance."""
    result = await db.execute(
        select(Proposal).where(
            Proposal.id == proposal_id,
            Proposal.source == ProposalSource.EXTENSION,
            Proposal.source_ref == integration_id,
        )
    )
    return result.scalar_one_or_none()


async def list_extension_proposals(
    db: AsyncSession,
    *,
    integration_id: UUID,
    status: ProposalStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Proposal]:
    """List one integration's proposals, newest-first with an id tiebreaker."""
    stmt = select(Proposal).where(
        Proposal.source == ProposalSource.EXTENSION,
        Proposal.source_ref == integration_id,
    )
    if status is not None:
        stmt = stmt.where(Proposal.status == status)
    stmt = stmt.order_by(Proposal.created_at.desc(), Proposal.id.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def count_extension_proposals(
    db: AsyncSession, *, integration_id: UUID, status: ProposalStatus | None = None
) -> int:
    from sqlalchemy import func

    stmt = select(func.count(Proposal.id)).where(
        Proposal.source == ProposalSource.EXTENSION,
        Proposal.source_ref == integration_id,
    )
    if status is not None:
        stmt = stmt.where(Proposal.status == status)
    result = await db.execute(stmt)
    return int(result.scalar() or 0)


# ---------------------------------------------------------------------------
# Chat cards
# ---------------------------------------------------------------------------


def _chat_kind_code(task_type: str) -> str:
    kind = kind_for_card_variant(task_type) or get_kind(task_type)
    if kind is None:
        raise UnknownProposalKind(task_type)
    return kind.id


async def upsert_chat_proposals(
    db: AsyncSession,
    *,
    message: ChatMessage,
    tasks: list[dict[str, Any]] | None,
    tenant_id: UUID,
    user_id: UUID | None,
) -> list[Proposal]:
    """Persist a message's HITL cards as proposal rows, in card order.

    Idempotent by proposal id, so the graph's proactive save followed by the
    final save is an update, not a duplicate. A row already resolved is never
    reset to ``proposed`` by a later save of the same card.
    """
    if not tasks:
        return []

    rows: list[Proposal] = []
    for ordinal, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        raw_id = task.get("proposal_id")
        if not raw_id:
            logger.warning("chat HITL task without proposal_id skipped (message=%s)", message.id)
            continue
        proposal_id = UUID(str(raw_id))
        kind_code = _chat_kind_code(str(task.get("task_type") or ""))
        existing = await db.get(Proposal, proposal_id)
        payload = card_payload(
            title=task.get("title"),
            proposed_payload=task.get("proposed_payload"),
            context=task.get("context"),
        )
        if existing is None:
            kind_id = await ensure_proposal_kind(db, kind_code)
            if kind_id is None:
                raise UnknownProposalKind(kind_code)
            row = Proposal(
                id=proposal_id,
                tenant_id=tenant_id,
                user_id=user_id,
                created_by=user_id,
                patient_id=_uuid_or_none((task.get("context") or {}).get("patient_id")),
                source=ProposalSource.CHAT,
                source_ref=message.id,
                ordinal=ordinal,
                kind_id=kind_id,
                status=ProposalStatus.PROPOSED,
                payload=payload,
                payload_schema_version=CARD_SCHEMA_VERSION,
                expires_at=utcnow() + DEFAULT_TTL,
                resolution_trace={},
            )
            db.add(row)
            await db.flush()
            await db.refresh(row)
        else:
            existing.source_ref = message.id
            existing.ordinal = ordinal
            existing.payload = payload
            if existing.status == ProposalStatus.PROPOSED:
                existing.patient_id = _uuid_or_none((task.get("context") or {}).get("patient_id"))
            row = existing
        rows.append(row)
    return rows


def _uuid_or_none(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


async def proposals_by_message(
    db: AsyncSession, message_ids: list[UUID], *, tenant_id: UUID | None = None
) -> dict[UUID, list[Proposal]]:
    """Chat proposals grouped by their originating message, ordinal order."""
    if not message_ids:
        return {}
    stmt = (
        select(Proposal)
        .where(
            Proposal.source == ProposalSource.CHAT,
            Proposal.source_ref.in_(message_ids),
        )
        .order_by(Proposal.source_ref, Proposal.ordinal, Proposal.created_at)
    )
    if tenant_id is not None:
        stmt = stmt.where(Proposal.tenant_id == tenant_id)
    result = await db.execute(stmt)
    grouped: dict[UUID, list[Proposal]] = {}
    for row in result.scalars().all():
        grouped.setdefault(row.source_ref, []).append(row)
    return grouped


def card_dict(proposal: Proposal) -> dict[str, Any]:
    """Serialize one proposal as the ADR-0015 wire card (shape unchanged)."""
    payload = dict(proposal.payload or {})
    kind = proposal.kind
    trace = dict(proposal.resolution_trace or {})
    resolved = None
    if proposal.status != ProposalStatus.PROPOSED:
        resolved = {
            "confirmed_by": trace.get("resolved_by"),
            "final_payload": proposal.final_payload,
            "result": trace.get("result"),
            "error": trace.get("error"),
            "at": trace.get("at")
            or (proposal.resolved_at.isoformat() if proposal.resolved_at else None),
        }
    return {
        "schema_version": proposal.payload_schema_version,
        "proposal_id": str(proposal.id),
        "task_type": kind.card_variant if kind is not None else None,
        "title": payload.get("title"),
        "status": card_status(proposal.status).value,
        "proposed_payload": payload.get("proposed_payload") or {},
        "context": payload.get("context") or {},
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
        "resolved": resolved,
    }


async def attach_cards(db: AsyncSession, messages: list[ChatMessage]) -> None:
    """Attach each message's cards as a transient ``tasks`` attribute.

    The column is gone (no replacement column — ordering lives in
    ``proposals.ordinal``); the ADR-0015 wire shape still carries ``tasks``, so
    the serializers read this in-memory projection. Every message gets a list
    (empty when it proposed nothing), matching the old JSONB contract.
    """
    if not messages:
        return
    grouped = await proposals_by_message(db, [m.id for m in messages])
    for message in messages:
        cards = [card_dict(row) for row in grouped.get(message.id, [])]
        object.__setattr__(message, "tasks", cards)


async def get_chat_proposal(
    db: AsyncSession,
    *,
    proposal_id: UUID,
    session_id: UUID,
    tenant_id: UUID,
    user_id: UUID | None = None,
) -> tuple[Proposal | None, ChatMessage | None]:
    """One chat proposal + its originating message, session/tenant/user scoped."""
    filters = [
        Proposal.id == proposal_id,
        Proposal.source == ProposalSource.CHAT,
        ChatMessage.session_id == session_id,
        ChatSession.tenant_id == tenant_id,
    ]
    if user_id is not None:
        filters.append(ChatSession.user_id == user_id)
    result = await db.execute(
        select(Proposal, ChatMessage)
        .join(ChatMessage, Proposal.source_ref == ChatMessage.id)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(*filters)
    )
    row = result.first()
    if row is None:
        return None, None
    return row[0], row[1]


async def supersede_proposal(
    db: AsyncSession,
    proposal: Proposal,
    *,
    by_id: UUID,
    actor_id: UUID | None = None,
    note: str | None = None,
) -> None:
    """Supersede a pending proposal (a newer card replaced it).

    Superseded is terminal and conflicts with any later approve (409) — the
    "rebase-or-reject" half of the ADR-0015 conflict rule.
    """
    transition(
        proposal,
        ProposalStatus.SUPERSEDED,
        action="supersede",
        actor_id=actor_id,
        note=note,
        superseded_by=by_id,
    )


async def expire_stale_proposals(
    db: AsyncSession, *, now: datetime | None = None, limit: int = 500
) -> int:
    """Resolve stale pending proposals to ``expired`` (the sweep's chokepoint).

    Returns the number of rows expired. The caller commits.
    """
    stamp = now or utcnow()
    result = await db.execute(
        select(Proposal)
        .where(
            Proposal.status == ProposalStatus.PROPOSED,
            Proposal.expires_at.is_not(None),
            Proposal.expires_at <= stamp,
        )
        .limit(limit)
    )
    rows = list(result.scalars().all())
    for row in rows:
        transition(
            row,
            ProposalStatus.EXPIRED,
            action="expire",
            note="expired by the sweep",
            now=stamp,
        )
    return len(rows)


__all__ = [
    "CARD_SCHEMA_VERSION",
    "DEFAULT_TTL",
    "ProposalPayloadInvalid",
    "UnknownProposalKind",
    "attach_cards",
    "card_dict",
    "card_payload",
    "compute_dedup_key",
    "count_extension_proposals",
    "create_proposal",
    "expire_stale_proposals",
    "find_pending_extension_proposal",
    "get_chat_proposal",
    "list_extension_proposals",
    "proposals_by_message",
    "supersede_proposal",
    "upsert_chat_proposals",
    "validate_final_payload",
]
