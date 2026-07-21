"""SDK specs for the integration HITL-proposal flow (workstream G).

Providers opt into human-in-the-loop catalog proposals by:

1. Overriding :meth:`BaseHealthProvider.supports_hitl_proposals` to return
   ``True`` (lands in G.3).
2. Implementing :meth:`BaseHealthProvider.pull_hitl_proposals` to return a
   list of :class:`IntegrationProposalSpec` objects (G.3).
3. Optionally implementing :meth:`BaseHealthProvider.handle_proposal_resolution`
   to react when the user resolves a proposal (advance a cursor so the
   next sync doesn't re-propose, log audit, etc.) — default no-op.

The platform's ``run_sync`` (engine wiring in G.4) calls
``pull_hitl_proposals``, persists each spec as a PROPOSED
:class:`~app.models.integration_proposal.IntegrationProposal` row, and
fires an HITL notification. The user resolves via
``/api/v1/integrations/instance/{id}/proposals/{proposal_id}/resolve``;
on approve, the resolver delegates to
:func:`app.services.catalog_proposal_service.apply_proposal` — the same
write path F.3 auto-applies through.

This module mirrors :mod:`integrations.sdk.catalog` (Pydantic specs with
``Literal`` discriminators) and :mod:`integrations.sdk.notifications`
(spec + builder style).
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# proposal_type discriminator
# ---------------------------------------------------------------------------

#: The four catalog-proposal ``proposal_type`` values the resolver supports
#: today. Names mirror the chat-side ``task_type`` strings
#: (``create_*_definition``) so a future unified review UI can render both
#: sources identically. Patient-record types (``create_event``,
#: ``create_examination``, ``add_medication``, ``add_biomarker_to_examination``)
#: are deferred — the resolver raises ``NotImplementedError`` for them. Note:
#: the chat-side tool *function* names were renamed to ``propose_define_*`` /
#: ``propose_prescribe_*`` / ``propose_record_*`` for clarity, but the
#: ``task_type`` and ``proposal_type`` strings stay stable to preserve SDK
#: alignment.
IntegrationProposalType = Literal[
    "create_biomarker_definition",
    "create_medication_definition",
    "create_concept",
    "create_edge",
]


# ---------------------------------------------------------------------------
# Spec — what a provider emits in pull_hitl_proposals
# ---------------------------------------------------------------------------


class IntegrationProposalSpec(BaseModel):
    """One HITL proposal from an integration.

    The platform's ``run_sync`` pipeline calls
    :func:`app.services.integration_proposal_service.create_proposal` to
    persist each spec as a PROPOSED row + fires an HITL notification.
    Re-emitting the same spec on consecutive syncs is a no-op (idempotent
    on the dedup key derived from ``(proposal_type, proposed_payload)``).

    The payload shape per ``proposal_type`` is documented on
    :class:`integrations.sdk.catalog.CatalogProposal` (the resolver maps
    the ``proposal_type`` to the matching catalog kind on approve).
    """

    model_config = ConfigDict(extra="forbid")

    proposal_type: IntegrationProposalType = Field(
        ...,
        description=(
            "Catalog entity kind the proposal contributes. Routes the "
            "approve path through the matching service-layer write."
        ),
    )
    title: str = Field(
        ...,
        description=(
            "Human-readable headline shown in the notification + the "
            "review card (e.g. 'Define Biomarker: Sleep Quality')."
        ),
    )
    proposed_payload: Dict[str, Any] = Field(
        ...,
        description=(
            "Kind-specific payload. See "
            ":class:`integrations.sdk.catalog.CatalogProposal` for the "
            "expected fields per kind."
        ),
    )
    patient_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Optional patient scope when the proposal is about a specific "
            "patient's data. Catalog proposals (biomarker/medication/"
            "concept/edge definitions) are tenant-wide and leave this "
            "unset."
        ),
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional snapshot at proposal time — upstream source, "
            "observed codes, etc. Recorded on the row for audit; not "
            "consumed by the resolver."
        ),
    )


# ---------------------------------------------------------------------------
# Outcome — what the resolver hands back to handle_proposal_resolution
# ---------------------------------------------------------------------------


class ProposalOutcome(BaseModel):
    """Outcome of a resolve, passed to the provider's
    ``handle_proposal_resolution`` callback.

    Only fired on ``action="approve"`` — reject/cancel have no apply step
    so there's nothing for the provider to react to.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["approve"] = "approve"
    final_payload: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The payload that was actually applied (after the user's "
            "edits, if any). Includes ``_applied_entity_id`` (UUID of "
            "the created/updated catalog row) and ``_dedup_no_op`` "
            "(True when the apply found an existing entry and did not "
            "write a new one)."
        ),
    )
    applied_entity_id: Optional[UUID] = Field(
        default=None,
        description=(
            "UUID of the catalog row created/updated on approve. ``None`` "
            "if the apply failed (see ``error``) or was a no-op."
        ),
    )
    error: Optional[str] = Field(
        default=None,
        description=(
            "Set when the apply failed (status=FAILED). The provider can "
            "use this to decide whether to re-propose on the next sync "
            "(transient errors) or give up (validation errors)."
        ),
    )


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def biomarker_hitl_proposal(
    *,
    title: str,
    name: str,
    slug: Optional[str] = None,
    category: Optional[str] = None,
    coding_system: Optional[str] = None,
    code: Optional[str] = None,
    preferred_unit_symbol: Optional[str] = None,
    reference_range_min: Optional[float] = None,
    reference_range_max: Optional[float] = None,
    aliases: Optional[list] = None,
    info: Optional[str] = None,
    is_telemetry: Optional[bool] = None,
    context: Optional[Dict[str, Any]] = None,
    patient_id: Optional[UUID] = None,
) -> IntegrationProposalSpec:
    """Build a ``proposal_type="create_biomarker_definition"`` HITL spec.

    Wraps the same payload shape as
    :func:`integrations.sdk.catalog.biomarker_proposal` but tags it for
    human review instead of auto-apply.
    """
    payload: Dict[str, Any] = {"name": name}
    if slug is not None:
        payload["slug"] = slug
    if category is not None:
        payload["category"] = category
    if coding_system is not None:
        payload["coding_system"] = coding_system
    if code is not None:
        payload["code"] = code
    if preferred_unit_symbol is not None:
        payload["preferred_unit_symbol"] = preferred_unit_symbol
    if reference_range_min is not None:
        payload["reference_range_min"] = reference_range_min
    if reference_range_max is not None:
        payload["reference_range_max"] = reference_range_max
    if aliases is not None:
        payload["aliases"] = aliases
    if info is not None:
        payload["info"] = info
    if is_telemetry is not None:
        payload["is_telemetry"] = is_telemetry
    return IntegrationProposalSpec(
        proposal_type="create_biomarker_definition",
        title=title,
        proposed_payload=payload,
        context=context or {},
        patient_id=patient_id,
    )


