"""Shared HITL task-card resolution (family contract, ADR-0015 Class-B).

Extracted from the ``/ai-assistance`` resolve endpoint (2026-10-11) so the
health-assistant bridge can record resolutions through the integration
identity with the exact same semantics.

Plan 5: a chat card *is* a row in the one ``proposals`` table
(``source='chat'``, ``source_ref=<assistant message id>``, ``ordinal`` = card
order). Resolution no longer touches ``chat_messages.tasks`` (the column is
gone); it transitions the proposal through the one state machine and appends
the agent-awareness note.

The client commits any proposed write through the canonical REST endpoint
BEFORE resolving — this service records the outcome into the proposal row
(audit + agent awareness); it never performs the write itself. Resolution is
idempotent: repeating the same decision returns the stored card (200);
a conflicting decision on a terminal proposal raises
:class:`~app.services.proposals.proposal_state.ProposalConflict` (409).
"""

from __future__ import annotations

from uuid import UUID

from app.models.enums import HitlTaskStatus, ProposalStatus
from app.services.proposals import proposal_service
from app.services.proposals.proposal_state import (
    ProposalConflict,
    proposal_status,
    resolve_transition,
    transition,
)


class HitlResolutionError(ValueError):
    """Invalid resolve request (bad status, invalid payload) — maps to 400."""


class HitlResolutionNotFound(HitlResolutionError):
    """Proposal/message/session not found — maps to 404."""


class HitlResolutionConflict(ProposalConflict):
    """The proposal already records a different outcome — maps to HTTP 409."""


async def resolve_chat_task(
    chat_service,
    session_id: UUID,
    proposal_id: str,
    status: str,
    final_payload: dict | None = None,
    result: dict | None = None,
    error: str | None = None,
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> dict:
    """Record a confirmed/dismissed resolution on a chat proposal card.

    Returns the updated ADR-0015 card dict.

    Raises:
        HitlResolutionError: proposal/message/session not found, invalid wire
            status, or an invalid ``final_payload`` for the kind.
        HitlResolutionConflict: the proposal records a different outcome.
    """
    if status not in (HitlTaskStatus.CONFIRMED, HitlTaskStatus.DISMISSED):
        raise HitlResolutionError("status must be 'confirmed' or 'dismissed'")
    if tenant_id is None:
        raise HitlResolutionNotFound("Task proposal not found or access denied.")
    try:
        proposal_uuid = UUID(str(proposal_id))
    except (TypeError, ValueError) as exc:
        raise HitlResolutionNotFound("Task proposal not found or access denied.") from exc

    db = chat_service.db
    row, _message = await proposal_service.get_chat_proposal(
        db,
        proposal_id=proposal_uuid,
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if row is None:
        raise HitlResolutionNotFound("Task proposal not found or access denied.")

    target = proposal_status(status)
    try:
        idempotent = resolve_transition(row.status, target)
    except ProposalConflict as exc:
        raise HitlResolutionConflict(exc.current, exc.target) from exc

    if idempotent:
        return proposal_service.card_dict(row)

    kind_code = row.kind.code if row.kind is not None else ""
    validated = None
    if final_payload is not None:
        try:
            validated = proposal_service.validate_final_payload(kind_code, final_payload)
        except ValueError as exc:
            raise HitlResolutionError(str(exc)) from exc

    transition(
        row,
        target,
        action="confirm" if target == ProposalStatus.APPLIED else "dismiss",
        actor_id=user_id,
        final_payload=validated,
        result=result,
        error=error,
    )
    await db.commit()
    await db.refresh(row)

    # Agent awareness: append a concise assistant note so the next chat turn
    # knows the proposed action was carried out (or dismissed) by the user.
    # ``ask_user`` cards are read-only — the note says "answered", not "saved".
    card = proposal_service.card_dict(row)
    if target == ProposalStatus.APPLIED:
        payload = card.get("proposed_payload") or {}
        label = payload.get("title") or card.get("title") or card.get("task_type")
        if card["task_type"] == "ask_user":
            note_text = f"✓ The user answered: {label}."
        else:
            note_text = f"✓ Confirmed and saved: {label}."
        await chat_service.save_message(
            session_id=session_id,
            role="assistant",
            content={"text": note_text},
        )
    else:
        await chat_service.save_message(
            session_id=session_id,
            role="assistant",
            content={
                "text": "The proposed draft was dismissed. Let me know how you'd like to proceed."
            },
        )
    return card


__all__ = [
    "HitlResolutionConflict",
    "HitlResolutionError",
    "HitlResolutionNotFound",
    "resolve_chat_task",
]
