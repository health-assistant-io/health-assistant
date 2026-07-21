"""Modular link-proposal helpers shared by every ``propose_*`` HITL tool.

Single source of truth for *which* links the AI may propose between a primary
entity (the one being created/added) and existing catalog items / instances.
Used by:

* ``propose_prescribe_medication`` / ``propose_define_medication`` /
  ``propose_define_biomarker`` / ``propose_create_clinical_event``
  — each accepts an optional ``links=[...]`` argument that is validated +
  snapshotted here, then carried into the HITL task payload so the form can
  render + commit them after the primary write succeeds.
* The ``get_link_schema`` read tool (LLM discovery) — returns the matrix slice
  for a given ``src_type`` so the LLM knows what destinations + relations are
  valid before calling any ``propose_*`` tool.
* The frontend ``<LinksSection>`` component — fetches the same matrix via
  ``GET /concept-edges/schema`` so each create form auto-discovers the link
  types it supports based on its primary ``srcType`` (no per-form declaration).

Design principles:

* **Form is the authority.** A form declares only ``srcType``; the matrix
  decides what destinations + relations are offered. No per-form link code.
* **AI never writes.** ``build_link_specs`` validates + snapshots; the actual
  write goes through the canonical ``POST /concept-edges`` endpoint after the
  human approves.
* **Defence in depth.** The matrix is enforced (a) here in the tool layer,
  (b) in the ``ConceptEdgeCreate`` Pydantic schema, (c) in
  ``ConceptService.create_edge`` (which also checks concept endpoints exist).
* **Drop and report.** Invalid link combinations are silently dropped by
  ``build_link_specs``; the calling tool reports the kept vs dropped counts
  in its LLM-facing result so the agent can self-correct on the next turn.
* **Dedup with badge.** When the primary already exists, ``build_link_specs``
  queries ``concept_edges`` for an existing ``(src, dst, relation)`` match;
  the form renders an "Link exists" badge and skips it on submit.
* **No PHI in payload.** Only ``{id, label, icon, color, kind}`` snapshots.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.concept_model import ConceptEdge
from app.models.enums import ConceptRelationType, EdgeEndpointType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LINK_SCHEMA — single source of truth for valid (src_type, dst_type) → relations
# ---------------------------------------------------------------------------
#
# Consumed by:
#   1. ``validate_relation_combo`` — server-side guard
#   2. ``get_link_schema`` tool + ``GET /concept-edges/schema`` REST endpoint
#   3. Frontend ``<LinksSection>`` (filters destination + relation dropdowns)
#
# A form's "what links it supports" is just LINK_SCHEMA filtered by
# ``src_type``. No per-form declaration code.
#
# Keep entries conservative:
#   - Don't add combos already covered by a direct FK column (e.g. a biomarker's
#     ``class_concept_id`` is its primary classification — edges are for the
#     M:N / cross-domain cases the FK can't express).
#   - Don't add combos covered by specialized M:N tables with their own
#     metadata columns (e.g. ``EventExaminationLink`` carries ``reason``).
LINK_SCHEMA: Dict[
    Tuple[EdgeEndpointType, EdgeEndpointType], List[ConceptRelationType]
] = {
    # --- biomarker ------------------------------------------------------------
    (EdgeEndpointType.BIOMARKER, EdgeEndpointType.CONCEPT): [
        ConceptRelationType.MEMBER_OF,  # panel membership
        ConceptRelationType.INDICATES,  # indicates a disease
        ConceptRelationType.CORRELATES_WITH,
        ConceptRelationType.AFFECTS,  # affects an organ / system
        ConceptRelationType.MONITORS,  # monitors a condition
    ],
    (EdgeEndpointType.BIOMARKER, EdgeEndpointType.CLINICAL_EVENT_TYPE): [
        ConceptRelationType.MONITORS,
        ConceptRelationType.INDICATES,
    ],
    (EdgeEndpointType.BIOMARKER, EdgeEndpointType.ANATOMY): [
        ConceptRelationType.AFFECTS,
        ConceptRelationType.LOCATED_IN,
    ],
    # --- medication -----------------------------------------------------------
    (EdgeEndpointType.MEDICATION, EdgeEndpointType.CONCEPT): [
        ConceptRelationType.TREATS,
        ConceptRelationType.CONTRAINDICATES,
        ConceptRelationType.INDICATES,
        ConceptRelationType.RISK_OF,  # drug-induced condition
    ],
    (EdgeEndpointType.MEDICATION, EdgeEndpointType.BIOMARKER): [
        ConceptRelationType.MONITORS,  # drug-level monitoring
        ConceptRelationType.CORRELATES_WITH,
    ],
    (EdgeEndpointType.MEDICATION, EdgeEndpointType.CLINICAL_EVENT_TYPE): [
        ConceptRelationType.TREATS,
        ConceptRelationType.INDICATES,
        ConceptRelationType.CONTRAINDICATES,
    ],
    # --- allergy --------------------------------------------------------------
    (EdgeEndpointType.ALLERGY, EdgeEndpointType.CONCEPT): [
        ConceptRelationType.INDICATES,
        ConceptRelationType.CORRELATES_WITH,
    ],
    (EdgeEndpointType.ALLERGY, EdgeEndpointType.ANATOMY): [
        ConceptRelationType.AFFECTS,
    ],
    # --- vaccine / immunization ----------------------------------------------
    (EdgeEndpointType.IMMUNIZATION, EdgeEndpointType.CONCEPT): [
        ConceptRelationType.PREVENTS,
        ConceptRelationType.INDICATES,
    ],
    (EdgeEndpointType.IMMUNIZATION, EdgeEndpointType.CLINICAL_EVENT_TYPE): [
        ConceptRelationType.PREVENTS,
    ],
    # --- clinical event type --------------------------------------------------
    (EdgeEndpointType.CLINICAL_EVENT_TYPE, EdgeEndpointType.CONCEPT): [
        ConceptRelationType.MEMBER_OF,
        ConceptRelationType.CLASSIFIED_AS,
        ConceptRelationType.INDICATES,
        ConceptRelationType.CORRELATES_WITH,
    ],
    (EdgeEndpointType.CLINICAL_EVENT_TYPE, EdgeEndpointType.BIOMARKER): [
        ConceptRelationType.MONITORS,
        ConceptRelationType.INDICATES,
    ],
    (EdgeEndpointType.CLINICAL_EVENT_TYPE, EdgeEndpointType.MEDICATION): [
        ConceptRelationType.TREATS,
        ConceptRelationType.INDICATES,
    ],
    (EdgeEndpointType.CLINICAL_EVENT_TYPE, EdgeEndpointType.ANATOMY): [
        ConceptRelationType.LOCATED_IN,
    ],
    # --- anatomy hierarchy ----------------------------------------------------
    (EdgeEndpointType.ANATOMY, EdgeEndpointType.ANATOMY): [
        ConceptRelationType.BRANCH_OF,
        ConceptRelationType.DRAINS_INTO,
        ConceptRelationType.ARTICULATES_WITH,
        ConceptRelationType.INNERVATED_BY,
        ConceptRelationType.SUPPLIED_BY,
        ConceptRelationType.PART_OF,
        ConceptRelationType.CONTINUOUS_WITH,
        ConceptRelationType.LOCATED_IN,
    ],
    # --- concept ↔ concept (rich; covers specialty→disease, disease→organ) ---
    (EdgeEndpointType.CONCEPT, EdgeEndpointType.CONCEPT): [
        ConceptRelationType.MEMBER_OF,
        ConceptRelationType.PART_OF,
        ConceptRelationType.CLASSIFIED_AS,
        ConceptRelationType.CORRELATES_WITH,
        ConceptRelationType.RISK_OF,
        ConceptRelationType.CAUSED_BY,
        ConceptRelationType.AFFECTS,
        ConceptRelationType.TREATS,
        ConceptRelationType.PREVENTS,
        ConceptRelationType.CONTRAINDICATES,
        ConceptRelationType.SCREENS_FOR,
        ConceptRelationType.INDICATES,
        ConceptRelationType.EXAMINES,
        ConceptRelationType.IMAGES,
    ],
    # --- doctor (multi-specialty beyond the single specialty_concept_id FK) --
    (EdgeEndpointType.DOCTOR, EdgeEndpointType.CONCEPT): [
        ConceptRelationType.HAS_SPECIALTY,
    ],
    # --- instance links -------------------------------------------------------
    (EdgeEndpointType.EXAMINATION, EdgeEndpointType.CLINICAL_EVENT_TYPE): [
        ConceptRelationType.INDICATES,
    ],
    (EdgeEndpointType.EXAMINATION, EdgeEndpointType.CONCEPT): [
        ConceptRelationType.CLASSIFIED_AS,
    ],
    (EdgeEndpointType.DOCUMENT, EdgeEndpointType.CONCEPT): [
        ConceptRelationType.CLASSIFIED_AS,
    ],
}


# ---------------------------------------------------------------------------
# Matrix queries (pure functions)
# ---------------------------------------------------------------------------


def validate_relation_combo(
    src_type: EdgeEndpointType,
    relation: ConceptRelationType,
    dst_type: EdgeEndpointType,
) -> Tuple[bool, str]:
    """Return ``(ok, reason)``. ``reason`` is empty on success, a short
    human-readable hint on failure.

    Called from ``build_link_specs`` (tool layer) AND from the frontend via
    ``GET /concept-edges/schema`` — single validation contract.
    """
    allowed = LINK_SCHEMA.get((src_type, dst_type))
    if allowed is None:
        return (
            False,
            f"No relations defined for {src_type.value} -> {dst_type.value}",
        )
    if relation not in allowed:
        return (
            False,
            (
                f"{relation.value} not valid for "
                f"{src_type.value} -> {dst_type.value}. "
                f"Use: {[r.value for r in allowed]}"
            ),
        )
    return True, ""


def relations_for(
    src_type: EdgeEndpointType,
    dst_type: EdgeEndpointType,
) -> List[str]:
    """Valid relation strings for a specific endpoint pair."""
    return [r.value for r in LINK_SCHEMA.get((src_type, dst_type), [])]


def relations_for_source(src_type: EdgeEndpointType) -> Dict[str, List[str]]:
    """All destination types + valid relations for a given source type.

    Used by ``get_link_schema(src_type=...)`` and the form's ``<LinksSection>``
    to enumerate the destinations it should offer "Add link to {dstType}" for.
    """
    out: Dict[str, List[str]] = {}
    for (s, d), relations in LINK_SCHEMA.items():
        if s is src_type:
            out[d.value] = [r.value for r in relations]
    return out


def serialize_full_schema() -> List[Dict[str, Any]]:
    """Stable JSON-serializable view of the entire matrix.

    Returned by ``GET /concept-edges/schema`` with no arguments; also useful
    for snapshotting in tests.
    """
    out: List[Dict[str, Any]] = []
    for (src, dst), relations in LINK_SCHEMA.items():
        out.append(
            {
                "src_type": src.value,
                "dst_type": dst.value,
                "relations": [r.value for r in relations],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Endpoint resolution (UUID passthrough + name/slug fallback)
# ---------------------------------------------------------------------------

# Maps an EdgeEndpointType to the catalog_search_service type key. Endpoints
# not listed here (observation, doctor, examination, document) cannot be
# resolved by text — they require a UUID (they're patient-instance rows, not
# catalog entries).
_CATALOG_TYPE_BY_ENDPOINT: Dict[EdgeEndpointType, str] = {
    EdgeEndpointType.BIOMARKER: "biomarker",
    EdgeEndpointType.MEDICATION: "medication",
    EdgeEndpointType.ALLERGY: "allergy",
    EdgeEndpointType.ANATOMY: "anatomy",
    EdgeEndpointType.IMMUNIZATION: "vaccine",
    EdgeEndpointType.CONCEPT: "concept",
    EdgeEndpointType.CLINICAL_EVENT_TYPE: "clinical_event_type",
}


def _try_parse_uuid(value: Any) -> Optional[UUID]:
    """Parse ``value`` as a UUID, returning None on failure. Tolerates the
    str-UUID forms commonly produced by the LLM."""
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


async def _fetch_endpoint_by_id(
    db: AsyncSession, etype: EdgeEndpointType, eid: UUID
) -> Optional[Dict[str, Any]]:
    """Fetch a single endpoint by UUID using the bulk resolver registry.
    Returns None if no row exists for that id (NOT a fallback payload)."""
    # Local import keeps the module import-light (avoids pulling the entire
    # model graph at module-load time when this is imported by hitl_proposals).
    from app.services.concept_endpoint_resolver import _RESOLVERS

    resolver = _RESOLVERS.get(etype)
    if resolver is None:
        return None
    out = await resolver(db, [eid])
    return out.get(eid)


async def _resolve_endpoint_by_text(
    db: AsyncSession,
    tenant_id: UUID,
    etype: EdgeEndpointType,
    text: str,
) -> Optional[Dict[str, Any]]:
    """Resolve a non-UUID identifier (slug or name) via catalog search.

    Returns None for endpoint types that have no catalog (instances).
    """
    catalog_type = _CATALOG_TYPE_BY_ENDPOINT.get(etype)
    if catalog_type is None:
        return None
    try:
        from app.services.catalog_search_service import search_catalogs
    except ImportError:
        logger.exception("catalog_search_service unavailable")
        return None

    try:
        hits = await search_catalogs(
            db,
            tenant_id,
            text,
            types=[catalog_type],
            limit_total=1,
            enrich=True,
        )
    except Exception:
        logger.exception("catalog search failed for endpoint resolution")
        return None
    if not hits:
        return None
    hit = hits[0]
    label = hit.get("label") or hit.get("name") or hit.get("slug") or text
    return {
        "type": etype.value,
        "id": str(hit["id"]),
        "label": label,
        "icon": hit.get("icon"),
        "color": hit.get("color"),
        "kind": hit.get("kind"),
    }


async def resolve_endpoint(
    db: AsyncSession,
    tenant_id: UUID,
    etype: EdgeEndpointType,
    identifier: Any,
) -> Optional[Dict[str, Any]]:
    """Resolve a single endpoint identifier (UUID, slug, or name).

    Returns the standard ``{type, id, label, icon, color, kind}`` payload,
    or ``None`` if the endpoint cannot be found.

    UUIDs are queried directly against the resolver registry; non-UUID
    identifiers fall back to ``search_catalogs``. Endpoint types without a
    catalog (observation, doctor, examination, document) require a UUID.
    """
    eid = _try_parse_uuid(identifier)
    if eid is not None:
        return await _fetch_endpoint_by_id(db, etype, eid)
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    return await _resolve_endpoint_by_text(db, tenant_id, etype, identifier.strip())


# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------


async def check_existing_edge(
    db: AsyncSession,
    tenant_id: UUID,
    src_id: UUID,
    dst_id: UUID,
    relation: ConceptRelationType,
) -> Optional[Dict[str, Any]]:
    """Return ``{id, status}`` for an existing ``(src, dst, relation)`` edge
    (any status, tenant-scoped OR global), else None.

    Looks at all statuses so the form can surface both approved duplicates and
    pending AI-proposed duplicates.
    """
    stmt = select(ConceptEdge).where(
        ConceptEdge.src_id == src_id,
        ConceptEdge.dst_id == dst_id,
        ConceptEdge.relation == relation,
        (ConceptEdge.tenant_id == tenant_id) | (ConceptEdge.tenant_id.is_(None)),
    )
    row = (await db.execute(stmt)).scalars().first()
    if row is None:
        return None
    status = row.status.value if hasattr(row.status, "value") else row.status
    return {"id": str(row.id), "status": status}


# ---------------------------------------------------------------------------
# Orchestrator — called by every propose_* tool that accepts a ``links`` arg
# ---------------------------------------------------------------------------


async def build_link_specs(
    db: AsyncSession,
    tenant_id: UUID,
    src_type: EdgeEndpointType,
    raw_links: Optional[List[Dict[str, Any]]],
    *,
    primary_existing_id: Optional[UUID] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Validate + snapshot a list of proposed links.

    Args:
        db: AsyncSession for endpoint resolution + dedup queries.
        tenant_id: Tenant scope for catalog search + dedup.
        src_type: The primary entity's endpoint type (e.g. MEDICATION for
            ``propose_prescribe_medication``). The destination type is taken from
            each raw link.
        raw_links: The LLM-provided list. Each item should carry
            ``dst_type``, ``dst_id`` (UUID/slug/name), ``relation``, and an
            optional ``properties`` dict. Missing or malformed items are
            dropped with a reason.
        primary_existing_id: UUID of the primary entity IF it already exists
            (e.g. the standalone ``propose_link`` tool operating on two
            existing entities). Pass ``None`` when the primary is being
            newly created — dedup is skipped in that case (there can't be an
            existing edge to a not-yet-existing source).

    Returns:
        ``{"kept": [link_spec, ...], "dropped": [{"raw": ..., "reason": ...}]}``

        Each kept ``link_spec`` has the shape::

            {
              "dst":  {"type","id","label","icon","color","kind"},
              "relation": "TREATS",
              "properties": {...},
              "duplicate_of": null | "<edge-uuid>"
            }
    """
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []

    if not raw_links:
        return {"kept": kept, "dropped": dropped}

    for raw in raw_links:
        if not isinstance(raw, dict):
            dropped.append({"raw": raw, "reason": "link spec must be an object"})
            continue

        dst_type_raw = raw.get("dst_type")
        dst_identifier = raw.get("dst_id")
        relation_raw = raw.get("relation")
        properties = raw.get("properties") or {}

        if not dst_type_raw or not dst_identifier or not relation_raw:
            dropped.append(
                {
                    "raw": raw,
                    "reason": "missing required field (dst_type/dst_id/relation)",
                }
            )
            continue

        try:
            dst_type = EdgeEndpointType(dst_type_raw)
            relation = ConceptRelationType(relation_raw)
        except ValueError as exc:
            dropped.append({"raw": raw, "reason": f"invalid enum value: {exc}"})
            continue

        ok, why = validate_relation_combo(src_type, relation, dst_type)
        if not ok:
            dropped.append({"raw": raw, "reason": why})
            continue

        dst_payload = await resolve_endpoint(
            db, tenant_id, dst_type, str(dst_identifier)
        )
        if dst_payload is None:
            dropped.append(
                {
                    "raw": raw,
                    "reason": (
                        f"destination {dst_type_raw}:{dst_identifier!r} "
                        "not found or not accessible"
                    ),
                }
            )
            continue

        duplicate_of: Optional[str] = None
        if primary_existing_id is not None:
            try:
                dst_uuid = UUID(dst_payload["id"])
            except (ValueError, TypeError):
                dst_uuid = None
            if dst_uuid is not None:
                existing = await check_existing_edge(
                    db, tenant_id, primary_existing_id, dst_uuid, relation
                )
                if existing is not None:
                    duplicate_of = existing["id"]

        kept.append(
            {
                "dst": dst_payload,
                "relation": relation.value,
                "properties": properties if isinstance(properties, dict) else {},
                "duplicate_of": duplicate_of,
            }
        )

    return {"kept": kept, "dropped": dropped}


__all__ = [
    "LINK_SCHEMA",
    "build_link_specs",
    "check_existing_edge",
    "relations_for",
    "relations_for_source",
    "resolve_endpoint",
    "serialize_full_schema",
    "validate_relation_combo",
]
