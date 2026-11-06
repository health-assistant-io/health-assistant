"""Pydantic schemas for the extension HITL-proposal endpoints (plan 5).

Extension proposals are rows in the one ``proposals`` table
(``source='extension'``, ``source_ref`` = integration instance id). The response
is the row's public projection: the kind vocabulary (``kind`` + the ADR-0015
``card_variant``), the versioned card document (``payload``), the
user-approved ``final_payload``, and the audit-grade ``resolution_trace``.

The SDK-facing spec (:class:`app.extensions.sdk.proposals.IntegrationProposalSpec`)
lives in the extension SDK so providers can build specs without importing API
code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProposalSource, ProposalStatus

ResolveAction = Literal["approve", "reject", "cancel"]


def proposal_response(row: Any) -> dict[str, Any]:
    """Serialize one ``Proposal`` row for the extension proposal API."""
    kind = row.kind
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "user_id": row.user_id,
        "patient_id": row.patient_id,
        "source": ProposalSource(row.source).value,
        "source_ref": row.source_ref,
        "ordinal": row.ordinal,
        "kind": kind.code if kind is not None else None,
        "card_variant": kind.card_variant if kind is not None else None,
        "status": ProposalStatus(row.status).value,
        "payload": row.payload,
        "payload_schema_version": row.payload_schema_version,
        "final_payload": row.final_payload,
        "dedup_key": row.dedup_key,
        "expires_at": row.expires_at,
        "resolved_at": row.resolved_at,
        "applied_at": row.applied_at,
        "resolution_trace": row.resolution_trace,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class IntegrationProposalResponse(BaseModel):
    """API response shape for one extension proposal row."""

    model_config = ConfigDict(extra="allow")

    id: UUID
    tenant_id: UUID
    user_id: UUID | None = None
    patient_id: UUID | None = None
    source: ProposalSource
    source_ref: UUID | None = None
    ordinal: int | None = None
    kind: str
    card_variant: str
    status: ProposalStatus
    payload: dict[str, Any]
    payload_schema_version: int
    final_payload: dict[str, Any] | None = None
    dedup_key: str | None = None
    expires_at: datetime | None = None
    resolved_at: datetime | None = None
    applied_at: datetime | None = None
    resolution_trace: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IntegrationProposalResolveRequest(BaseModel):
    """Body for ``POST .../proposals/{proposal_id}/resolve``.

    - ``action="approve"`` → validate the (possibly-edited) payload against the
      kind's schema, then apply it through the kind's server-side resolver.
      Status becomes ``applied`` on success, ``failed`` on an apply error (the
      row stays for re-review).
    - ``action="reject"`` / ``"cancel"`` → status ``rejected``, no apply.

    ``payload`` overrides the declared ``proposed_payload`` on approve (user
    edits in the review modal). Ignored on reject / cancel.
    """

    model_config = ConfigDict(extra="forbid")

    action: ResolveAction
    payload: dict[str, Any] | None = None
    note: str | None = None


__all__ = [
    "IntegrationProposalResolveRequest",
    "IntegrationProposalResponse",
    "ResolveAction",
    "proposal_response",
]