def medication_hitl_proposal(
    *,
    title: str,
    name: str,
    description: Optional[str] = None,
    indications: Optional[str] = None,
    dosage_info: Optional[str] = None,
    contraindications: Optional[str] = None,
    side_effects: Optional[list] = None,
    context: Optional[Dict[str, Any]] = None,
    patient_id: Optional[UUID] = None,
) -> IntegrationProposalSpec:
    """Build a ``proposal_type="create_medication_definition"`` HITL spec."""
    payload: Dict[str, Any] = {"name": name}
    if description is not None:
        payload["description"] = description
    if indications is not None:
        payload["indications"] = indications
    if dosage_info is not None:
        payload["dosage_info"] = dosage_info
    if contraindications is not None:
        payload["contraindications"] = contraindications
    if side_effects is not None:
        payload["side_effects"] = side_effects
    return IntegrationProposalSpec(
        proposal_type="create_medication_definition",
        title=title,
        proposed_payload=payload,
        context=context or {},
        patient_id=patient_id,
    )


def concept_hitl_proposal(
    *,
    title: str,
    slug: str,
    name: str,
    kind: str,
    description: Optional[str] = None,
    coding_system: Optional[str] = None,
    code: Optional[str] = None,
    aliases: Optional[list] = None,
    context: Optional[Dict[str, Any]] = None,
    patient_id: Optional[UUID] = None,
) -> IntegrationProposalSpec:
    """Build a ``proposal_type="create_concept"`` HITL spec.

    ``kind`` is a lowercase :class:`~app.models.enums.ConceptKind` value
    (e.g. ``"disease"``, ``"body_system"``).
    """
    payload: Dict[str, Any] = {"slug": slug, "name": name, "kind": kind}
    if description is not None:
        payload["description"] = description
    if coding_system is not None:
        payload["coding_system"] = coding_system
    if code is not None:
        payload["code"] = code
    if aliases is not None:
        payload["aliases"] = aliases
    return IntegrationProposalSpec(
        proposal_type="create_concept",
        title=title,
        proposed_payload=payload,
        context=context or {},
        patient_id=patient_id,
    )


def edge_hitl_proposal(
    *,
    title: str,
    src_type: str,
    src_id: str,
    dst_type: str,
    dst_id: str,
    relation: str,
    properties: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    patient_id: Optional[UUID] = None,
) -> IntegrationProposalSpec:
    """Build a ``proposal_type="create_edge"`` HITL spec.

    Endpoint types + relation must be valid
    :class:`~app.models.enums.EdgeEndpointType` /
    :class:`~app.models.enums.ConceptRelationType` values. Concept
    endpoints must already exist (or be proposed earlier in the same
    batch).
    """
    payload: Dict[str, Any] = {
        "src_type": src_type,
        "src_id": src_id,
        "dst_type": dst_type,
        "dst_id": dst_id,
        "relation": relation,
    }
    if properties is not None:
        payload["properties"] = properties
    if evidence is not None:
        payload["evidence"] = evidence
    return IntegrationProposalSpec(
        proposal_type="create_edge",
        title=title,
        proposed_payload=payload,
        context=context or {},
        patient_id=patient_id,
    )


__all__ = [
    "IntegrationProposalSpec",
    "IntegrationProposalType",
    "ProposalOutcome",
    "biomarker_hitl_proposal",
    "medication_hitl_proposal",
    "concept_hitl_proposal",
    "edge_hitl_proposal",
]
