"""Catalog-proposal authoring helpers for integration providers.

Providers that discover new catalog entries upstream (a wearable integration
that adds a new "Sleep Quality" metric, a hospital integration that knows
about a disease→biomarker mapping the local catalog doesn't, …) opt into
catalog contributions by:

1. Overriding :meth:`BaseHealthProvider.supports_catalog_proposals` to return
   ``True``.
2. Implementing :meth:`BaseHealthProvider.pull_catalog_proposals` to return a
   list of :class:`CatalogProposal` objects.

The platform calls these hooks from ``integration_sync_service.run_sync``
after observations + clinical events + examinations have been processed.
Each proposal is routed through
:func:`app.services.catalog_proposal_service.apply_proposal`, which dispatches
by ``kind`` to the appropriate service-layer write path
(``BiomarkerDefinition`` / ``MedicationCatalog`` / ``ConceptService.create_concept``
/ ``ConceptService.create_edge``) and stamps ``ConceptProvenance.INTEGRATION``
provenance where the underlying model supports it.

This module mirrors :mod:`integrations.sdk.notifications` (Pydantic spec +
small helpers that document the payload contract in one place).
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Kind discriminator
# ---------------------------------------------------------------------------

#: The four catalog-entity kinds an integration can propose. Each maps to a
#: payload shape documented on :class:`CatalogProposal` below.
CatalogProposalKind = Literal["biomarker", "medication", "concept", "edge"]


# ---------------------------------------------------------------------------
# Proposal spec
# ---------------------------------------------------------------------------


class CatalogProposal(BaseModel):
    """One catalog contribution from an integration.

    The platform's ``run_sync`` pipeline calls
    :func:`app.services.catalog_proposal_service.apply_proposal` on each
    proposal returned by ``pull_catalog_proposals``. The proposal is
    stateless — once applied, the resulting catalog row is owned by the
    integration's tenant (with ``AuditMixin.created_by`` set to the owning
    user) and the proposal itself is not persisted. Re-applying the same
    proposal on the next sync is a no-op (idempotent on the natural key of
    each kind).

    Payload shape per ``kind`` (mirrors the chat HITL ``proposed_payload``
    shape at ``app/ai/tools/hitl_proposals.py`` so a future unified review
    UI can render both sources identically):

    ``kind="biomarker"`` — fields mirror ``BiomarkerCreate`` /
    ``propose_define_biomarker``::

        {
            "name": "Sleep Quality Score",
            "slug": "sleep_quality_score",         # optional; derived from name if absent
            "category": "Sleep",                   # optional; resolved to a biomarker_class concept
            "coding_system": "custom",             # optional; default "loinc"
            "code": "HKSleepQualityScore",         # optional
            "preferred_unit_symbol": "score",      # optional
            "reference_range_min": 0.0,            # optional
            "reference_range_max": 100.0,          # optional
            "aliases": ["sleep_index"],            # optional
            "info": "Wearable-derived sleep score",# optional
            "is_telemetry": True                   # optional; default False
        }

    ``kind="medication"`` — fields mirror ``MedicationCatalogCreate`` /
    ``propose_define_medication``::

        {
            "name": "Melatonin",
            "description": "...",
            "indications": "...",
            "dosage_info": "...",
            "contraindications": "...",
            "side_effects": ["drowsiness"]
        }

    ``kind="concept"`` — fields mirror ``ConceptService.create_concept``::

        {
            "slug": "sleep_disorder",
            "name": "Sleep Disorder",
            "kind": "disease",                    # ConceptKind value
            "description": "...",
            "coding_system": "snomed",            # optional
            "code": "262513006",                  # optional
            "aliases": ["insomnia"]               # optional
        }

    ``kind="edge"`` — fields mirror ``ConceptService.create_edge``::

        {
            "src_type": "concept",                # EdgeEndpointType value
            "src_id": "<uuid>",
            "dst_type": "biomarker",              # EdgeEndpointType value
            "dst_id": "<uuid>",
            "relation": "MONITORS",               # ConceptRelationType value
            "properties": {"weight": 0.8}         # optional
        }

    Advisory fields (not required to apply; recorded in audit/meta where
    applicable):

    - ``confidence``: 0.0–1.0 — the provider's confidence in the proposal.
    - ``rationale``: human-readable note explaining the proposal.
    """

    model_config = ConfigDict(extra="forbid")

    kind: CatalogProposalKind = Field(
        ...,
        description=(
            "Catalog entity kind — routes the proposal through the matching "
            "service-layer write path."
        ),
    )
    payload: Dict[str, Any] = Field(
        ...,
        description=(
            "Kind-specific payload. See the class docstring for the "
            "expected fields per kind."
        ),
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Optional advisory confidence in the proposal (0.0–1.0). "
            "Recorded in audit/meta where applicable; not used to gate "
            "application."
        ),
    )
    rationale: Optional[str] = Field(
        default=None,
        description=(
            "Optional human-readable note explaining why the integration "
            "proposed this entry. Recorded in audit/meta where applicable."
        ),
    )


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def biomarker_proposal(
    *,
    name: str,
    slug: Optional[str] = None,
    category: Optional[str] = None,
    coding_system: Optional[str] = None,
    code: Optional[str] = None,
    preferred_unit_symbol: Optional[str] = None,
    reference_range_min: Optional[float] = None,
    reference_range_max: Optional[float] = None,
    aliases: Optional[List[str]] = None,
    info: Optional[str] = None,
    is_telemetry: Optional[bool] = None,
    confidence: Optional[float] = None,
    rationale: Optional[str] = None,
) -> CatalogProposal:
    """Build a ``kind="biomarker"`` proposal without hand-writing the payload dict.

    The payload fields mirror :func:`propose_define_biomarker`
    (chat HITL) field-for-field.
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
    return CatalogProposal(
        kind="biomarker", payload=payload, confidence=confidence, rationale=rationale
    )


