"""The ``/catalogs`` meta-layer — a thin, registry-driven dispatcher over all
clinical catalogs.

This router is **read-mostly + admin-write** and complements (does not replace)
the domain endpoints (``/biomarkers``, ``/medications``, ``/anatomy``, ...).
Domain endpoints stay FHIR-aligned and carry domain-specific operations
(``/remap``, ``/retry-migration``, patient-instance routes); this meta-layer
handles the cross-cutting concerns — unified listing, cross-catalog search
(Phase 4), cross-catalog graph traversal (Phase 2), and admin/LLM access.

Phase 0 exposes only read paths (list types, list a type, get one). Write
routes (POST/PUT/DELETE) arrive in Phase 1 once the uniform RBAC policy is
enforced.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs import CatalogRegistry
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData

router = APIRouter(prefix="/catalogs", tags=["Catalogs"])

_UNKNOWN_TYPE_DETAIL = "Unknown catalog type '{type}'. Available: {available}"


def _require_descriptor(type: str):
    if not CatalogRegistry.is_registered(type):
        raise HTTPException(
            status_code=404,
            detail=_UNKNOWN_TYPE_DETAIL.format(
                type=type, available=CatalogRegistry.types()
            ),
        )
    return CatalogRegistry.get(type)


@router.get("")
async def list_catalog_types(
    current_user: TokenData = Depends(get_current_user),
) -> dict[str, Any]:
    """List every registered catalog type with its UI metadata.

    Drives the left rail of the ``/admin/catalogs`` workspace — no hardcoded
    nav. Returns ``{"types": [{"type", "ui": {...}, "has_concept_link",
    "edge_endpoint_type", "search_columns"}, ...]}``.
    """
    return {
        "types": [
            {
                "type": d.type,
                "ui": {
                    "label_key": d.ui.label_key,
                    "icon": d.ui.icon,
                    "color": d.ui.color,
                    "admin_route": d.ui.admin_route,
                },
                "has_concept_link": d.has_concept_link,
                "edge_endpoint_type": d.edge_endpoint_type.value,
                "search_columns": list(d.search_columns),
            }
            for d in CatalogRegistry.all()
        ]
    }


@router.get("/relation-types")
async def list_relation_types(
    current_user: TokenData = Depends(get_current_user),
) -> dict[str, Any]:
    """Relation-type reference metadata (label / description / icon / group).

    The single source of truth for the human-facing description of each
    ``ConceptRelationType``. Powers the info affordance in the catalog
    relations picker (and is available to AI tools). Returns
    ``{"items": [{"value", "label", "group", "description", "icon"}, ...]}``.
    """
    from app.catalogs.relation_types import list_relation_types as _types

    return {"items": _types()}


@router.get("/search")
async def search_catalogs_endpoint(
    q: str = Query(..., min_length=2),
    types: Optional[str] = Query(
        None, description="Comma-separated catalog types to search (default: all)"
    ),
    limit: int = Query(20, ge=1, le=100),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Unified cross-catalog search (trigram, tenant-scoped).

    Returns ``{"results": [{"type", "id", "label"}, ...]}`` across all
    registered catalogs (biomarker, medication, allergy, anatomy, concept, …)
    or a ``types`` subset. Defined before ``/{type}`` so the literal ``search``
    path isn't captured by the type path-param.
    """
    from app.services.catalog_search_service import search_catalogs

    type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
    results = await search_catalogs(
        db, current_user.tenant_id, q, types=type_list, limit_total=limit
    )
    return {"results": results}


