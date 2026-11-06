"""Integration-proposal service — the extension binding of the one proposal store.

Plan 5 merged the two HITL stores: ``integration_proposals`` is gone; extension
proposals are rows in ``proposals`` with ``source='extension'`` and
``source_ref=<user_integration_id>``. This module keeps the extension-facing
API (create/list/get/count/resolve) and delegates persistence, card shape and
the state machine to :mod:`app.services.proposals.proposal_service` /
:mod:`app.services.proposals.proposal_state`.

On approve, the (possibly user-edited) payload is validated against the kind's
contract schema **before any write**, then applied through
:mod:`app.services.proposals.proposal_resolvers` (catalog kinds → the catalog service;
the four patient-record kinds → the canonical clinical services). The status
records the apply outcome: ``applied`` on success, ``failed`` on an apply
error (the row stays for re-review). Re-resolving an already-applied proposal
returns the stored outcome (idempotent, 200); a conflicting decision on a
terminal row raises :class:`~app.services.proposals.proposal_state.ProposalConflict`
(409).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ProposalStatus
from app.models.proposal import Proposal
from app.services.proposals import proposal_service
from app.services.proposals.proposal_resolvers import apply_proposal, assert_payload_scope
from app.services.proposals.proposal_state import resolve_transition, transition

logger = logging.getLogger(__name__)


# Backwards-compatible name for the resubmission idempotency key.
compute_dedup_key = proposal_service.compute_dedup_key


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_proposal(
    db: AsyncSession,
    *,
    integration_id: UUID,
    tenant_id: UUID | None,
    proposal_type: str,
    title: str,
    proposed_payload: Any,
    patient_id: UUID | None = None,
    context: dict | None = None,
    created_by: UUID | None = None,
) -> tuple[Proposal, bool]:
    """Insert a PROPOSED extension proposal (idempotent on the dedup key).

    Returns ``(row, created)``; the caller owns the commit. A re-emitted spec
    returns the existing row unchanged — even after a decision — so re-syncs
    never spam the inbox.
    """
    if tenant_id is None:
        raise ValueError("extension proposals require a tenant_id")
    dedup_key = proposal_service.compute_dedup_key(proposal_type, proposed_payload)
    return await proposal_service.create_proposal(
        db,
        tenant_id=tenant_id,
        kind_code=proposal_type,
        source="extension",
        source_ref=integration_id,
        user_id=created_by,
        patient_id=patient_id,
        title=title,
        proposed_payload=proposed_payload,
        context=context,
        dedup_key=dedup_key,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_proposal(
    db: AsyncSession,
    *,
    integration_id: UUID,
    proposal_id: UUID,
) -> Proposal | None:
    """One proposal, scoped to ``integration_id`` (no id-guessing across instances)."""
    return await proposal_service.find_pending_extension_proposal(
        db, integration_id=integration_id, proposal_id=proposal_id
    )


async def list_proposals(
    db: AsyncSession,
    *,
    integration_id: UUID,
    status: ProposalStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Proposal]:
    """List one integration's proposals, newest-first."""
    return await proposal_service.list_extension_proposals(
        db, integration_id=integration_id, status=status, limit=limit, offset=offset
    )


async def count_proposals(
    db: AsyncSession,
    *,
    integration_id: UUID,
    status: ProposalStatus | None = None,
) -> int:
    """Count one integration's proposals (sync-log bookkeeping)."""
    return await proposal_service.count_extension_proposals(
        db, integration_id=integration_id, status=status
    )


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------


class ResolutionResult:
    """Outcome of resolving a proposal (status + applied id + error)."""

    __slots__ = ("applied_entity_id", "error", "proposal")

    def __init__(
        self,
        *,
        proposal: Proposal,
        applied_entity_id: UUID | None = None,
        error: str | None = None,
    ):
        self.proposal = proposal
        self.applied_entity_id = applied_entity_id
        self.error = error

    @property
    def status(self) -> ProposalStatus:
        return self.proposal.status


def _stored_outcome(row: Proposal) -> ResolutionResult:
    trace = dict(row.resolution_trace or {})
    result = trace.get("result") or {}
    raw_entity = result.get("entity_id")
    try:
        entity_id = UUID(str(raw_entity)) if raw_entity else None
    except (TypeError, ValueError):
        entity_id = None
    return ResolutionResult(
        proposal=row,
        applied_entity_id=entity_id,
        error=trace.get("error"),
    )


