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


async def whole_concept_graph(
    db: AsyncSession,
    *,
    tenant_id: Optional[UUID],
    kinds: Optional[list[str]] = None,
    include_anatomy: bool = False,
    limit_nodes: int = 1000,
    limit_edges: int = 10000,
) -> dict[str, Any]:
    """Rootless whole-graph loader for the concept ontology (Phase 5, Option B).

    Unlike :func:`traverse` (which is start-node-bound), this returns the full
    concept graph — optionally kind-filtered — without requiring a start node.
    Used by the workspace-level Graph view to reproduce TaxonomyManager's
    whole-ontology exploration.

    **Scalability design:** the kind filter narrows both the concept set AND
    the edge set (only edges where *both* endpoints are in the visible concept
    set are returned, not all edges in the ontology). This keeps the payload
    proportional to the filtered domain, not the full graph. For very large
    ontologies, cursor pagination can be added later without changing the
    response shape.

    Parameters:
        tenant_id: standard tenant scope (``or_(NULL, caller)``).
        kinds: optional list of ``ConceptKind`` values to filter by (via the
            ``concept_kind_tags`` join — a multi-kind concept appears if it
            carries *any* of the requested kinds). ``None`` = all kinds.
        include_anatomy: when ``True``, also returns anatomy_structures nodes
            + concept↔anatomy polymorphic edges (the "anatomy overlay").
        limit_nodes / limit_edges: caps. ``truncated`` in the response tells
            the client if results were capped.

    Returns ``{"nodes": [...], "edges": [...], "truncated": bool}`` where nodes
    and edges match :func:`traverse`'s shapes (resolved via
    :func:`resolve_endpoints`).
    """
    from app.models.concept_model import Concept, ConceptEdge, ConceptKindTag
    from app.models.enums import ConceptStatus, EdgeApprovalStatus
    from app.services.concept_service import concepts_with_kind

    truncated = False

    # 1. Load concepts (optionally kind-filtered via the tag join).
    concept_stmt = (
        select(Concept)
        .where(
            or_(
                Concept.tenant_id.is_(None),
                Concept.tenant_id == tenant_id,
            ),
            Concept.deleted_at.is_(None),
            Concept.status == ConceptStatus.ACTIVE,
        )
    )
    if kinds:
        from app.models.enums import ConceptKind

        resolved_kinds = [ConceptKind(k) for k in kinds if k]
        if resolved_kinds:
            # A concept appears if it carries ANY of the requested kinds.
            concept_stmt = concept_stmt.where(
                Concept.id.in_(
                    select(ConceptKindTag.concept_id).where(
                        ConceptKindTag.kind.in_(resolved_kinds)
                    )
                )
            )
    concept_stmt = concept_stmt.limit(limit_nodes)
    concepts = (await db.execute(concept_stmt)).scalars().all()
    if len(concepts) >= limit_nodes:
        truncated = True
    concept_ids = {c.id for c in concepts}

    # 2. Load approved edges between those concepts only (not all edges).
    edges: list[dict[str, Any]] = []
    if concept_ids:
        edge_stmt = (
            select(ConceptEdge)
            .where(
                ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                or_(
                    ConceptEdge.tenant_id.is_(None),
                    ConceptEdge.tenant_id == tenant_id,
                ),
                ConceptEdge.src_type == EdgeEndpointType.CONCEPT,
                ConceptEdge.src_id.in_(concept_ids),
                ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                ConceptEdge.dst_id.in_(concept_ids),
            )
            .limit(limit_edges)
        )
        edge_rows = (await db.execute(edge_stmt)).scalars().all()
        if len(edge_rows) >= limit_edges:
            truncated = True
        for e in edge_rows:
            edges.append(
                {
                    "id": str(e.id),
                    "src": {"type": EdgeEndpointType.CONCEPT.value, "id": str(e.src_id)},
                    "dst": {"type": EdgeEndpointType.CONCEPT.value, "id": str(e.dst_id)},
                    "relation": e.relation.value,
                    "status": e.status.value,
                }
            )

    # 3. Optionally load anatomy nodes + concept↔anatomy edges.
    anatomy_ids: set[UUID] = set()
    if include_anatomy:
        from app.models.anatomy_model import AnatomyStructure

        anatomy_stmt = (
            select(AnatomyStructure)
            .where(
                or_(
                    AnatomyStructure.tenant_id.is_(None),
                    AnatomyStructure.tenant_id == tenant_id,
                ),
                AnatomyStructure.deleted_at.is_(None),
            )
            .limit(limit_nodes)
        )
        anatomy_rows = (await db.execute(anatomy_stmt)).scalars().all()
        anatomy_ids = {a.id for a in anatomy_rows}

        if anatomy_ids and concept_ids:
            anat_edge_stmt = (
                select(ConceptEdge)
                .where(
                    ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                    or_(
                        ConceptEdge.tenant_id.is_(None),
                        ConceptEdge.tenant_id == tenant_id,
                    ),
                    or_(
                        # concept → anatomy
                        (ConceptEdge.src_type == EdgeEndpointType.CONCEPT)
                        & (ConceptEdge.src_id.in_(concept_ids))
                        & (ConceptEdge.dst_type == EdgeEndpointType.ANATOMY)
                        & (ConceptEdge.dst_id.in_(anatomy_ids)),
                        # anatomy → concept
                        (ConceptEdge.src_type == EdgeEndpointType.ANATOMY)
                        & (ConceptEdge.src_id.in_(anatomy_ids))
                        & (ConceptEdge.dst_type == EdgeEndpointType.CONCEPT)
                        & (ConceptEdge.dst_id.in_(concept_ids)),
                    ),
                )
                .limit(limit_edges)
            )
            anat_edge_rows = (await db.execute(anat_edge_stmt)).scalars().all()
            for e in anat_edge_rows:
                edges.append(
                    {
                        "id": str(e.id),
                        "src": {"type": e.src_type.value, "id": str(e.src_id)},
                        "dst": {"type": e.dst_type.value, "id": str(e.dst_id)},
                        "relation": e.relation.value,
                        "status": e.status.value,
                    }
                )

    # 4. Resolve all node endpoints (concepts + anatomy) via the resolver.
    endpoint_pairs: list[tuple[EdgeEndpointType, UUID]] = []
    for cid in concept_ids:
        endpoint_pairs.append((EdgeEndpointType.CONCEPT, cid))
    for aid in anatomy_ids:
        endpoint_pairs.append((EdgeEndpointType.ANATOMY, aid))

    resolved = await resolve_endpoints(db, endpoint_pairs) if endpoint_pairs else {}

    # Dedup nodes by id, concepts first then anatomy (stable order).
    seen: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for cid in concept_ids:
        if str(cid) not in seen:
            seen.add(str(cid))
            nodes.append(resolved.get(cid) or _fallback(EdgeEndpointType.CONCEPT, cid))
    for aid in anatomy_ids:
        if str(aid) not in seen:
            seen.add(str(aid))
            nodes.append(resolved.get(aid) or _fallback(EdgeEndpointType.ANATOMY, aid))

    return {"nodes": nodes, "edges": edges, "truncated": truncated}


__all__ = [
    "traverse",
    "count_relations",
    "count_relations_both_directions",
    "whole_concept_graph",
]
