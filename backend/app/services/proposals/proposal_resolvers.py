"""Server-side apply dispatch for HITL proposals (plan 5 W3).

The proposal-kind contract names a ``resolver`` per kind; this module is the
static resolver table (no dynamic imports — the extension structure probe
forbids them under ``app/extensions``) and the one tenant/patient-scope gate
every server-side apply passes through.

Extension proposals are applied server-side on approve. Chat proposals are
**not**: the UI already committed the write through the canonical REST/FHIR
endpoint and the resolve call only records the outcome (ADR-0015: "AI
proposes, humans approve, canonical endpoints write").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.extensions.sdk.proposals import PATIENT_RECORD_RESOLVERS, PatientRecordWrite
from app.models.proposal import Proposal

logger = logging.getLogger(__name__)

#: Catalog resolver name → ``CatalogProposal.kind``.
CATALOG_KINDS: dict[str, str] = {
    "catalog_biomarker": "biomarker",
    "catalog_medication": "medication",
    "catalog_concept": "concept",
    "catalog_edge": "edge",
}

#: Kinds whose write the UI already performed through a canonical endpoint.
CLIENT_COMMITTED_RESOLVERS = frozenset({"client_committed", "record_only"})


class ProposalApplyError(ValueError):
    """The proposal's kind cannot be applied server-side, or scope is invalid."""


@dataclass(frozen=True)
class ApplyOutcome:
    """Outcome of one server-side apply."""

    created: bool
    entity_id: UUID | None
    detail: str | None = None


def assert_payload_scope(proposal: Proposal, payload: dict[str, Any]) -> None:
    """Reject a payload that names a different patient than the proposal row.

    A human approved *this* proposal; a payload may omit ``patient_id`` (the row
    carries the scope) but may never retarget the write to another patient.
    Raises :class:`ProposalApplyError` before any state change or write.
    """
    payload_patient = payload.get("patient_id")
    if payload_patient in (None, "") or proposal.patient_id is None:
        return
    try:
        payload_uuid = UUID(str(payload_patient))
    except (TypeError, ValueError) as exc:
        raise ProposalApplyError(f"payload patient_id is not a UUID: {payload_patient!r}") from exc
    if payload_uuid != proposal.patient_id:
        raise ProposalApplyError("payload patient_id does not match the proposal's patient scope")


def _payload_for_apply(proposal: Proposal, payload: dict[str, Any] | None) -> dict[str, Any]:
    """The payload to apply, with the proposal's patient scope enforced.

    A payload may omit ``patient_id`` (the row carries the scope) but may never
    name a different patient than the proposal's — that would be a cross-patient
    write behind a human approval.
    """
    chosen = dict(
        payload if payload is not None else (proposal.payload or {}).get("proposed_payload") or {}
    )
    row_patient = proposal.patient_id
    payload_patient = chosen.get("patient_id")
    if payload_patient not in (None, ""):
        try:
            payload_uuid = UUID(str(payload_patient))
        except (TypeError, ValueError) as exc:
            raise ProposalApplyError(
                f"payload patient_id is not a UUID: {payload_patient!r}"
            ) from exc
        if row_patient is not None and payload_uuid != row_patient:
            raise ProposalApplyError(
                "payload patient_id does not match the proposal's patient scope"
            )
        if row_patient is None:
            row_patient = payload_uuid
    if row_patient is not None:
        chosen["patient_id"] = str(row_patient)
    return chosen


async def apply_proposal(
    db: AsyncSession,
    *,
    proposal: Proposal,
    actor: Any,
    integration: Any,
    payload: dict[str, Any] | None = None,
) -> ApplyOutcome:
    """Apply one approved extension proposal through its kind's resolver.

    Raises:
        ProposalApplyError: the kind is not server-applicable, the scope is
            invalid, or no resolver is registered for the contract entry.
    """
    kind = proposal.kind
    if kind is None:
        raise ProposalApplyError("proposal has no kind row")
    resolver = kind.resolver
    resolved_payload = _payload_for_apply(proposal, payload)

    if resolver in CLIENT_COMMITTED_RESOLVERS:
        raise ProposalApplyError(
            f"kind {kind.code!r} is {resolver} — it has no server-side apply path"
        )

    patient_resolver = PATIENT_RECORD_RESOLVERS.get(resolver)
    if patient_resolver is not None:
        write: PatientRecordWrite = await patient_resolver(
            db,
            actor,
            resolved_payload,
            integration_id=getattr(integration, "id", None),
            external_id=str(proposal.id),
        )
        return ApplyOutcome(created=True, entity_id=write.entity_id, detail=write.detail)

    catalog_kind = CATALOG_KINDS.get(resolver)
    if catalog_kind is not None:
        from app.extensions.sdk.catalog import CatalogProposal
        from app.services.catalog.catalog_proposal_service import apply_proposal as apply_catalog

        result = await apply_catalog(
            db,
            actor,
            integration,
            CatalogProposal(kind=catalog_kind, payload=resolved_payload),
        )
        return ApplyOutcome(
            created=result.created,
            entity_id=result.entity_id,
            detail=result.detail,
        )

    raise ProposalApplyError(f"no resolver registered for kind {kind.code!r} (resolver={resolver})")


__all__ = [
    "CATALOG_KINDS",
    "CLIENT_COMMITTED_RESOLVERS",
    "ApplyOutcome",
    "ProposalApplyError",
    "apply_proposal",
    "assert_payload_scope",
]
