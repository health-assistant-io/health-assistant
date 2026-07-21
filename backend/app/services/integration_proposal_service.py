"""Integration-proposal persistence + resolution service.

Workstream G of the integrations follow-ups pass. Closes the gap left by
``ConceptProvenance.INTEGRATION`` for the case where the integration wants
a human to review its catalog contributions before they apply. Today
(workstream F) integrations can already auto-apply catalog entries via
``supports_catalog_proposals``; this module is the opt-in HITL layer for
providers that want a human-in-the-loop gate.

Two layers:

- **Persistence + lookups** (this module, G.1): ``create_proposal``,
  ``list_proposals``, ``get_proposal``, ``compute_dedup_key``.
- **Resolver** (G.2): ``resolve_proposal`` performs the state-machine
  transition (PROPOSED → CONFIRMED / DISMISSED / FAILED), delegates the
  approve path to :func:`catalog_proposal_service.apply_proposal`, and
  invokes the provider's ``handle_proposal_resolution`` callback.

The resolver lands in the next commit; for now the module covers
persistence + dedup only.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.converters import utcnow as _now
from app.models.enums import HitlTaskStatus
from app.models.integration_proposal import IntegrationProposal
from integrations.sdk.catalog import CatalogProposal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# proposal_type → CatalogProposal.kind mapping
# ---------------------------------------------------------------------------

#: Maps an integration-proposal ``proposal_type`` string to the matching
#: ``CatalogProposal.kind`` value. Used by the resolver to route the
#: approve path through ``catalog_proposal_service.apply_proposal``. Names
#: mirror the chat-side ``task_type`` strings (``create_*_definition``)
#: so a future unified review UI can render both sources identically.
_PROPOSAL_TYPE_TO_CATALOG_KIND: dict[str, str] = {
    "create_biomarker_definition": "biomarker",
    "create_medication_definition": "medication",
    "create_concept": "concept",
    "create_edge": "edge",
}


# ---------------------------------------------------------------------------
# Dedup key
# ---------------------------------------------------------------------------


def compute_dedup_key(proposal_type: str, proposed_payload: Any) -> Optional[str]:
    """Return the sha256 hex digest of the canonical-JSON dedup key, or
    ``None`` if the payload can't be canonicalized (which disables dedup
    for that proposal — the engine lookup-then-insert still runs, but
    the partial unique index will allow duplicates).

    The key is ``sha256(canonical_json({"type": ..., "payload": ...}))``.
    Canonical JSON = ``json.dumps(..., sort_keys=True, separators=(",", ":"),
    default=str)`` so dict-key ordering + whitespace don't defeat dedup.
    """
    try:
        canonical = json.dumps(
            {"type": proposal_type, "payload": proposed_payload},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "compute_dedup_key: failed to canonicalize payload for type=%s: %s",
            proposal_type,
            exc,
        )
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_proposal(
    db: AsyncSession,
    *,
    integration_id: UUID,
    tenant_id: Optional[UUID],
    proposal_type: str,
    title: str,
    proposed_payload: Any,
    patient_id: Optional[UUID] = None,
    context: Optional[dict] = None,
    created_by: Optional[UUID] = None,
) -> Tuple[IntegrationProposal, bool]:
    """Insert a PROPOSED ``IntegrationProposal`` row.

    Returns ``(row, created)`` where ``created`` is ``True`` when a new row
    was actually inserted and ``False`` when an existing row was returned
    unchanged (the caller doesn't need to re-notify).

    Idempotent on ``(integration_id, dedup_key)``:

    - If a row with the same dedup_key already exists for this
      integration, **return the existing row unchanged** (no status bump,
      no payload overwrite). This covers both the common case (re-sync
      before the user reviews) and the post-decision case (re-sync after
      CONFIRMED / DISMISSED — we don't re-spam the inbox). Providers
      wanting stronger "don't re-propose after decision" semantics should
      advance their own cursor in ``handle_proposal_resolution``.
    - If the payload can't be canonicalized (``compute_dedup_key`` returns
      ``None``), the row is inserted with ``dedup_key=NULL`` and the
      partial unique index is bypassed.

    The caller is responsible for committing the session.
    """
    dedup_key = compute_dedup_key(proposal_type, proposed_payload)

    existing = await _find_by_dedup(db, integration_id, dedup_key)
    if existing is not None:
        return existing, False

    row = IntegrationProposal(
        integration_id=integration_id,
        tenant_id=tenant_id,
        patient_id=patient_id,
        proposal_type=proposal_type,
        title=title,
        status=HitlTaskStatus.PROPOSED,
        proposed_payload=proposed_payload,
        context=context or {},
        dedup_key=dedup_key,
        created_by=created_by,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Race: another sync beat us to the insert between our lookup and
        # INSERT. The partial unique index caught it. Re-fetch and return.
        await db.rollback()
        if dedup_key is None:
            raise
        raced = await _find_by_dedup(db, integration_id, dedup_key)
        if raced is not None:
            logger.info(
                "create_proposal raced another writer for integration=%s "
                "dedup_key=%s — returning the existing row",
                integration_id,
                dedup_key[:12],
            )
            return raced, False
        raise ValueError(
            f"create_proposal integrity check failed for integration="
            f"{integration_id} dedup_key={dedup_key}: {exc}"
        ) from exc
    return row, True


async def _find_by_dedup(
    db: AsyncSession,
    integration_id: UUID,
    dedup_key: Optional[str],
) -> Optional[IntegrationProposal]:
    if dedup_key is None:
        return None
    result = await db.execute(
        select(IntegrationProposal).where(
            IntegrationProposal.integration_id == integration_id,
            IntegrationProposal.dedup_key == dedup_key,
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_proposal(
    db: AsyncSession,
    *,
    integration_id: UUID,
    proposal_id: UUID,
) -> Optional[IntegrationProposal]:
    """Return one proposal, scoped to ``integration_id`` so a caller can't
    read another integration's proposals by id-guessing.
    """
    result = await db.execute(
        select(IntegrationProposal).where(
            IntegrationProposal.integration_id == integration_id,
            IntegrationProposal.id == proposal_id,
        )
    )
    return result.scalar_one_or_none()


async def list_proposals(
    db: AsyncSession,
    *,
    integration_id: UUID,
    status: Optional[HitlTaskStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[IntegrationProposal]:
    """List proposals for one integration, optionally filtered by status.

    Ordered by ``created_at DESC`` so the freshest proposals surface first
    (the frontend's "open proposals" list reads this with
    ``status=PROPOSED``).
    """
    stmt = (
        select(IntegrationProposal)
        .where(IntegrationProposal.integration_id == integration_id)
    )
    if status is not None:
        stmt = stmt.where(IntegrationProposal.status == status)
    # Newest-first with a deterministic tiebreaker (id DESC) so rows
    # inserted in the same transaction — and thus sharing a server-side
    # ``created_at`` — don't come back in random order across calls.
    stmt = stmt.order_by(
        IntegrationProposal.created_at.desc(),
        IntegrationProposal.id.desc(),
    ).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def count_proposals(
    db: AsyncSession,
    *,
    integration_id: UUID,
    status: Optional[HitlTaskStatus] = None,
) -> int:
    """Count proposals matching the filter. Used for sync-log bookkeeping
    (e.g. total PROPOSED proposals for an integration)."""
    from sqlalchemy import func

    stmt = select(func.count(IntegrationProposal.id)).where(
        IntegrationProposal.integration_id == integration_id
    )
    if status is not None:
        stmt = stmt.where(IntegrationProposal.status == status)
    result = await db.execute(stmt)
    return int(result.scalar() or 0)


# ---------------------------------------------------------------------------
# Resolve (G.2)
# ---------------------------------------------------------------------------


class ResolutionResult:
    """Outcome of resolving a proposal.

    Returned by :func:`resolve_proposal` so the endpoint layer can build
    a useful response body without re-fetching the row. Carries the
    final ``status``, the ``applied_entity_id`` (on a successful approve),
    and the optional ``error`` text (on a FAILED approve).
    """

    __slots__ = ("proposal", "applied_entity_id", "error")

    def __init__(
        self,
        *,
        proposal: IntegrationProposal,
        applied_entity_id: Optional[UUID] = None,
        error: Optional[str] = None,
    ):
        self.proposal = proposal
        self.applied_entity_id = applied_entity_id
        self.error = error

    @property
    def status(self) -> HitlTaskStatus:
        return self.proposal.status


async def resolve_proposal(
    db: AsyncSession,
    *,
    integration: Any,
    proposal_id: UUID,
    action: str,
    actor: Any,
    payload_override: Optional[dict] = None,
    note: Optional[str] = None,
    provider: Any = None,
) -> ResolutionResult:
    """Resolve a pending integration proposal.

    State machine (see plan §"Status state machine"):

    - ``action="approve"`` → build a :class:`CatalogProposal` from the
      (possibly-edited) payload, route through
      :func:`catalog_proposal_service.apply_proposal`. On success status
      transitions to ``CONFIRMED``; on apply-exception to ``FAILED``.
    - ``action="reject"`` → status ``DISMISSED``, no apply. ``note`` is
      preserved on the row for audit.
    - ``action="cancel"`` → status ``DISMISSED``, no apply. Semantic
      distinction from ``reject`` is the caller's responsibility (the
      ``note`` field carries intent).

    After a successful or failed resolve (not on reject/cancel — there's
    nothing to act on), the engine best-effort calls
    ``provider.handle_proposal_resolution(integration, proposal_id,
    ProposalOutcome(...))`` if the provider has opted in. The callback is
    fire-and-forget — failures are logged + swallowed so a buggy provider
    can't break the resolve flow.

    Args:
        db: Active session. This function commits.
        integration: The :class:`UserIntegration` row (used for the
            actor lookup and the provider callback).
        proposal_id: The proposal's UUID.
        action: ``"approve"``, ``"reject"``, or ``"cancel"``.
        actor: ``TokenData`` for the resolving user. The endpoint must
            ensure ``actor.user_id`` owns the integration OR is a
            tenant-admin permitted to act on it.
        payload_override: User-edited payload on approve. ``None`` keeps
            the original ``proposed_payload``.
        note: Optional free-text resolution note for audit.
        provider: The integration provider instance, used for the
            ``handle_proposal_resolution`` callback. ``None`` skips the
            callback (matches providers that haven't opted in).

    Raises:
        ValueError: ``action`` not in the allowed set, or
            ``proposal_type`` is not a known catalog-proposal type on
            approve.
        PermissionError: the actor's role can't perform the write that
            the approve path tries (raised by the underlying
            ``catalog_proposal_service.apply_proposal``). Caught + recorded
            as FAILED.
    """
    if action not in {"approve", "reject", "cancel"}:
        raise ValueError(
            f"resolve_proposal action must be approve/reject/cancel, "
            f"got {action!r}"
        )

    # Snapshot the integration id up-front — after a ``db.rollback()`` in
    # the FAILED path, ``integration`` is expired and accessing ``.id``
    # would trigger a lazy-load attempt (raises MissingGreenlet).
    integration_id_snapshot = integration.id

    row = await get_proposal(
        db, integration_id=integration_id_snapshot, proposal_id=proposal_id
    )
    if row is None:
        raise LookupError(f"Proposal {proposal_id} not found")

    if row.status != HitlTaskStatus.PROPOSED:
        raise ValueError(
            f"Proposal {proposal_id} is already in terminal state "
            f"{row.status.value!r} — re-resolve is a no-op (caller "
            f"should surface as 409)."
        )

    resolved_payload: Optional[dict] = None
    applied_entity_id: Optional[UUID] = None
    error: Optional[str] = None
    final_status: HitlTaskStatus

    # Snapshot attributes we'll need for logging after a potential rollback —
    # accessing ``row.<attr>`` after ``db.rollback()`` triggers a lazy-load
    # attempt which fails with ``MissingGreenlet`` (the session is expired).
    row_proposal_type = row.proposal_type
    row_proposed_payload = dict(row.proposed_payload or {})

    if action == "approve":
        kind = _PROPOSAL_TYPE_TO_CATALOG_KIND.get(row_proposal_type)
        if kind is None:
            # Unknown proposal types can't be auto-applied — record as
            # FAILED with a clear message. (Patient-record types like
            # ``create_event`` land in a future workstream.)
            error = (
                f"Resolver doesn't yet support proposal_type="
                f"{row_proposal_type!r} — only the catalog kinds "
                f"{sorted(_PROPOSAL_TYPE_TO_CATALOG_KIND.keys())}."
            )
            final_status = HitlTaskStatus.FAILED
        else:
            payload_to_apply = (
                dict(payload_override) if payload_override is not None
                else row_proposed_payload
            )
            try:
                from app.services.catalog_proposal_service import (
                    apply_proposal,
                )

                apply_result = await apply_proposal(
                    db,
                    actor,
                    integration,
                    CatalogProposal(kind=kind, payload=payload_to_apply),
                )
                applied_entity_id = apply_result.entity_id
                # Stamp the applied id into the resolved payload so audit
                # + future UIs can trace what the proposal wrote.
                resolved_payload = {
                    **payload_to_apply,
                    "_applied_entity_id": (
                        str(applied_entity_id)
                        if applied_entity_id is not None
                        else None
                    ),
                    "_dedup_no_op": not apply_result.created,
                }
                final_status = HitlTaskStatus.CONFIRMED
            except Exception as exc:
                # Roll back the in-flight apply work but keep the row
                # around for the FAILED stamp below. Re-raise if the
                # rollback itself fails — that's a real bug.
                await db.rollback()
                logger.warning(
                    "resolve_proposal: apply_proposal failed for "
                    "proposal=%s (type=%s): %s",
                    proposal_id, row_proposal_type, exc,
                )
                error = f"{type(exc).__name__}: {exc}"
                final_status = HitlTaskStatus.FAILED
                resolved_payload = None
    else:
        # reject / cancel → DISMISSED, no apply.
        final_status = HitlTaskStatus.DISMISSED

    # Re-fetch the row if we rolled back (the ORM session was invalidated).
    if action == "approve" and final_status == HitlTaskStatus.FAILED:
        row = await get_proposal(
            db,
            integration_id=integration_id_snapshot,
            proposal_id=proposal_id,
        )
        if row is None:
            raise RuntimeError(
                f"Proposal {proposal_id} vanished after a failed apply — "
                "the resolver can't record the FAILED state."
            )

    row.status = final_status
    row.resolved_by = actor.user_id
    row.resolved_at = _now()
    row.resolution_note = note
    if resolved_payload is not None:
        row.resolved_payload = resolved_payload
    elif action != "approve":
        # reject/cancel: carry the proposed_payload forward so the row is
        # self-contained (audit reads the row without needing the
        # proposed_payload column).
        row.resolved_payload = {"action": action, "note": note}

    await db.commit()
    await db.refresh(row)

    # Fire the provider callback (best-effort). Skipped when there's no
    # provider, when the provider hasn't opted in
    # (``handle_proposal_resolution`` is the default no-op so this is
    # really just a None-check on the provider), or when the action is
    # reject/cancel (no apply happened, nothing to react to). On approve,
    # the provider learns whether the apply succeeded.
    if provider is not None and action == "approve":
        try:
            from integrations.sdk.proposals import ProposalOutcome

            outcome = ProposalOutcome(
                action=action,
                final_payload=(resolved_payload or {}),
                applied_entity_id=applied_entity_id,
                error=error,
            )
            await provider.handle_proposal_resolution(
                integration, proposal_id, outcome
            )
        except Exception as cb_err:
            logger.warning(
                "handle_proposal_resolution raised for proposal=%s: %s",
                proposal_id, cb_err,
            )

    return ResolutionResult(
        proposal=row,
        applied_entity_id=applied_entity_id,
        error=error,
    )


__all__ = [
    "compute_dedup_key",
    "create_proposal",
    "get_proposal",
    "list_proposals",
    "count_proposals",
    "resolve_proposal",
    "ResolutionResult",
]
