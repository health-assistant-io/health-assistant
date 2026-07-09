"""Cross-catalog graph traversal over the polymorphic ``concept_edges`` graph.

``concept_edges`` is the single link system between every catalog type
(concept ↔ anatomy ↔ biomarker ↔ medication ↔ allergy ↔ clinical_event_type ↔
…). :func:`traverse` does a depth-bounded, cycle-safe, tenant-scoped recursive-
CTE traversal from any ``(type, id)`` start node and returns a display-ready
``{nodes, edges, start}`` payload (endpoints resolved via
:mod:`app.services.concept_endpoint_resolver`).

This is what powers "a biomarker → AFFECTS → which organ → AFFECTED_BY → which
diseases → TREATED_BY → which medications" — the headline cross-catalog query
the unified-catalog architecture exists to answer. See
``dev/plans/unified-catalog-architecture-2026-07-08.md`` §3.3.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ConceptRelationType, EdgeEndpointType
from app.services.concept_endpoint_resolver import resolve_endpoints

# The recursive CTE collects every edge reachable within ``max_depth`` hops of
# the start node. ``path`` (an edge-id array) prevents edge cycles; the final
# SELECT DISTINCT collapses the same edge reached via different paths.
_TRAVERSE_SQL = """
WITH RECURSIVE reach AS (
    SELECT id, src_type, src_id, dst_type, dst_id, relation, status,
           tenant_id, properties, 1 AS depth, ARRAY[id] AS path
    FROM concept_edges
    WHERE status = ANY(:ok_statuses)
      AND (tenant_id IS NULL OR tenant_id = :tenant_id)
      {relation_clause_anchor}
      AND (
          (src_id = :start_id AND src_type = :start_type)
          OR (dst_id = :start_id AND dst_type = :start_type)
      )
    UNION
    SELECT e.id, e.src_type, e.src_id, e.dst_type, e.dst_id, e.relation,
           e.status, e.tenant_id, e.properties, r.depth + 1, r.path || e.id
    FROM concept_edges e
    JOIN reach r ON (
        e.src_id = r.src_id OR e.src_id = r.dst_id
        OR e.dst_id = r.src_id OR e.dst_id = r.dst_id
    )
    WHERE e.status = ANY(:ok_statuses)
      AND (e.tenant_id IS NULL OR e.tenant_id = :tenant_id)
      {relation_clause_rec}
      AND r.depth < :max_depth
      AND NOT (e.id = ANY(r.path))
)
SELECT DISTINCT id, src_type, src_id, dst_type, dst_id, relation, status,
                tenant_id, properties
