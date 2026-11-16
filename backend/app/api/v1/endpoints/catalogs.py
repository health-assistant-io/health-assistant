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

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs import CatalogRegistry
from app.catalogs.policy import CatalogConflict, CatalogUniqueViolation
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData

router = APIRouter(prefix="/catalogs", tags=["Catalogs"])

_UNKNOWN_TYPE_DETAIL = "Unknown catalog type '{type}'. Available: {available}"


def _require_descriptor(type: str):
    if not CatalogRegistry.is_registered(type):
        raise HTTPException(
            status_code=404,
            detail=_UNKNOWN_TYPE_DETAIL.format(type=type, available=CatalogRegistry.types()),
        )
    return CatalogRegistry.get(type)


def _require_write_contract(type: str):
    """Resolve the descriptor + its typed write contract."""
    descriptor = _require_descriptor(type)
    if descriptor.write is None:  # pragma: no cover - every registered type has one
        raise HTTPException(status_code=405, detail=f"Catalog type '{type}' has no write contract.")
    return descriptor


def _validate_payload(schema, payload: dict[str, Any]):
    """Validate a raw body against the type's write schema.

    Unknown or system-owned fields (``scope``, ``tenant_id``, ``seed_key``,
    ``created_by``, ...) are rejected with a typed 422 — never silently
    dropped (the retired ``_writable``-as-validation behavior).
    """
    from pydantic import ValidationError

    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False),
        ) from None


@router.get("")
async def list_catalog_types(
    current_user: TokenData = Depends(get_current_user),
) -> dict[str, Any]:
    """List every registered catalog type with its UI + write metadata.

    Drives the left rail of the ``/admin/catalogs`` workspace — no hardcoded
    nav. Returns ``{"types": [{"type", "ui": {...}, "has_concept_link",
    "edge_endpoint_type", "search_columns", "write": {...}}, ...]}``.

    The ``write`` block is derived from the type's :class:`CatalogWriteContract`
    (``create_fields``/``update_fields`` = the Pydantic schema fields,
    ``supports_promote``). It is the single source of truth for write-path
    clients: a body key outside these lists is rejected 422 by the generic
    routes (``extra="forbid"``), so callers filter their payloads to exactly
    these fields.
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
                "write": {
                    "create_fields": sorted(d.write.create_schema.model_fields),
                    "update_fields": sorted(d.write.update_schema.model_fields),
                    "supports_promote": d.write.supports_promote,
                },
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
    types: str | None = Query(
        None, description="Comma-separated catalog types to search (default: all)"
    ),
    kind: str | None = Query(
        None,
        description="ConceptKind value (e.g. 'event_category', 'specialty', "
        "'disease') to narrow the concept catalog. Ignored for non-concept "
        "catalogs. Powers concept-kind-filtered pickers (examination "
        "categories, event categories, specialties) from the same endpoint.",
    ),
    limit: int = Query(20, ge=1, le=100),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Unified cross-catalog search (trigram, tenant-scoped).

    Returns ``{"results": [{"type", "id", "label"}, ...]}`` across all
    registered catalogs (biomarker, medication, allergy, anatomy, concept, …)
    or a ``types`` subset. ``kind`` further narrows the ``concept`` catalog to
    one ConceptKind. Defined before ``/{type}`` so the literal ``search`` path
    isn't captured by the type path-param.
    """
    from app.models.enums import ConceptKind
    from app.services.catalog.catalog_search_service import search_catalogs

    type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None

    # Validate the kind param up front so a typo is a 400, not a silent
    # "no results because nothing matched an unknown kind".
    kind_enum: ConceptKind | None = None
    if kind:
        try:
            kind_enum = ConceptKind(kind.strip().lower())
        except ValueError:
            valid = sorted(k.value for k in ConceptKind)
            raise HTTPException(
                status_code=400,
                detail=(f"Unknown kind '{kind}'. Valid ConceptKind values: {valid}"),
            ) from None

    results = await search_catalogs(
        db,
        current_user.tenant_id,
        q,
        types=type_list,
        kind=kind_enum,
        limit_total=limit,
    )
    return {"results": results}


