"""SDK specs + server-side resolvers for the integration HITL-proposal flow.

Providers opt into human-in-the-loop proposals by:

1. Declaring the ``hitl_proposal`` data class in the manifest's
   ``itemSchema`` (declared data, gated at registration).
2. Implementing :meth:`BaseHealthProvider.pull_hitl_proposals` to return a
   list of :class:`IntegrationProposalSpec` objects.
3. Optionally implementing :meth:`BaseHealthProvider.handle_proposal_resolution`
   to react when the user resolves a proposal (advance a cursor so the
   next sync doesn't re-propose, log audit, etc.) — default no-op.

The platform's ``run_sync`` calls ``pull_hitl_proposals``, persists each spec
as a PROPOSED row in the one ``proposals`` table (plan 5), and fires an HITL
notification. The user resolves via
``/api/v1/integrations/instance/{id}/proposals/{proposal_id}/resolve``; on
approve, the resolver validates the user-approved ``final_payload`` against
the kind's contract schema and then writes through the same services the UI
uses:

* catalog kinds → :func:`app.services.catalog.catalog_proposal_service.apply_proposal`;
* patient-record kinds (``create_event``, ``create_examination``,
  ``add_medication``, ``add_biomarker_to_examination``) → the resolvers in
  this module, which route through the canonical clinical services.

This module mirrors :mod:`app.extensions.sdk.catalog` (Pydantic specs with
``Literal`` discriminators) and :mod:`app.extensions.sdk.notifications`
(spec + builder style).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# proposal_type (kind id) discriminator
# ---------------------------------------------------------------------------

#: The proposal-kind vocabulary an integration may emit. Kind ids are the
#: contract ids in ``hitl-proposal-kinds.json`` (seeded into
#: ``proposal_kinds``); the chat-side card keys live in the kind's
#: ``card_variant``. The first four write to the patient record; the last four
#: contribute to the catalog.
IntegrationProposalType = Literal[
    "create_event",
    "create_examination",
    "add_medication",
    "add_biomarker_to_examination",
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
    :func:`app.services.integration.integration_proposal_service.create_proposal` to
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
    proposed_payload: dict[str, Any] = Field(
        ...,
        description=(
            "Kind-specific payload. See "
            ":class:`integrations.sdk.catalog.CatalogProposal` for the "
            "expected fields per kind."
        ),
    )
    patient_id: UUID | None = Field(
        default=None,
        description=(
            "Optional patient scope when the proposal is about a specific "
            "patient's data. Catalog proposals (biomarker/medication/"
            "concept/edge definitions) are tenant-wide and leave this "
            "unset."
        ),
    )
    context: dict[str, Any] = Field(
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
    final_payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The payload that was actually applied (after the user's "
            "edits, if any). Includes ``_applied_entity_id`` (UUID of "
            "the created/updated catalog row) and ``_dedup_no_op`` "
            "(True when the apply found an existing entry and did not "
            "write a new one)."
        ),
    )
    applied_entity_id: UUID | None = Field(
        default=None,
        description=(
            "UUID of the catalog row created/updated on approve. ``None`` "
            "if the apply failed (see ``error``) or was a no-op."
        ),
    )
    error: str | None = Field(
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
    slug: str | None = None,
    category: str | None = None,
    coding_system: str | None = None,
    code: str | None = None,
    preferred_unit_symbol: str | None = None,
    reference_range_min: float | None = None,
    reference_range_max: float | None = None,
    aliases: list | None = None,
    info: str | None = None,
    is_telemetry: bool | None = None,
    context: dict[str, Any] | None = None,
    patient_id: UUID | None = None,
) -> IntegrationProposalSpec:
    """Build a ``proposal_type="create_biomarker_definition"`` HITL spec.

    Wraps the same payload shape as
    :func:`integrations.sdk.catalog.biomarker_proposal` but tags it for
    human review instead of auto-apply.
    """
    payload: dict[str, Any] = {"name": name}
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
    description: str | None = None,
    indications: str | None = None,
    dosage_info: str | None = None,
    contraindications: str | None = None,
    side_effects: list | None = None,
    context: dict[str, Any] | None = None,
    patient_id: UUID | None = None,
) -> IntegrationProposalSpec:
    """Build a ``proposal_type="create_medication_definition"`` HITL spec."""
    payload: dict[str, Any] = {"name": name}
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
    description: str | None = None,
    coding_system: str | None = None,
    code: str | None = None,
    aliases: list | None = None,
    context: dict[str, Any] | None = None,
    patient_id: UUID | None = None,
) -> IntegrationProposalSpec:
    """Build a ``proposal_type="create_concept"`` HITL spec.

    ``kind`` is a lowercase :class:`~app.models.enums.ConceptKind` value
    (e.g. ``"disease"``, ``"body_system"``).
    """
    payload: dict[str, Any] = {"slug": slug, "name": name, "kind": kind}
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
    properties: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    patient_id: UUID | None = None,
) -> IntegrationProposalSpec:
    """Build a ``proposal_type="create_edge"`` HITL spec.

    Endpoint types + relation must be valid
    :class:`~app.models.enums.EdgeEndpointType` /
    :class:`~app.models.enums.ConceptRelationType` values. Concept
    endpoints must already exist (or be proposed earlier in the same
    batch).
    """
    payload: dict[str, Any] = {
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


# ---------------------------------------------------------------------------
# Server-side patient-record resolvers (plan 5 W3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PatientRecordWrite:
    """Outcome of applying one patient-record proposal."""

    kind: str
    entity_id: UUID
    detail: str | None = None


def _require_patient_id(payload: dict[str, Any], kind: str) -> UUID:
    raw = payload.get("patient_id")
    if raw in (None, ""):
        raise ValueError(f"{kind} proposal requires a patient_id (row scope or payload)")
    return UUID(str(raw))


async def resolve_create_event(
    db: Any,
    actor: Any,
    payload: dict[str, Any],
    *,
    integration_id: UUID | None = None,
    external_id: str | None = None,
) -> PatientRecordWrite:
    """Apply a ``create_event`` proposal through ``clinical_event_service``."""
    from app.extensions.sdk.proposal_schemas import CreateEventPayload
    from app.schemas.clinical_event import ClinicalEventCreate
    from app.services.clinical.clinical_event_service import create_event

    validated = CreateEventPayload.model_validate(payload).model_dump(mode="json")
    patient_id = _require_patient_id(validated, "create_event")
    event_data = {**validated, "patient_id": patient_id}
    created = await create_event(
        db,
        actor,
        ClinicalEventCreate(**event_data),
        source_integration_id=integration_id,
        external_id=external_id,
    )
    return PatientRecordWrite(
        kind="create_event",
        entity_id=UUID(str(created["id"])),
        detail=created.get("title"),
    )


async def resolve_create_examination(
    db: Any,
    actor: Any,
    payload: dict[str, Any],
    *,
    integration_id: UUID | None = None,
    external_id: str | None = None,
) -> PatientRecordWrite:
    """Apply a ``create_examination`` proposal through ``examination_service``."""
    from app.extensions.sdk.proposal_schemas import CreateExaminationPayload
    from app.schemas.examination import ExaminationCreate
    from app.services.clinical.examination_service import create_examination

    validated = CreateExaminationPayload.model_validate(payload).model_dump(mode="json")
    patient_id = _require_patient_id(validated, "create_examination")
    exam_data = {**validated, "patient_id": patient_id}
    created = await create_examination(
        db,
        actor,
        ExaminationCreate(**exam_data),
        source_integration_id=integration_id,
        external_id=external_id,
    )
    return PatientRecordWrite(
        kind="create_examination",
        entity_id=created.id,
        detail=created.notes,
    )


async def resolve_add_medication(
    db: Any,
    actor: Any,
    payload: dict[str, Any],
    *,
    integration_id: UUID | None = None,
    external_id: str | None = None,
) -> PatientRecordWrite:
    """Apply an ``add_medication`` proposal through ``medication_service``."""
    from app.extensions.sdk.proposal_schemas import AddMedicationPayload
    from app.schemas.medication import MedicationRecordCreate
    from app.services.clinical.medication_service import add_patient_medication

    validated = AddMedicationPayload.model_validate(payload).model_dump(mode="json")
    patient_id = _require_patient_id(validated, "add_medication")
    medication_data = {**validated, "patient_id": patient_id}
    created = await add_patient_medication(
        db,
        actor,
        MedicationRecordCreate(**medication_data),
        source_integration_id=integration_id,
        external_id=external_id,
    )
    return PatientRecordWrite(
        kind="add_medication",
        entity_id=created.id,
        detail=(created.code or {}).get("text"),
    )


async def resolve_add_biomarker_to_examination(
    db: Any,
    actor: Any,
    payload: dict[str, Any],
    *,
    integration_id: UUID | None = None,
    external_id: str | None = None,
) -> PatientRecordWrite:
    """Apply an ``add_biomarker_to_examination`` proposal as an Observation.

    Routes through ``fhir_service.create_observation`` — the same service the
    ``POST /observations`` endpoint uses — after an explicit tenant-scoped
    patient access check (the observation service has no actor parameter).
    """
    from app.extensions.sdk.proposal_schemas import AddBiomarkerPayload
    from app.services.auth.access import check_patient_access
    from app.services.fhir.fhir_service import create_observation

    validated = AddBiomarkerPayload.model_validate(payload).model_dump(mode="json")
    patient_id = _require_patient_id(validated, "add_biomarker_to_examination")
    await check_patient_access(patient_id, actor, db)
    observation_data = {**validated, "patient_id": patient_id}
    if external_id is not None:
        observation_data.setdefault("external_id", external_id)
    created = await create_observation(observation_data, actor.tenant_id)
    if created is None:
        raise ValueError("add_biomarker_to_examination: observation write failed")
    return PatientRecordWrite(
        kind="add_biomarker_to_examination",
        entity_id=created.id,
        detail=(created.code or {}).get("text"),
    )


#: Patient-record kind → resolver. The catalog kinds route through
#: ``catalog_proposal_service`` instead (see ``proposal_resolvers``).
PATIENT_RECORD_RESOLVERS: dict[str, Callable[..., Any]] = {
    "create_event": resolve_create_event,
    "create_examination": resolve_create_examination,
    "add_medication": resolve_add_medication,
    "add_biomarker_to_examination": resolve_add_biomarker_to_examination,
}


__all__ = [
    "PATIENT_RECORD_RESOLVERS",
    "IntegrationProposalSpec",
    "IntegrationProposalType",
    "PatientRecordWrite",
    "ProposalOutcome",
    "biomarker_hitl_proposal",
    "concept_hitl_proposal",
    "edge_hitl_proposal",
    "medication_hitl_proposal",
    "resolve_add_biomarker_to_examination",
    "resolve_add_medication",
    "resolve_create_event",
    "resolve_create_examination",
]
