"""Pydantic schemas for the integration-proposal HITL flow (workstream G).

The flow: provider opts in via ``supports_hitl_proposals`` + emits
``IntegrationProposalSpec`` objects through ``pull_hitl_proposals``; the
engine persists each as a PROPOSED ``IntegrationProposal`` row + fires an
HITL notification; the user reviews via
``/api/v1/integrations/instance/{id}/proposals/.../resolve``.

Schemas here are the API boundary — request bodies + response shape for
the resolver endpoints. The SDK-facing spec
(:class:`integrations.sdk.proposals.IntegrationProposalSpec`) lives in
the SDK package so providers can build specs without importing app code.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import HitlTaskStatus


ResolveAction = Literal["approve", "reject", "cancel"]


class IntegrationProposalResponse(BaseModel):
    """API response shape for one ``IntegrationProposal`` row.

    Mirrors the persistent row 1:1; safe to expose to the proposal owner
    (the integration's owning user). ``resolved_payload`` carries the
    final payload (possibly user-edited) plus an ``applied_entity_id``
    key when the resolver performed a successful write.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    integration_id: UUID
    tenant_id: Optional[UUID] = None
    patient_id: Optional[UUID] = None
    proposal_type: str
    title: str
    status: HitlTaskStatus
    proposed_payload: Dict[str, Any]
    context: Dict[str, Any] = Field(default_factory=dict)
    resolved_payload: Optional[Dict[str, Any]] = None
    resolved_by: Optional[UUID] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    dedup_key: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class IntegrationProposalResolveRequest(BaseModel):
    """Body for ``POST .../proposals/{proposal_id}/resolve``.

    - ``action="approve"`` → apply the (possibly-edited) payload through
      :func:`catalog_proposal_service.apply_proposal`. On success the
      status transitions to ``CONFIRMED``; on apply-error to ``FAILED``.
    - ``action="reject"`` → status transitions to ``DISMISSED`` with no
      apply. Semantic: user reviewed and declined.
    - ``action="cancel"`` → status transitions to ``DISMISSED`` with no
      apply. Semantic: user dismissed without considering (e.g. closed the
      modal). Distinguishable from reject in audit by the ``note``.

    ``payload`` overrides ``proposed_payload`` on approve (user edits in
    the review modal). Ignored on reject / cancel.
    """

    model_config = ConfigDict(extra="forbid")

    action: ResolveAction
    payload: Optional[Dict[str, Any]] = None
    note: Optional[str] = None


__all__ = [
    "IntegrationProposalResponse",
    "IntegrationProposalResolveRequest",
    "ResolveAction",
]