async def resolve_proposal(
    db: AsyncSession,
    *,
    integration: Any,
    proposal_id: UUID,
    action: str,
    actor: Any,
    payload_override: dict | None = None,
    note: str | None = None,
    provider: Any = None,
) -> ResolutionResult:
    """Resolve a pending extension proposal (approve / reject / cancel).

    Idempotent: repeating a resolve that asks for the already-recorded outcome
    returns the stored result. A different decision on a terminal row raises
    :class:`ProposalConflict`; an apply error records ``failed`` and returns
    (the row stays for re-review).

    Raises:
        LookupError: proposal not found for this integration.
        ProposalConflict: conflicting terminal decision.
        IllegalProposalTransition: a move the state machine does not allow.
        ProposalPayloadInvalid: the approved payload fails the kind schema.
    """
    if action not in {"approve", "reject", "cancel"}:
        raise ValueError(f"resolve_proposal action must be approve/reject/cancel, got {action!r}")

    integration_id = integration.id
    row = await get_proposal(db, integration_id=integration_id, proposal_id=proposal_id)
    if row is None:
        raise LookupError(f"Proposal {proposal_id} not found")

    target = ProposalStatus.APPLIED if action == "approve" else ProposalStatus.REJECTED
    if resolve_transition(row.status, target):
        return _stored_outcome(row)

    if action != "approve":
        transition(
            row,
            ProposalStatus.REJECTED,
            action=action,
            actor_id=getattr(actor, "user_id", None),
            note=note,
        )
        await db.commit()
        await db.refresh(row)
        return ResolutionResult(proposal=row)

    kind_code = row.kind.code
    raw_payload = (
        payload_override
        if payload_override is not None
        else ((row.payload or {}).get("proposed_payload") or {})
    )
    validated = proposal_service.validate_final_payload(kind_code, raw_payload)
    assert_payload_scope(row, validated)
    if row.patient_id is not None and validated.get("patient_id") in (None, ""):
        validated["patient_id"] = str(row.patient_id)

    transition(
        row,
        ProposalStatus.APPROVED,
        action="approve",
        actor_id=getattr(actor, "user_id", None),
        note=note,
        final_payload=validated,
    )
    await db.flush()

    error: str | None = None
    entity_id: UUID | None = None
    detail: str | None = None
    try:
        outcome = await apply_proposal(
            db,
            proposal=row,
            actor=actor,
            integration=integration,
            payload=validated,
        )
        entity_id = outcome.entity_id
        detail = outcome.detail
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning(
            "resolve_proposal apply failed for proposal=%s (kind=%s): %s",
            proposal_id,
            kind_code,
            exc,
        )
        error = f"{type(exc).__name__}: {exc}"

    # Re-fetch after a rollback (the ORM session was invalidated) or to see the
    # committed row before stamping the terminal state.
    row = await get_proposal(db, integration_id=integration_id, proposal_id=proposal_id)
    if row is None:
        raise RuntimeError(f"Proposal {proposal_id} vanished during resolve")

    if error is not None:
        transition(
            row,
            ProposalStatus.FAILED,
            action="apply",
            actor_id=getattr(actor, "user_id", None),
            note=note,
            error=error,
        )
    else:
        transition(
            row,
            ProposalStatus.APPLIED,
            action="apply",
            actor_id=getattr(actor, "user_id", None),
            note=note,
            result={"entity_id": str(entity_id) if entity_id else None, "detail": detail},
        )
    await db.commit()
    await db.refresh(row)

    if provider is not None and error is None:
        try:
            from app.extensions.sdk.proposals import ProposalOutcome
            from app.services.integration.integration_sync_state import flush as flush_cursors
            from app.services.integration.integration_sync_state import hydrate

            # The callback may advance a provider cursor (e.g. "this code was
            # seen") — cursors are rows, so hydrate + flush around it.
            await hydrate(db, integration)
            outcome_payload = dict(row.final_payload or {})
            if entity_id is not None:
                outcome_payload["_applied_entity_id"] = str(entity_id)
            await provider.handle_proposal_resolution(
                integration,
                proposal_id,
                ProposalOutcome(
                    action="approve",
                    final_payload=outcome_payload,
                    applied_entity_id=entity_id,
                    error=None,
                ),
            )
            await flush_cursors(db, integration)
            await db.commit()
        except Exception as cb_err:
            logger.warning(
                "handle_proposal_resolution raised for proposal=%s: %s",
                proposal_id,
                cb_err,
            )

    return ResolutionResult(proposal=row, applied_entity_id=entity_id, error=error)


__all__ = [
    "ResolutionResult",
    "compute_dedup_key",
    "count_proposals",
    "create_proposal",
    "get_proposal",
    "list_proposals",
    "resolve_proposal",
]
