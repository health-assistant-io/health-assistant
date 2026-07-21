"""Concept + ConceptEdge API endpoints.

CRUD for the unified taxonomy + graph edges, with RBAC:
- ``USER``: read-only (list, get, neighbors, edges).
- ``ADMIN`` / ``MANAGER``: manage **tenant-scoped** concepts/edges.
- ``SYSTEM_ADMIN``: manage **global** (tenant_id NULL) concepts/edges + bypass.

All list reads apply the standard ``or_(tenant_id == caller, tenant_id.is_(None))``
filter so global canonical rows are visible to every tenant.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import (
    ConceptKind,
    ConceptRelationType,
    EdgeEndpointType,
)
from app.schemas.concept import (
    ConceptCreate,
    ConceptEdgeCreate,
    ConceptEdgeResponse,
    ConceptResponse,
    ConceptUpdate,
    NeighborResponse,
)
from app.schemas.user import TokenData
from app.services.concept_service import ConceptService

router = APIRouter(prefix="/concepts", tags=["Concepts"])


def _resolve_kind(kind: Optional[str]) -> Optional[ConceptKind]:
    if kind is None:
        return None
    try:
        return ConceptKind(kind)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid kind '{kind}'. Valid: {[k.value for k in ConceptKind]}",
        )


def _resolve_relation(relation: Optional[str]) -> Optional[ConceptRelationType]:
    if relation is None:
        return None
    try:
        return ConceptRelationType(relation)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid relation '{relation}'. Valid: {[r.value for r in ConceptRelationType]}",
        )


# ---------------------------------------------------------------------------
# Concept CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=List[ConceptResponse])
async def list_concepts(
    kind: Optional[str] = Query(None),
    parent_id: Optional[UUID] = Query(None),
    include_retired: bool = Query(False),
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List concepts visible to the caller, optionally filtered by kind/parent."""
    svc = ConceptService(db)
    concepts = await svc.list_concepts(
        tenant_id=current_user.tenant_id,
        kind=_resolve_kind(kind),
        parent_id=parent_id,
        include_retired=include_retired,
        limit=limit,
        offset=offset,
    )
    return [ConceptResponse.model_validate(c) for c in concepts]


