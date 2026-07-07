from fastapi import APIRouter, Depends, HTTPException, Query, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Optional
from uuid import UUID

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user_model import UserModel
from app.schemas.anatomy import (
    AnatomyStructureResponse,
    AnatomyStructureCreate,
    AnatomyStructureUpdate,
    AnatomyRelationCreate,
    AnatomyRelationResponse,
    AnatomyGraphNode,
    AnatomyListResponse,
    AnatomyRelatedResponse,
    AnatomyGraphResponse,
    AnatomyFigureResponse,
)
from app.schemas.anatomy_import import AnatomyImportPayload
from app.models.enums import AnatomyRelationType
from app.models.user_model import Role
from app.services import anatomy_service
from app.services.anatomy_import_service import AnatomyImportService
from app.core.security import RoleChecker

router = APIRouter()

_admin_only = Depends(RoleChecker([Role.SYSTEM_ADMIN]))

_ALLOWED_IMAGE_TYPES = {"image/webp", "image/png"}


def _ext_for(upload: UploadFile) -> str:
    """Resolve the file extension from the upload's content type."""
    ct = (upload.content_type or "").lower()
    if "png" in ct:
        return "png"
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    return "webp"


@router.get("", response_model=AnatomyListResponse)
async def list_anatomy_structures(
    class_concept_id: Optional[UUID] = None,
    search: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """
    Retrieve anatomy structures (nodes). Includes global items and tenant-specific items.
    Supports optional ``class_concept_id`` and ``search`` (ilike on name/slug/code/description)
    filtering plus pagination.
    """
    items, total = await anatomy_service.get_anatomy_structures(
        db,
        tenant_id=current_user.tenant_id,
        class_concept_id=class_concept_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total}


@router.post("", response_model=AnatomyStructureResponse)
async def create_anatomy_structure(
    structure_in: AnatomyStructureCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """
    Create a new anatomical structure.
    """
    # Check if slug exists
    existing = await anatomy_service.get_anatomy_structure_by_id_or_slug(
        db, structure_in.slug, current_user.tenant_id
    )
    if existing:
        raise HTTPException(
            status_code=400, detail="Structure with this slug already exists."
        )

    # Standard users create tenant-scoped items. System admins create global ones if they don't have a tenant.
    return await anatomy_service.create_anatomy_structure(
        db, structure_in, tenant_id=current_user.tenant_id
    )


# --- Anatomy figures (DB-driven body atlas, raster images) ---
# IMPORTANT: these /figures routes MUST be declared before /{identifier}, else
# FastAPI matches "figures" as an identifier. List + image are readable by any
# authenticated user; create/update/delete are SYSTEM_ADMIN-only.


@router.get("/figures", response_model=list[AnatomyFigureResponse])
async def list_anatomy_figures(
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """List body figures (views). Metadata only."""
    figures = await anatomy_service.list_anatomy_figures(db, active_only=active_only)
    return [f.to_dict() for f in figures]


@router.get("/figures/{slug}", response_model=AnatomyFigureResponse)
async def get_anatomy_figure(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """Get one figure's metadata."""
    figure = await anatomy_service.get_anatomy_figure(db, slug)
    if not figure:
        raise HTTPException(status_code=404, detail="Figure not found")
    return figure.to_dict()


@router.get("/figures/{slug}/image")
async def get_anatomy_figure_image(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> FileResponse:
    """Serve the figure's cropped image file (WebP/PNG)."""
    figure = await anatomy_service.get_anatomy_figure(db, slug)
    if not figure:
        raise HTTPException(status_code=404, detail="Figure not found")
    abspath = anatomy_service.figure_image_abspath(figure)
    if not abspath or not abspath.exists():
        raise HTTPException(status_code=404, detail="Figure image file not found")
    media = "image/webp" if abspath.suffix == ".webp" else "image/png"
    return FileResponse(str(abspath), media_type=media)


@router.get("/figures/{slug}/source-image")
async def get_anatomy_figure_source_image(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> FileResponse:
    """Serve the figure's original uncropped source image (for re-cropping)."""
    figure = await anatomy_service.get_anatomy_figure(db, slug)
    if not figure:
        raise HTTPException(status_code=404, detail="Figure not found")
    abspath = anatomy_service.figure_source_abspath(figure)
    if not abspath or not abspath.exists():
        raise HTTPException(
            status_code=404, detail="No source image stored for this figure"
        )
    media = (
        "image/webp"
        if abspath.suffix == ".webp"
        else ("image/png" if abspath.suffix == ".png" else "image/jpeg")
    )
    return FileResponse(str(abspath), media_type=media)


@router.post(
    "/figures", response_model=AnatomyFigureResponse, dependencies=[_admin_only]
)
async def create_anatomy_figure(
    label: str = Form(...),
    figure_key: str = Form(...),
    view_key: str = Form(...),
    image: UploadFile = File(...),
    slug: Optional[str] = Form(None),
    source: Optional[UploadFile] = File(None),
    sort_order: int = Form(0),
    is_active: bool = Form(True),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Create a new body figure view with a cropped image upload. SYSTEM_ADMIN.

    Optionally include a ``source`` file — the original uncropped image kept for
    re-cropping in the editor. Accepts WebP or PNG.
    """
    if (image.content_type or "").lower() not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Image must be WebP or PNG")
    slug_val = slug or f"{figure_key}-{view_key}"
    image_bytes = await image.read()
    source_bytes = await source.read() if source else None
    try:
        return (
            await anatomy_service.create_anatomy_figure(
                db,
                slug=slug_val,
                label=label,
                figure_key=figure_key,
                view_key=view_key,
                image_data=image_bytes,
                ext=_ext_for(image),
                source_data=source_bytes,
                source_ext=_ext_for(source) if source else "webp",
                sort_order=sort_order,
                is_active=is_active,
            )
        ).to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/figures/{slug}", response_model=AnatomyFigureResponse, dependencies=[_admin_only]
)
async def update_anatomy_figure(
    slug: str,
    db: AsyncSession = Depends(get_db),
    label: Optional[str] = Form(None),
    figure_key: Optional[str] = Form(None),
    view_key: Optional[str] = Form(None),
    sort_order: Optional[int] = Form(None),
    is_active: Optional[bool] = Form(None),
    image: Optional[UploadFile] = File(None),
    source: Optional[UploadFile] = File(None),
    clear_source: bool = Form(False),
) -> Any:
    """Update a figure (metadata and/or image). SYSTEM_ADMIN.

    - ``image`` replaces the cropped view image.
    - ``source`` replaces the original uncropped source (for re-cropping).
    - ``clear_source`` removes the stored source image.
    """
    figure = await anatomy_service.get_anatomy_figure(db, slug)
    if not figure:
        raise HTTPException(status_code=404, detail="Figure not found")
    image_bytes = ext = None
    if image:
        if (image.content_type or "").lower() not in _ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Image must be WebP or PNG")
        image_bytes = await image.read()
        ext = _ext_for(image)
    source_bytes = source_ext = None
    if source:
        source_bytes = await source.read()
        source_ext = _ext_for(source)
    return (
        await anatomy_service.update_anatomy_figure(
            db,
            figure,
            label=label,
            figure_key=figure_key,
            view_key=view_key,
            sort_order=sort_order,
            is_active=is_active,
            image_data=image_bytes,
            ext=ext or "webp",
            source_data=source_bytes,
            source_ext=source_ext or "webp",
            clear_source=clear_source,
        )
    ).to_dict()


@router.delete("/figures/{slug}", dependencies=[_admin_only])
async def delete_anatomy_figure(
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Delete a figure view and its image file. SYSTEM_ADMIN only."""
    figure = await anatomy_service.get_anatomy_figure(db, slug)
    if not figure:
        raise HTTPException(status_code=404, detail="Figure not found")
    await anatomy_service.delete_anatomy_figure(db, figure)
    return {"detail": "Figure deleted"}


@router.patch("/{identifier}", response_model=AnatomyStructureResponse)
async def update_anatomy_structure(
    identifier: str,
    structure_in: AnatomyStructureUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """
    Update an existing anatomy structure. Only fields provided in the body are
    changed. Global (non-custom) structures require a SYSTEM_ADMIN role.
    """
    structure = await anatomy_service.get_anatomy_structure_by_id_or_slug(
        db, identifier, current_user.tenant_id
    )
    if not structure:
        raise HTTPException(status_code=404, detail="Anatomy structure not found")

    is_global = structure.tenant_id is None
    if is_global and current_user.role != Role.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Only system admins can edit global anatomy structures",
        )

    return await anatomy_service.update_anatomy_structure(db, structure, structure_in)


@router.delete("/{identifier}")
async def delete_anatomy_structure(
    identifier: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """
    Delete an anatomy structure. Global (non-custom) structures require a
    SYSTEM_ADMIN role.
    """
    structure = await anatomy_service.get_anatomy_structure_by_id_or_slug(
        db, identifier, current_user.tenant_id
    )
    if not structure:
        raise HTTPException(status_code=404, detail="Anatomy structure not found")

    is_global = structure.tenant_id is None
    if is_global and current_user.role != Role.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Only system admins can delete global anatomy structures",
        )

    await anatomy_service.delete_anatomy_structure(db, structure)
    return {"detail": "Anatomy structure deleted"}


@router.get("/{identifier}", response_model=AnatomyGraphNode)
async def get_anatomy_structure(
    identifier: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """
    Get a specific anatomical structure by ID or Slug.
    """
    structure = await anatomy_service.get_anatomy_structure_by_id_or_slug(
        db, identifier, current_user.tenant_id
    )
    if not structure:
        raise HTTPException(status_code=404, detail="Anatomy structure not found")
    return structure


@router.post("/relations", response_model=AnatomyRelationResponse)
async def create_anatomy_relation(
    relation_in: AnatomyRelationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """
    Create a relationship (edge) between two anatomy structures.
    """
    # Verify both exist
    source = await anatomy_service.get_anatomy_structure_by_id_or_slug(
        db, str(relation_in.source_id), current_user.tenant_id
    )
    target = await anatomy_service.get_anatomy_structure_by_id_or_slug(
        db, str(relation_in.target_id), current_user.tenant_id
    )

    if not source or not target:
        raise HTTPException(
            status_code=404, detail="Source or Target structure not found"
        )

    return await anatomy_service.create_relation(db, relation_in)


@router.get("/{identifier}/related", response_model=AnatomyRelatedResponse)
async def get_related(
    identifier: str,
    relation_type: Optional[AnatomyRelationType] = None,
    direction: str = Query("both", pattern="^(both|outgoing|incoming)$"),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """
    Traverse the graph to get related structures.
    """
    structure = await anatomy_service.get_anatomy_structure_by_id_or_slug(
        db, identifier, current_user.tenant_id
    )
    if not structure:
        raise HTTPException(status_code=404, detail="Anatomy structure not found")

    relations = await anatomy_service.get_related_structures(
        db, structure.id, relation_type, direction
    )

    # Format the response for the frontend (resolving the target/source objects)
    response = {"outgoing": [], "incoming": []}
    for rel in relations["outgoing"]:
        response["outgoing"].append(
            {"relation_type": rel.relation_type, "structure": rel.target_structure}
        )
    for rel in relations["incoming"]:
        response["incoming"].append(
            {"relation_type": rel.relation_type, "structure": rel.source_structure}
        )

    return response


@router.get("/{identifier}/graph", response_model=AnatomyGraphResponse)
async def get_anatomy_graph(
    identifier: str,
    depth: int = Query(1, ge=1, le=3),
    relation_type: Optional[AnatomyRelationType] = None,
    direction: str = Query("both", pattern="^(both|outgoing|incoming)$"),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> Any:
    """
    Breadth-first traversal of the anatomy graph around ``identifier``.

    Returns the root plus all structures within ``depth`` hops (default 1,
    max 3), along with the edges between them. Each node carries its hop
    distance (``depth``) from the root. Used by the relationship-graph modal
    to render multi-layer neighbourhoods.
    """
    structure = await anatomy_service.get_anatomy_structure_by_id_or_slug(
        db, identifier, current_user.tenant_id
    )
    if not structure:
        raise HTTPException(status_code=404, detail="Anatomy structure not found")

    data = await anatomy_service.get_anatomy_graph(
        db,
        structure,
        tenant_id=current_user.tenant_id,
        depth=depth,
        relation_type=relation_type,
        direction=direction,
    )
    nodes = [{**n["structure"].to_dict(), "depth": n["depth"]} for n in data["nodes"]]
    edges = [
        {
            "source_id": str(e.source_id),
            "target_id": str(e.target_id),
            "relation_type": e.relation_type.value,
        }
        for e in data["edges"]
    ]
    return {"root_id": str(structure.id), "nodes": nodes, "edges": edges}


@router.post("/import")
async def import_anatomy_graph(
    payload: AnatomyImportPayload,
    db: AsyncSession = Depends(get_db),
    _: UserModel = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
) -> Any:
    """
    Import an Anatomy Graph (nodes and edges) from JSON.
    Restricted to SYSTEM_ADMIN since it affects the global ontology.
    """
    service = AnatomyImportService(db)
    stats = await service.import_graph(payload)
    return stats
