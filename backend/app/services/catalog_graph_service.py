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

from sqlalchemy import text
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


__all__ = ["traverse", "count_relations"]