@router.get("/search", response_model=List[ConceptResponse])
async def search_concepts(
    q: str = Query(..., min_length=1),
    kind: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ranked trigram + alias search over concepts."""
    from app.services.catalog_search_service import search_concepts as _search

    results = await _search(
        db,
        current_user.tenant_id,
        q,
        kind=_resolve_kind(kind),
        limit=limit,
    )
    return [ConceptResponse.model_validate(c) for c in results]


@router.post("", response_model=ConceptResponse, status_code=201)
async def create_concept(
    body: ConceptCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a concept. Global (tenant_scoped=False) requires SYSTEM_ADMIN.

    Accepts ``kinds: [...]`` (preferred) or legacy ``kind: "..."``; at least
    one kind must be supplied.
    """
    svc = ConceptService(db)
    resolved_kinds: List[ConceptKind] = []
    for k in body.kinds:
        try:
            resolved_kinds.append(ConceptKind(k))
        except ValueError:
            raise HTTPException(400, f"Invalid kind '{k}'")
    if body.kind is not None:
        try:
            legacy = ConceptKind(body.kind)
        except ValueError:
            raise HTTPException(400, f"Invalid kind '{body.kind}'")
        if legacy not in resolved_kinds:
            resolved_kinds.append(legacy)
    if not resolved_kinds:
        raise HTTPException(400, "at least one kind is required")

    tenant_id = current_user.tenant_id if body.tenant_scoped else None
    try:
        concept = await svc.create_concept(
            slug=body.slug,
            name=body.name,
            kinds=resolved_kinds,
            tenant_id=tenant_id,
            role=current_user.role,
            description=body.description,
            parent_id=body.parent_id,
            coding_system=body.coding_system,
            code=body.code,
            aliases=body.aliases,
            icon=body.icon,
            color=body.color,
            display_order=body.display_order,
            meta_data=body.meta_data,
            created_by=current_user.user_id,
            actor=current_user,
        )
        await db.commit()
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return ConceptResponse.model_validate(concept)


@router.get("/{concept_id}", response_model=ConceptResponse)
async def get_concept(
    concept_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single concept by ID (tenancy-scoped)."""
    svc = ConceptService(db)
    concept = await svc.get_concept(concept_id, current_user.tenant_id)
    if concept is None:
        raise HTTPException(404, "Concept not found")
    return ConceptResponse.model_validate(concept)


@router.put("/{concept_id}", response_model=ConceptResponse)
async def update_concept(
    concept_id: UUID,
    body: ConceptUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a concept's mutable fields (RBAC enforced in service)."""
    svc = ConceptService(db)
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and isinstance(fields.get("status"), str):
        from app.models.enums import ConceptStatus

        try:
            fields["status"] = ConceptStatus(fields["status"])
        except ValueError:
            raise HTTPException(400, f"Invalid status '{fields['status']}'")

    try:
        concept = await svc.update_concept(
            concept_id,
            current_user.tenant_id,
            current_user.role,
            actor=current_user,
            **fields,
        )
        await db.commit()
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(404 if "not found" in str(exc).lower() else 400, str(exc))
    await db.refresh(concept)
    return ConceptResponse.model_validate(concept)


@router.delete("/{concept_id}", status_code=204)
async def delete_concept(
    concept_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete (or retire if referenced) a concept.

    A concept with active edges in either direction is **retired** (status set
    to ``retired``); an edge-less concept is also soft-deleted (``deleted_at``
    set). Both are reversible via ``POST /{concept_id}/restore``.
    """
    svc = ConceptService(db)
    try:
        await svc.delete_concept(
            concept_id,
            current_user.tenant_id,
            current_user.role,
            actor=current_user,
        )
        await db.commit()
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{concept_id}/restore", response_model=ConceptResponse)
async def restore_concept(
    concept_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reverse a prior retire/soft-delete (``status`` → active, clears ``deleted_at``)."""
    svc = ConceptService(db)
    try:
        concept = await svc.restore_concept(
            concept_id,
            current_user.tenant_id,
            current_user.role,
            actor=current_user,
        )
        await db.commit()
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    await db.refresh(concept)
    return ConceptResponse.model_validate(concept)


@router.get("/{concept_id}/neighbors", response_model=List[NeighborResponse])
async def get_neighbors(
    concept_id: UUID,
    relation: Optional[str] = Query(None),
    include_proposed: bool = Query(False),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One-hop neighbor lookup for a concept (graph traversal)."""
    svc = ConceptService(db)
    neighbors = await svc.get_neighbors(
        concept_id,
        current_user.tenant_id,
        relation=_resolve_relation(relation),
        include_proposed=include_proposed,
    )
    return [
        NeighborResponse(
            edge=ConceptEdgeResponse.model_validate(n["edge"]),
            direction=n["direction"],
            endpoint=n["endpoint"],
        )
        for n in neighbors
    ]


# ---------------------------------------------------------------------------
# ConceptEdge CRUD (separate sub-router under /concept-edges)
# ---------------------------------------------------------------------------

edge_router = APIRouter(prefix="/concept-edges", tags=["Concept Edges"])


@edge_router.get("/schema")
async def get_link_schema(
    src_type: Optional[str] = Query(
        None,
        description="Filter to relations FROM this EdgeEndpointType",
    ),
    dst_type: Optional[str] = Query(
        None,
        description="Filter to relations TO this EdgeEndpointType (requires src_type)",
    ),
):
    """Return the link-schema matrix that decides which ``(src_type, dst_type)``
    pairs the knowledge-graph accepts and which ``relation`` values are valid
    for each.

    Pure metadata — no DB hit, no tenancy. Used by the frontend
    ``<LinksSection>`` to filter destination + relation dropdowns on each
    create form, and by the LLM via the ``get_link_schema`` tool.

    * With ``src_type`` and ``dst_type``: ``{"relations": ["TREATS", ...]}``
    * With ``src_type`` only: ``{"<dst_type>": ["TREATS", ...], ...}``
    * With neither: ``[{"src_type", "dst_type", "relations"}, ...]``
    """
    from app.ai.tools.propose_link import (
        relations_for,
        relations_for_source,
        serialize_full_schema,
    )

    try:
        src = EdgeEndpointType(src_type) if src_type else None
        dst = EdgeEndpointType(dst_type) if dst_type else None
    except ValueError as exc:
        raise HTTPException(400, f"Invalid endpoint type: {exc}")

    if src and dst:
        return {"relations": relations_for(src, dst)}
    if src:
        return relations_for_source(src)
    return serialize_full_schema()


@edge_router.get("", response_model=List[ConceptEdgeResponse])
async def list_edges(
    src_type: Optional[str] = Query(None),
    src_id: Optional[UUID] = Query(None),
    dst_type: Optional[str] = Query(None),
    dst_id: Optional[UUID] = Query(None),
    relation: Optional[str] = Query(None),
    include_proposed: bool = Query(False),
    limit: int = Query(200, ge=1, le=5000),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List edges matching filters (tenant-scoped, approved-only by default)."""
    svc = ConceptService(db)
    try:
        st = EdgeEndpointType(src_type) if src_type else None
        dt = EdgeEndpointType(dst_type) if dst_type else None
    except ValueError:
        raise HTTPException(400, "Invalid endpoint type")

    edges = await svc.get_edges(
        tenant_id=current_user.tenant_id,
        src_type=st,
        src_id=src_id,
        dst_type=dt,
        dst_id=dst_id,
        relation=_resolve_relation(relation),
        include_proposed=include_proposed,
        limit=limit,
    )
    return [ConceptEdgeResponse.model_validate(e) for e in edges]


@edge_router.post("", response_model=ConceptEdgeResponse, status_code=201)
async def create_edge(
    body: ConceptEdgeCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a typed edge. Validates concept endpoints exist. Global edges need SYSTEM_ADMIN."""
    svc = ConceptService(db)
    try:
        src_type = EdgeEndpointType(body.src_type)
        dst_type = EdgeEndpointType(body.dst_type)
        relation = ConceptRelationType(body.relation)
        from app.models.enums import ConceptProvenance, EdgeApprovalStatus

        source = ConceptProvenance(body.source)
        status = EdgeApprovalStatus(body.status)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid enum value: {exc}")

    tenant_id = current_user.tenant_id if body.tenant_scoped else None
    try:
        edge = await svc.create_edge(
            src_type=src_type,
            src_id=body.src_id,
            dst_type=dst_type,
            dst_id=body.dst_id,
            relation=relation,
            tenant_id=tenant_id,
            role=current_user.role,
            properties=body.properties,
            evidence=body.evidence,
            source=source,
            status=status,
            created_by=current_user.user_id,
        )
        await db.commit()
        return ConceptEdgeResponse.model_validate(edge)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@edge_router.delete("/{edge_id}", status_code=204)
async def delete_edge(
    edge_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an edge (RBAC enforced in service)."""
    svc = ConceptService(db)
    try:
        await svc.delete_edge(edge_id, current_user.tenant_id, current_user.role)
        await db.commit()
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