FROM reach
LIMIT :limit
"""


async def traverse(
    db: AsyncSession,
    start_type: EdgeEndpointType,
    start_id: UUID,
    *,
    tenant_id: Optional[UUID],
    max_depth: int = 3,
    relation_whitelist: Optional[tuple[ConceptRelationType, ...]] = None,
    include_proposed: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    """BFS-ish recursive-CTE traversal from a start node.

    Returns ``{"start": payload, "nodes": [payload, ...], "edges": [edge_dict, ...]}``
    where each ``edge_dict`` is
    ``{"id", "src": {type, id}, "dst": {type, id}, "relation", "status"}``.
    Endpoints (``nodes``) are resolved to display payloads via the resolver
    registry; the ``start`` node is always included. Tenant-scoped
    (``or_(tenant_id == caller, tenant_id IS NULL)``); proposed edges excluded
    unless ``include_proposed``.
    """
    if max_depth < 1 or max_depth > 5:
        raise ValueError("max_depth must be between 1 and 5")

    ok_statuses = ["approved", "proposed"] if include_proposed else ["approved"]
    relation_clause = "AND e.relation = ANY(:relations) " if relation_whitelist else ""
    sql = _TRAVERSE_SQL.format(
        relation_clause_anchor=relation_clause.replace("e.", ""),
        relation_clause_rec=relation_clause,
    )

    params: dict[str, Any] = {
        "ok_statuses": ok_statuses,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "start_id": str(start_id),
        "start_type": start_type.value,
        "max_depth": max_depth,
        "limit": limit,
    }
    if relation_whitelist:
        # asyncpg binds a Python list as a PG array, which ``= ANY()`` accepts.
        params["relations"] = [r.value for r in relation_whitelist]

    result = await db.execute(text(sql), params)
    rows = result.mappings().all()

    # Collect every endpoint (type, id) referenced, plus the start node.
    endpoint_pairs: list[tuple[EdgeEndpointType, UUID]] = [(start_type, start_id)]
    edges: list[dict[str, Any]] = []
    for row in rows:
        src_type = EdgeEndpointType(row["src_type"])
        dst_type = EdgeEndpointType(row["dst_type"])
        endpoint_pairs.append((src_type, row["src_id"]))
        endpoint_pairs.append((dst_type, row["dst_id"]))
        edges.append(
            {
                "id": str(row["id"]),
                "src": {"type": src_type.value, "id": str(row["src_id"])},
                "dst": {"type": dst_type.value, "id": str(row["dst_id"])},
                "relation": row["relation"],
                "status": row["status"],
            }
        )

    resolved = await resolve_endpoints(db, endpoint_pairs)

    # Dedup nodes by id while preserving a stable order (start first).
    seen: set[str] = {str(start_id)}
    nodes = [resolved.get(start_id) or _fallback(start_type, start_id)]
    for row in rows:
        for side in ("src_id", "dst_id"):
            eid = row[side]
            if str(eid) not in seen:
                seen.add(str(eid))
                nodes.append(resolved.get(eid) or _fallback(start_type, eid))

    return {
        "start": nodes[0],
        "nodes": nodes,
        "edges": edges,
    }


def _fallback(etype: EdgeEndpointType, eid: UUID) -> dict[str, Any]:
    return {
        "type": etype.value,
        "id": str(eid),
        "label": f"{etype.value}:{str(eid)[:8]}",
        "icon": None,
        "color": None,
        "kind": None,
    }


_COUNT_SQL = """
SELECT src_id, relation, COUNT(*) AS n
FROM concept_edges
WHERE status = 'approved'
  AND (tenant_id IS NULL OR tenant_id = :tenant_id)
  AND src_type = :src_type
  AND src_id = ANY(:ids)