@router.get("/{type}")
async def list_catalog_items(
    type: str,
    search: Optional[str] = Query(None),
    scope: Optional[str] = Query(
        None,
        description="Narrow to a scope tier: system | tenant | user",
    ),
    kind: Optional[str] = Query(
        None,
        description="Domain kind filter. For the ``concept`` catalog type this "
        "filters by ``primary_kind`` (e.g. ``anatomy_class``, ``disease``).",
    ),
    class_: Optional[str] = Query(
        None,
        alias="class",
        description="Taxonomy-class concept slug(s) to filter by, e.g. "
        "``organ`` or ``organ,organ-part``. Works for any catalog whose items "
        "carry a ``class_concept_id`` FK.",
    ),
    include: Optional[str] = Query(
        None,
        description="Comma-separated extras: 'relations' annotates each item "
        "with relation_count + relation_breakdown (one batched query).",
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List items of one catalog type, tenant-scoped (global + caller's tenant).

    ``scope`` narrows the result to a single tier (system/tenant/user).
    ``kind`` filters concepts by ``primary_kind``. ``?class=<slug>`` filters any
    catalog with a concept link by its taxonomy class (resolved slug→concept id).
    ``include=relations`` adds a ``relation_count`` + ``relation_breakdown``
    (per relation type) to each item — a single batched count query, no N+1.
    """
    descriptor = _require_descriptor(type)
    resp = await descriptor.service.list(
        db,
        current_user.tenant_id,
        search=search,
        kind=kind,
        scope=scope,
        concept_class=class_,
        limit=limit,
        offset=offset,
    )
    extras = {x.strip().lower() for x in (include or "").split(",") if x.strip()}
    if "relations" in extras:
        from app.services.catalog_graph_service import count_relations

        item_ids = [item.get("id") for item in resp["items"]]
        # Item ids come back as UUIDs from the adapters (biomarker) or strings
        # (to_dict). Normalise to UUID for the batch query.
        from uuid import UUID as _UUID

        norm = []
        for iid in item_ids:
            try:
                norm.append(iid if isinstance(iid, _UUID) else _UUID(str(iid)))
            except (ValueError, TypeError):
                continue
        counts = await count_relations(
            db, descriptor.edge_endpoint_type, norm, tenant_id=current_user.tenant_id
        )
        for item in resp["items"]:
            key = str(item.get("id"))
            stats = counts.get(key)
            item["relation_count"] = stats["total"] if stats else 0
            item["relation_breakdown"] = stats["by_relation"] if stats else {}
    return resp


@router.get("/{type}/{item_id}")
async def get_catalog_item(
    type: str,
    item_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Fetch one catalog item by id, tenant-scoped. 404 if missing/invisible."""
    descriptor = _require_descriptor(type)
    item = await descriptor.service.get(db, current_user.tenant_id, item_id)
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"{type} item '{item_id}' not found"
        )
    return item


@router.post("/{type}", status_code=201)
async def create_catalog_item(
    type: str,
    payload: dict[str, Any],
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a catalog item. The scope is derived from the creator's role
    (SYSTEM_ADMIN→system, ADMIN/MANAGER→tenant, USER→user) — any authenticated
    role may create (plan §1.2).

    The payload is a generic dict; the adapter mass-assignment-guards it
    against read-only/system fields. Domain-specific rich create logic (e.g.
    biomarker unit-symbol resolution, medication AI enrichment) stays on the
    domain endpoints.
    """
    descriptor = _require_descriptor(type)
    return await descriptor.service.create(db, current_user, payload)


@router.put("/{type}/{item_id}")
async def update_catalog_item(
    type: str,
    item_id: UUID,
    payload: dict[str, Any],
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update one catalog item. Scope + ownership enforced in the adapter
    (creator OR ADMIN for user-scope; ADMIN/MANAGER for tenant; SYSTEM_ADMIN
    for system); raises :class:`~app.catalogs.policy.CatalogPermissionDenied`
    (→ 403) on insufficient role. Returns 404 when the item is missing or
    outside the caller's scope.
    """
    descriptor = _require_descriptor(type)
    item = await descriptor.service.update(db, current_user, item_id, payload)
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"{type} item '{item_id}' not found"
        )
    return item


@router.delete("/{type}/{item_id}")
async def delete_catalog_item(
    type: str,
    item_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete one catalog item (scope + ownership enforced in the adapter)."""
    descriptor = _require_descriptor(type)
    deleted = await descriptor.service.delete(db, current_user, item_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"{type} item '{item_id}' not found"
        )
    return {"status": "success", "message": f"{type} item deleted"}


@router.post("/{type}/{item_id}/promote")
async def promote_catalog_item(
    type: str,
    item_id: UUID,
    payload: dict[str, Any],
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Transition a catalog item's scope (plan §1.3).

    Body: ``{"scope": "tenant" | "system" | "user"}``. Role-gated:
    user↔tenant requires ADMIN/MANAGER; any transition involving system
    requires SYSTEM_ADMIN. On promote-to-system the ``tenant_id`` is cleared
    (canonical); on demote-to-tenant it is set to the actor's tenant.
    """
    descriptor = _require_descriptor(type)
    target = (payload or {}).get("scope")
    if not target:
        raise HTTPException(status_code=400, detail="body must include 'scope'")
    try:
        item = await descriptor.service.promote_scope(db, current_user, item_id, target)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"invalid scope '{target}'. Use: system | tenant | user",
        )
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"{type} item '{item_id}' not found"
        )
    return item


@router.get("/{type}/{item_id}/history")
async def get_catalog_item_history(
    type: str,
    item_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The audit trail for one catalog item (newest-first), tenant-scoped.

    Returns ``{"items": [audit_entry, ...]}``. Each entry records the
    operation (create/update/delete/promote/demote), who performed it, when,
    and any scope transition. The item itself must be visible to the caller
    (404 otherwise) so the trail is never leaked cross-tenant.
    """
    from app.services.catalog_audit_service import list_history

    descriptor = _require_descriptor(type)
    # The item must be visible to the caller before its history is revealed.
    item = await descriptor.service.get(db, current_user.tenant_id, item_id)
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"{type} item '{item_id}' not found"
        )
    rows = await list_history(
        db,
        tenant_id=current_user.tenant_id,
        catalog_type=type,
        item_id=item_id,
        limit=limit,
    )
    return {"items": [r.to_dict() for r in rows]}


@router.get("/{type}/{item_id}/relations")
async def get_catalog_relations(
    type: str,
    item_id: UUID,
    depth: int = Query(2, ge=1, le=3),
    relation: Optional[str] = Query(
        None,
        description="Comma-separated relation whitelist, e.g. 'AFFECTS,TREATS'",
    ),
    include_proposed: bool = Query(False),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Cross-catalog graph traversal from one catalog item.

    Returns ``{start, nodes, edges}`` — the polymorphic ``concept_edges``
    subgraph reachable within ``depth`` hops of the item. Powers "which organ
    does this biomarker affect?", "what treats this disease?", etc.
    """
    from app.models.enums import ConceptRelationType
    from app.services.catalog_graph_service import traverse

    descriptor = _require_descriptor(type)
    whitelist: Optional[tuple[ConceptRelationType, ...]] = None
    if relation:
        requested = [r.strip().upper() for r in relation.split(",") if r.strip()]
        valid = {rt.value for rt in ConceptRelationType}
        invalid = [r for r in requested if r not in valid]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown relation(s): {invalid}. Valid: {sorted(valid)}",
            )
        whitelist = tuple(ConceptRelationType(r) for r in requested)

    return await traverse(
        db,
        descriptor.edge_endpoint_type,
        item_id,
        tenant_id=current_user.tenant_id,
        max_depth=depth,
        relation_whitelist=whitelist,
        include_proposed=include_proposed,
    )