def medication_proposal(
    *,
    name: str,
    description: Optional[str] = None,
    indications: Optional[str] = None,
    dosage_info: Optional[str] = None,
    contraindications: Optional[str] = None,
    side_effects: Optional[List[str]] = None,
    confidence: Optional[float] = None,
    rationale: Optional[str] = None,
) -> CatalogProposal:
    """Build a ``kind="medication"`` proposal."""
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
    return CatalogProposal(
        kind="medication", payload=payload, confidence=confidence, rationale=rationale
    )


def concept_proposal(
    *,
    slug: str,
    name: str,
    kind: str,
    description: Optional[str] = None,
    coding_system: Optional[str] = None,
    code: Optional[str] = None,
    aliases: Optional[List[str]] = None,
    confidence: Optional[float] = None,
    rationale: Optional[str] = None,
) -> CatalogProposal:
    """Build a ``kind="concept"`` proposal.

    ``kind`` is the lowercase value of a :class:`ConceptKind` enum member
    (e.g. ``"disease"``, ``"biomarker_class"``, ``"body_system"``).
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
    return CatalogProposal(
        kind="concept", payload=payload, confidence=confidence, rationale=rationale
    )


def edge_proposal(
    *,
    src_type: str,
    src_id: str,
    dst_type: str,
    dst_id: str,
    relation: str,
    properties: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    confidence: Optional[float] = None,
    rationale: Optional[str] = None,
) -> CatalogProposal:
    """Build a ``kind="edge"`` proposal.

    ``src_type`` / ``dst_type`` are :class:`EdgeEndpointType` values; ``src_id``
    / ``dst_id`` are the stringified UUIDs of the endpoints; ``relation`` is a
    :class:`ConceptRelationType` value. Concept endpoints must already exist
    (or be proposed earlier in the same batch — proposals apply in the order
    the provider returns them).
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
    return CatalogProposal(
        kind="edge", payload=payload, confidence=confidence, rationale=rationale
    )


__all__ = [
    "CatalogProposal",
    "CatalogProposalKind",
    "biomarker_proposal",
    "medication_proposal",
    "concept_proposal",
    "edge_proposal",
]