GROUP BY src_id, relation
"""


async def count_relations(
    db: AsyncSession,
    src_type: EdgeEndpointType,
    src_ids: list[UUID],
    *,
    tenant_id: Optional[UUID] = None,
) -> dict[str, dict[str, Any]]:
    """Batch count of outgoing approved edges per item (N+1-safe).

    Returns ``{str(item_id): {"total": int, "by_relation": {relation: n}}}``.
    Items with no edges get no key (callers should default to 0). Empty
    ``src_ids`` returns ``{}`` without hitting the DB.
    """
    if not src_ids:
        return {}
    result = await db.execute(
        text(_COUNT_SQL),
        {
            "src_type": src_type.value,
            "ids": [str(i) for i in src_ids],
            "tenant_id": str(tenant_id) if tenant_id else None,
        },
    )
    counts: dict[str, dict[str, Any]] = {}
    for row in result.mappings().all():
        item_id = str(row["src_id"])
        bucket = counts.setdefault(item_id, {"total": 0, "by_relation": {}})
        bucket["total"] += row["n"]
        bucket["by_relation"][row["relation"]] = row["n"]
    return counts


_COUNT_BOTH_SQL = """
SELECT endpoint_id, relation, COUNT(*) AS n FROM (
    SELECT src_id AS endpoint_id, relation FROM concept_edges
    WHERE status = 'approved'
      AND (tenant_id IS NULL OR tenant_id = :tenant_id)
      AND src_type = :endpoint_type
      AND src_id = ANY(:ids)
  UNION ALL
    SELECT dst_id AS endpoint_id, relation FROM concept_edges
    WHERE status = 'approved'
      AND (tenant_id IS NULL OR tenant_id = :tenant_id)
      AND dst_type = :endpoint_type
      AND dst_id = ANY(:ids)
) AS combined
GROUP BY endpoint_id, relation
"""


async def count_relations_both_directions(
    db: AsyncSession,
    endpoint_type: EdgeEndpointType,
    ids: list[UUID],
    *,
    tenant_id: Optional[UUID] = None,
) -> dict[str, dict[str, Any]]:
    """Batch count of approved edges touching each item in **either** direction.

    Unlike :func:`count_relations` (outgoing only), this counts edges where the
    item is either the ``src`` or the ``dst`` — the correct semantics for graph-
    integrity gates like "does this concept still have live edges before I
    delete it?". A concept with only *incoming* edges (e.g. several biomarkers
    ``MEMBER_OF`` it) is not orphaned, and must not be hard-deleted.

    Returns ``{str(item_id): {"total": int, "by_relation": {relation: n}}}``.
    Items with no edges get no key (callers should default to 0). Empty ``ids``
    returns ``{}`` without hitting the DB.

    This generalizes ``ConceptService._count_active_edges_for_concept`` (which
    was concept-specific and single-id) so the bidirectional count is reusable
    and independently tested.
    """
    if not ids:
        return {}
    result = await db.execute(
        text(_COUNT_BOTH_SQL),
        {
            "endpoint_type": endpoint_type.value,
            "ids": [str(i) for i in ids],
            "tenant_id": str(tenant_id) if tenant_id else None,
        },
    )
    counts: dict[str, dict[str, Any]] = {}
    for row in result.mappings().all():
        item_id = str(row["endpoint_id"])
        bucket = counts.setdefault(item_id, {"total": 0, "by_relation": {}})
        bucket["total"] += row["n"]
        bucket["by_relation"][row["relation"]] = row["n"]
    return counts


async def whole_catalog_graph(
    db: AsyncSession,
    *,
    tenant_id: Optional[UUID],
    types: Optional[list[str]] = None,
    kinds: Optional[list[str]] = None,
    include_isolated: bool = False,
    limit_edges: int = 10000,
    limit_nodes: int = 5000,
) -> dict[str, Any]:
    """Rootless whole-graph loader for the entire cross-catalog ontology.

    Unlike :func:`traverse` (start-node-bound), this returns the full graph —
    optionally filtered by endpoint types and/or concept kinds — without
    requiring a start node. Used by the workspace-level Graph view.

    **Edge-driven design:** loads all approved ``concept_edges`` (optionally
    filtered by ``src_type``/``dst_type``), collects every endpoint ``(type,
    id)`` pair, and resolves them via the polymorphic
    :func:`resolve_endpoints` registry. This naturally handles all catalog
    types (concept, biomarker, medication, anatomy, allergy, vaccine) without
    querying each model table separately — the resolver does the per-type
    loading. Only nodes with at least one edge appear by default (the graph is
    about relationships); set ``include_isolated=True`` to also load items
    with no edges from each catalog table.

    Parameters:
        tenant_id: standard tenant scope.
        types: optional list of ``EdgeEndpointType`` values to filter by
            (e.g. ``["concept", "biomarker"]``). Only edges where *both*
            endpoints are of a requested type are returned. ``None`` = all
            types.
        kinds: optional list of ``ConceptKind`` values. When specified,
            concept endpoints that don't carry any of the requested kinds are
            filtered out (and their edges removed). Non-concept endpoints are
            unaffected.
        include_isolated: when ``True``, also loads all items from each
            requested catalog type's model table (tenant-scoped, not
            deleted/retired) and adds them to the node set — even if they have
            no edges. Lets the user browse the full catalog in graph mode,
            not just connected items.
        limit_edges / limit_nodes: caps. ``truncated`` in the response tells
            the client if results were capped.

    Returns ``{"nodes": [...], "edges": [...], "truncated": bool}`` where nodes
    and edges match :func:`traverse`'s shapes (resolved via
    :func:`resolve_endpoints`).
    """
    from app.models.concept_model import ConceptEdge, ConceptKindTag
    from app.models.enums import EdgeApprovalStatus

    truncated = False

    # Determine which catalog types are in scope.
    if types:
        resolved_types = [EdgeEndpointType(t) for t in types if t]
    else:
        resolved_types = None  # all types

    # 1. Load approved edges (tenant-scoped), optionally filtered by types.
    edge_stmt = select(ConceptEdge).where(
        ConceptEdge.status == EdgeApprovalStatus.APPROVED,
        or_(
            ConceptEdge.tenant_id.is_(None),
            ConceptEdge.tenant_id == tenant_id,
        ),
    )
    if resolved_types:
        edge_stmt = edge_stmt.where(
            ConceptEdge.src_type.in_(resolved_types),
            ConceptEdge.dst_type.in_(resolved_types),
        )
    edge_stmt = edge_stmt.limit(limit_edges)
    edge_rows = (await db.execute(edge_stmt)).scalars().all()
    if len(edge_rows) >= limit_edges:
        truncated = True

    # 2. Collect all endpoint (type, id) pairs from the loaded edges.
    visible: set[tuple[EdgeEndpointType, UUID]] = set()
    for e in edge_rows:
        visible.add((e.src_type, e.src_id))
        visible.add((e.dst_type, e.dst_id))

    # 2b. If include_isolated, also load all items from each catalog type's
    #     model table — even those with no edges.
    if include_isolated:
        types_to_load = resolved_types or [
            EdgeEndpointType.CONCEPT,
            EdgeEndpointType.BIOMARKER,
            EdgeEndpointType.MEDICATION,
            EdgeEndpointType.ALLERGY,
            EdgeEndpointType.ANATOMY,
            EdgeEndpointType.IMMUNIZATION,
        ]
        for etype in types_to_load:
            isolated_ids = await _load_catalog_ids(db, etype, tenant_id, limit_nodes)
            if len(isolated_ids) >= limit_nodes:
                truncated = True
            for eid in isolated_ids:
                visible.add((etype, eid))

    # 3. Optional concept-kind filter: remove concept endpoints whose kinds
    #    don't match any of the requested values.
    if kinds:
        from app.models.enums import ConceptKind

        resolved_kinds = [ConceptKind(k) for k in kinds if k]
        if resolved_kinds:
            concept_ids = {
                eid
                for (etype, eid) in visible
                if etype == EdgeEndpointType.CONCEPT
            }
            if concept_ids:
                valid = set(
                    row[0]
                    for row in (
                        await db.execute(
                            select(ConceptKindTag.concept_id).where(
                                ConceptKindTag.concept_id.in_(concept_ids),
                                ConceptKindTag.kind.in_(resolved_kinds),
                            )
                        )
                    ).all()
                )
                visible = {
                    (etype, eid)
                    for (etype, eid) in visible
                    if etype != EdgeEndpointType.CONCEPT or eid in valid
                }

    # 4. Filter edges: only keep edges where both endpoints survived filtering.
    visible_keys = {(etype.value, str(eid)) for (etype, eid) in visible}
    edges: list[dict[str, Any]] = []
    for e in edge_rows:
        src_key = (e.src_type.value, str(e.src_id))
        dst_key = (e.dst_type.value, str(e.dst_id))
        if src_key in visible_keys and dst_key in visible_keys:
            edges.append(
                {
                    "id": str(e.id),
                    "src": {"type": e.src_type.value, "id": str(e.src_id)},
                    "dst": {"type": e.dst_type.value, "id": str(e.dst_id)},
                    "relation": e.relation.value,
                    "status": e.status.value,
                }
            )

    # 5. Resolve all endpoints via the polymorphic resolver registry.
    endpoint_pairs = list(visible)
    resolved = await resolve_endpoints(db, endpoint_pairs) if endpoint_pairs else {}

    # 6. Build nodes list (deduped, stable order).
    seen: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for etype, eid in visible:
        if str(eid) not in seen:
            seen.add(str(eid))
            nodes.append(resolved.get(eid) or _fallback(etype, eid))

    return {"nodes": nodes, "edges": edges, "truncated": truncated}


async def _load_catalog_ids(
    db: AsyncSession,
    etype: EdgeEndpointType,
    tenant_id: Optional[UUID],
    limit: int,
) -> list[UUID]:
    """Load all item IDs for a catalog type (tenant-scoped, not deleted)."""
    from app.models.enums import ConceptStatus

    tenant_filter = or_(
        # tenant_id is on each model; we reference it via the model class
    )

    if etype == EdgeEndpointType.CONCEPT:
        from app.models.concept_model import Concept

        rows = (
            await db.execute(
                select(Concept.id)
                .where(
                    or_(Concept.tenant_id.is_(None), Concept.tenant_id == tenant_id),
                    Concept.deleted_at.is_(None),
                    Concept.status == ConceptStatus.ACTIVE,
                )
                .limit(limit)
            )
        ).all()
    elif etype == EdgeEndpointType.BIOMARKER:
        from app.models.biomarker_model import BiomarkerDefinition

        rows = (
            await db.execute(
                select(BiomarkerDefinition.id)
                .where(
                    or_(
                        BiomarkerDefinition.tenant_id.is_(None),
                        BiomarkerDefinition.tenant_id == tenant_id,
                    )
                )
                .limit(limit)
            )
        ).all()
    elif etype == EdgeEndpointType.MEDICATION:
        from app.models.fhir.medication import MedicationCatalog

        rows = (
            await db.execute(
                select(MedicationCatalog.id)
                .where(
                    or_(
                        MedicationCatalog.tenant_id.is_(None),
                        MedicationCatalog.tenant_id == tenant_id,
                    )
                )
                .limit(limit)
            )
        ).all()
    elif etype == EdgeEndpointType.ALLERGY:
        from app.models.fhir.allergy import AllergyCatalog

        rows = (
            await db.execute(
                select(AllergyCatalog.id)
                .where(
                    or_(
                        AllergyCatalog.tenant_id.is_(None),
                        AllergyCatalog.tenant_id == tenant_id,
                    )
                )
                .limit(limit)
            )
        ).all()
    elif etype == EdgeEndpointType.ANATOMY:
        from app.models.anatomy_model import AnatomyStructure

        rows = (
            await db.execute(
                select(AnatomyStructure.id)
                .where(
                    or_(
                        AnatomyStructure.tenant_id.is_(None),
                        AnatomyStructure.tenant_id == tenant_id,
                    ),
                )
                .limit(limit)
            )
        ).all()
    elif etype == EdgeEndpointType.IMMUNIZATION:
        from app.models.fhir.vaccine import VaccineCatalog

        rows = (
            await db.execute(
                select(VaccineCatalog.id)
                .where(
                    or_(
                        VaccineCatalog.tenant_id.is_(None),
                        VaccineCatalog.tenant_id == tenant_id,
                    )
                )
                .limit(limit)
            )
        ).all()
    else:
        return []
    return [row[0] for row in rows]


async def whole_concept_graph(
    db: AsyncSession,
    *,
    tenant_id: Optional[UUID],
    kinds: Optional[list[str]] = None,
    include_anatomy: bool = False,
    limit_nodes: int = 1000,
    limit_edges: int = 10000,
) -> dict[str, Any]:
    """.. deprecated:: Use :func:`whole_catalog_graph` instead.

    Thin delegate preserved for backward compatibility (existing tests +
    the ``/catalogs/concept/graph`` endpoint). The new function generalizes
    to all catalog types via the ``types`` parameter.
    """
    types = ["concept"]
    if include_anatomy:
        types = ["concept", "anatomy"]
    return await whole_catalog_graph(
        db,
        tenant_id=tenant_id,
        types=types,
        kinds=kinds,
        limit_edges=limit_edges,
    )


__all__ = [
    "traverse",
    "count_relations",
    "count_relations_both_directions",
    "whole_concept_graph",
    "whole_catalog_graph",
]