@router.get("/graph")
async def get_catalog_graph(
    types: str | None = Query(
        None,
        description="Comma-separated EdgeEndpointType values to filter by "
        "(e.g. 'concept,biomarker,medication'). Only edges where both "
        "endpoints are of a requested type are returned. Omit for all types.",
    ),
    kind: str | None = Query(
        None,
        description="Comma-separated ConceptKind values to filter concept "
        "endpoints by (e.g. 'disease,symptom'). Non-concept endpoints are "
        "unaffected. Omit for all kinds.",
    ),
    include_isolated: bool = Query(
        False,
        description="Also load items with no edges from each catalog table "
        "(lets you browse the full catalog in graph mode, not just "
        "connected items).",
    ),
    limit: int = Query(10000, ge=1, le=50000, description="Max edges to return."),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Whole cross-catalog ontology graph (rootless).

    Returns the full polymorphic graph — concepts, biomarkers, medications,
    anatomy, allergies, vaccines — and all approved ``concept_edges`` between
    them. Powers the workspace-level Graph view for exploring cross-catalog
    relationships (e.g. "which medications treat diseases that affect this
    organ?").

    The ``types`` filter narrows the graph to specific catalog types. The
    ``kind`` filter further narrows concept endpoints by their ConceptKind
    tags. Returns ``{nodes, edges, truncated}`` where nodes are resolved via
    the polymorphic resolver (label, icon, color, kind per node).
    """
    from app.services.catalog.catalog_graph_service import whole_catalog_graph

    # Map catalog type names (e.g. "vaccine") to EdgeEndpointType values
    # (e.g. "immunization") via the registry. Most match directly, but
    # "vaccine" → "immunization" is the one mismatch.
    raw_types = [t.strip().lower() for t in types.split(",") if t.strip()] if types else None
    type_list = None
    if raw_types:
        type_list = []
        for t in raw_types:
            if CatalogRegistry.is_registered(t):
                desc = CatalogRegistry.get(t)
                type_list.append(desc.edge_endpoint_type.value)
            else:
                type_list.append(t)  # might be a raw EdgeEndpointType value
    kinds = [k.strip().lower() for k in kind.split(",") if k.strip()] if kind else None
    return await whole_catalog_graph(
        db,
        tenant_id=current_user.tenant_id,
        types=type_list,
        kinds=kinds,
        include_isolated=include_isolated,
        limit_edges=limit,
    )


@router.get("/{type}")
async def list_catalog_items(
    type: str,
    search: str | None = Query(None),
    scope: str | None = Query(
        None,
        description="Narrow to a scope tier: system | tenant | user",
    ),
    kind: str | None = Query(
        None,
        description="Domain kind filter. For the ``concept`` catalog type this "
        "filters by ``primary_kind`` (e.g. ``anatomy_class``, ``disease``).",
    ),
    class_: str | None = Query(
        None,
        alias="class",
        description="Taxonomy-class concept slug(s) to filter by, e.g. "
        "``organ`` or ``organ,organ-part``. Works for any catalog whose items "
        "carry a ``class_concept_id`` FK.",
    ),
    include: str | None = Query(
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
        from app.services.catalog.catalog_graph_service import count_relations

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
        raise HTTPException(status_code=404, detail=f"{type} item '{item_id}' not found")
    return item


@router.post("/{type}", status_code=201)
async def create_catalog_item(
    type: str,
    payload: dict[str, Any],
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a catalog item through the type's ONE write contract (ADR 0055 D5).

    The raw body is validated against the descriptor's typed create schema
    (``extra="forbid"`` — unknown/system-owned fields → 422), then dispatched
    to the same service function the domain routes call. The scope is derived
    from the creator's role (SYSTEM_ADMIN→system, ADMIN/MANAGER→tenant,
    USER→user; concepts follow ConceptService's curated-taxonomy RBAC).
    """
    descriptor = _require_write_contract(type)
    data = _validate_payload(descriptor.write.create_schema, payload)
    try:
        return await descriptor.service.create(db, current_user, data)
    except CatalogUniqueViolation:
        raise  # global handler → 409 (must win over the ValueError mapping below)
    except ValueError as exc:
        # Domain-value errors (e.g. unknown biomarker state/unit slug) — the
        # same status the domain routes return for the same failure.
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.put("/{type}/{item_id}")
async def update_catalog_item(
    type: str,
    item_id: UUID,
    payload: dict[str, Any],
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update one catalog item through the type's ONE write contract.

    Scope + ownership enforced in the write service
    (:class:`~app.catalogs.policy.CatalogPermissionDenied` → 403). Returns 404
    when the item is missing or outside the caller's scope.
    """
    descriptor = _require_write_contract(type)
    data = _validate_payload(descriptor.write.update_schema, payload)
    try:
        item = await descriptor.service.update(db, current_user, item_id, data)
    except CatalogUniqueViolation:
        raise  # global handler → 409
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail=f"{type} item '{item_id}' not found")
    return item


@router.delete("/{type}/{item_id}")
async def delete_catalog_item(
    type: str,
    item_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete one catalog item (scope + ownership enforced in the write
    service; concepts retire-or-soft-delete via ConceptService)."""
    descriptor = _require_write_contract(type)
    try:
        deleted = await descriptor.service.delete(db, current_user, item_id)
    except CatalogUniqueViolation:
        raise  # global handler → 409
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not deleted:
        raise HTTPException(status_code=404, detail=f"{type} item '{item_id}' not found")
    return {"status": "success", "message": f"{type} item deleted"}


class CatalogPromoteRequest(BaseModel):
    """Body of ``POST /catalogs/{type}/{id}/promote``."""

    scope: Literal["system", "tenant", "user"]

    model_config = ConfigDict(extra="forbid")


@router.post("/{type}/{item_id}/promote")
async def promote_catalog_item(
    type: str,
    item_id: UUID,
    payload: CatalogPromoteRequest,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Transition a catalog item's scope (plan §1.3).

    Role-gated: user↔tenant requires ADMIN/MANAGER; any transition involving
    system requires SYSTEM_ADMIN. On promote-to-system the ``tenant_id`` is
    cleared (canonical); on demote-to-tenant it is set to the actor's tenant.
    A slug duplicate at the target tier → 409.
    """
    descriptor = _require_write_contract(type)
    if not descriptor.write.supports_promote:
        # Concepts derive scope from tenant_id at create; there is no tier
        # transition in ConceptService to route through.
        raise HTTPException(
            status_code=405,
            detail=(
                f"Catalog type '{type}' has no scope transition. "
                "Create the item at the target scope instead."
            ),
        )
    try:
        item = await descriptor.service.promote_scope(db, current_user, item_id, payload.scope)
    except (CatalogConflict, CatalogUniqueViolation):
        raise  # global handlers → 409
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail=f"{type} item '{item_id}' not found")
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
    from app.services.catalog.catalog_audit_service import list_history

    descriptor = _require_descriptor(type)
    # The item must be visible to the caller before its history is revealed.
    item = await descriptor.service.get(db, current_user.tenant_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"{type} item '{item_id}' not found")
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
    relation: str | None = Query(
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
    from app.services.catalog.catalog_graph_service import traverse

    descriptor = _require_descriptor(type)
    whitelist: tuple[ConceptRelationType, ...] | None = None
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
