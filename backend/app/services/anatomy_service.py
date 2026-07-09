from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import selectinload
from typing import List, Optional, Dict, Any, Tuple
from uuid import UUID
from pathlib import Path

from app.models.anatomy_model import AnatomyStructure, AnatomyRelation, AnatomyFigure
from app.schemas.anatomy import (
    AnatomyStructureCreate,
    AnatomyStructureUpdate,
    AnatomyRelationCreate,
)
from app.models.enums import AnatomyRelationType, ConceptKind
from app.core.config import settings
from app.services.concept_service import resolve_concept_by_slug


async def get_anatomy_structures(
    db: AsyncSession,
    tenant_id: Optional[UUID] = None,
    class_concept_id: Optional[UUID] = None,
    class_concept_ids: Optional[List[UUID]] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[List[AnatomyStructure], int]:

    query = select(AnatomyStructure)

    if tenant_id:
        query = query.where(
            or_(
                AnatomyStructure.tenant_id == tenant_id,
                AnatomyStructure.tenant_id.is_(None),
            )
        )
    else:
        query = query.where(AnatomyStructure.tenant_id.is_(None))

    if class_concept_id:
        query = query.where(AnatomyStructure.class_concept_id == class_concept_id)

    if class_concept_ids:
        query = query.where(AnatomyStructure.class_concept_id.in_(class_concept_ids))

    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                AnatomyStructure.name.ilike(term),
                AnatomyStructure.slug.ilike(term),
                AnatomyStructure.standard_code.ilike(term),
                AnatomyStructure.description.ilike(term),
            )
        )

    total_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(total_query)
    total = total_result.scalar() or 0

    query = query.order_by(AnatomyStructure.name.asc()).offset(offset).limit(limit)
    result = await db.execute(query)
    items = result.scalars().all()

    return list(items), total


async def get_anatomy_structure_by_id_or_slug(
    db: AsyncSession, identifier: str, tenant_id: Optional[UUID] = None
) -> Optional[AnatomyStructure]:

    query = select(AnatomyStructure).options(
        selectinload(AnatomyStructure.outgoing_relations),
        selectinload(AnatomyStructure.incoming_relations),
    )

    try:
        uuid_val = UUID(identifier)
        query = query.where(AnatomyStructure.id == uuid_val)
    except ValueError:
        query = query.where(AnatomyStructure.slug == identifier)

    if tenant_id:
        query = query.where(
            or_(
                AnatomyStructure.tenant_id == tenant_id,
                AnatomyStructure.tenant_id.is_(None),
            )
        )
    else:
        query = query.where(AnatomyStructure.tenant_id.is_(None))

    result = await db.execute(query)
    return result.scalars().first()


async def create_anatomy_structure(
    db: AsyncSession,
    structure_in: AnatomyStructureCreate,
    tenant_id: Optional[UUID] = None,
) -> AnatomyStructure:
    data = structure_in.model_dump()
    # Resolve the friendly class slug to a class_concept_id (takes precedence
    # over an explicit class_concept_id if both are supplied).
    slug = data.pop("class_concept_slug", None)
    if slug:
        data["class_concept_id"] = await resolve_concept_by_slug(
            db, slug, ConceptKind.ANATOMY_CLASS
        )
    db_structure = AnatomyStructure(**data, tenant_id=tenant_id)
    db.add(db_structure)
    await db.commit()
    await db.refresh(db_structure)
    return db_structure


async def update_anatomy_structure(
    db: AsyncSession,
    structure: AnatomyStructure,
    structure_in: AnatomyStructureUpdate,
) -> AnatomyStructure:
    """Patch an existing anatomy structure with the provided fields."""
    update_data = structure_in.model_dump(exclude_unset=True)
    slug = update_data.pop("class_concept_slug", None)
    if slug is not None:
        update_data["class_concept_id"] = (
            await resolve_concept_by_slug(db, slug, ConceptKind.ANATOMY_CLASS)
            if slug
            else None
        )
    for field, value in update_data.items():
        setattr(structure, field, value)
    await db.commit()
    await db.refresh(structure)
    return structure


async def delete_anatomy_structure(
    db: AsyncSession,
    structure: AnatomyStructure,
) -> None:
    """Delete an anatomy structure and cascade its relations."""
    await db.delete(structure)
    await db.commit()


async def create_relation(
    db: AsyncSession, relation_in: AnatomyRelationCreate
) -> AnatomyRelation:
    # Optional: Check if relation exists to avoid UniqueViolation
    existing_query = select(AnatomyRelation).where(
        and_(
            AnatomyRelation.source_id == relation_in.source_id,
            AnatomyRelation.target_id == relation_in.target_id,
            AnatomyRelation.relation_type == relation_in.relation_type,
        )
    )
    result = await db.execute(existing_query)
    existing = result.scalars().first()
    if existing:
        return existing

    db_relation = AnatomyRelation(**relation_in.model_dump())
    db.add(db_relation)
    await db.commit()
    await db.refresh(db_relation)
    return db_relation


async def get_related_structures(
    db: AsyncSession,
    structure_id: UUID,
    relation_type: Optional[AnatomyRelationType] = None,
    direction: str = "both",  # "outgoing", "incoming", "both"
) -> Dict[str, Any]:
    """
    Returns the related structures for a given node.
    Useful for the Graph Traversal in the UI.
    """
    response = {"outgoing": [], "incoming": []}

    if direction in ["both", "outgoing"]:
        q = (
            select(AnatomyRelation)
            .options(selectinload(AnatomyRelation.target_structure))
            .where(AnatomyRelation.source_id == structure_id)
        )
        if relation_type:
            q = q.where(AnatomyRelation.relation_type == relation_type)
        res = await db.execute(q)
        response["outgoing"] = res.scalars().all()

    if direction in ["both", "incoming"]:
        q = (
            select(AnatomyRelation)
            .options(selectinload(AnatomyRelation.source_structure))
            .where(AnatomyRelation.target_id == structure_id)
        )
        if relation_type:
            q = q.where(AnatomyRelation.relation_type == relation_type)
        res = await db.execute(q)
        response["incoming"] = res.scalars().all()

    return response


async def get_anatomy_graph(
    db: AsyncSession,
    root: AnatomyStructure,
    tenant_id: Optional[UUID] = None,
    depth: int = 1,
    relation_type: Optional[AnatomyRelationType] = None,
    direction: str = "both",
) -> Dict[str, Any]:
    """Breadth-first traversal of the anatomy graph from ``root`` up to ``depth`` hops.

    Returns ``{"nodes": [{"structure": AnatomyStructure, "depth": int}, ...],
    "edges": [AnatomyRelation, ...]}``. The root is included at depth 0 and
    edges are deduplicated by ``(source_id, target_id, relation_type)``. Each hop
    issues a single query per direction, so cost scales with ``depth`` (not with
    node count). Nodes are tenant-scoped (tenant or global) to prevent leakage.
    """
    if depth < 1:
        depth = 1

    root_id = root.id
    nodes: Dict[UUID, Dict[str, Any]] = {root_id: {"structure": root, "depth": 0}}
    visible_ids: set = {root_id}
    edges_seen: set = set()
    edge_rows: List[AnatomyRelation] = []
    frontier: set = {root_id}

    for hop in range(1, depth + 1):
        if not frontier:
            break

        neighbor_ids: set = set()
        new_edges: List[AnatomyRelation] = []

        if direction in ("both", "outgoing"):
            q = select(AnatomyRelation).where(AnatomyRelation.source_id.in_(frontier))
            if relation_type:
                q = q.where(AnatomyRelation.relation_type == relation_type)
            for r in (await db.execute(q)).scalars().all():
                new_edges.append(r)
                neighbor_ids.add(r.target_id)

        if direction in ("both", "incoming"):
            q = select(AnatomyRelation).where(AnatomyRelation.target_id.in_(frontier))
            if relation_type:
                q = q.where(AnatomyRelation.relation_type == relation_type)
            for r in (await db.execute(q)).scalars().all():
                new_edges.append(r)
                neighbor_ids.add(r.source_id)

        for r in new_edges:
            key = (r.source_id, r.target_id, r.relation_type)
            if key not in edges_seen:
                edges_seen.add(key)
                edge_rows.append(r)

        # Load structures for newly discovered neighbors (tenant-scoped).
        to_load = [nid for nid in neighbor_ids if nid not in nodes]
        next_frontier: set = set()
        if to_load:
            q = select(AnatomyStructure).where(AnatomyStructure.id.in_(to_load))
            if tenant_id:
                q = q.where(
                    or_(
                        AnatomyStructure.tenant_id == tenant_id,
                        AnatomyStructure.tenant_id.is_(None),
                    )
                )
            for s in (await db.execute(q)).scalars().all():
                nodes[s.id] = {"structure": s, "depth": hop}
                visible_ids.add(s.id)
                next_frontier.add(s.id)

        frontier = next_frontier

    # Drop edges whose endpoints were filtered out by tenant scoping.
    edge_rows = [
        e
        for e in edge_rows
        if e.source_id in visible_ids and e.target_id in visible_ids
    ]

    return {"nodes": list(nodes.values()), "edges": edge_rows}


# --- Anatomy figures (DB-driven body atlas, raster images) ---

FIGURES_DIR = "anatomy_figures"  # subdirectory under UPLOAD_DIR


def _figures_base_dir() -> Path:
    """Absolute path to the anatomy figures image directory under UPLOAD_DIR."""
    base = Path(str(settings.UPLOAD_DIR)) / FIGURES_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_figure_image(
    slug: str, data: bytes, ext: str = "webp", kind: str = "image"
) -> Tuple[str, int, int]:
    """Write image bytes to UPLOAD_DIR/anatomy_figures/{slug}.{ext} (or
    {slug}-source.{ext} when kind='source') and return (relative_path, w, h)."""
    from PIL import Image as PILImage
    import io

    base = _figures_base_dir()
    suffix = "-source" if kind == "source" else ""
    path = base / f"{slug}{suffix}.{ext}"
    path.write_bytes(data)
    with PILImage.open(io.BytesIO(data)) as img:
        w, h = img.size
    return f"{FIGURES_DIR}/{slug}{suffix}.{ext}", w, h


def figure_image_abspath(figure: AnatomyFigure) -> Optional[Path]:
    if not figure.image_path:
        return None
    return Path(str(settings.UPLOAD_DIR)) / figure.image_path


def figure_source_abspath(figure: AnatomyFigure) -> Optional[Path]:
    if not figure.source_image_path:
        return None
    return Path(str(settings.UPLOAD_DIR)) / figure.source_image_path


async def list_anatomy_figures(
    db: AsyncSession,
    active_only: bool = True,
) -> List[AnatomyFigure]:
    query = select(AnatomyFigure).order_by(
        AnatomyFigure.figure_key.asc(), AnatomyFigure.sort_order.asc()
    )
    if active_only:
        query = query.where(AnatomyFigure.is_active.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_anatomy_figure(db: AsyncSession, slug: str) -> Optional[AnatomyFigure]:
    result = await db.execute(select(AnatomyFigure).where(AnatomyFigure.slug == slug))
    return result.scalars().first()


async def create_anatomy_figure(
    db: AsyncSession,
    *,
    slug: str,
    label: str,
    figure_key: str,
    view_key: str,
    image_data: bytes,
    ext: str = "webp",
    source_data: Optional[bytes] = None,
    source_ext: str = "webp",
    sort_order: int = 0,
    is_active: bool = True,
) -> AnatomyFigure:
    existing = await get_anatomy_figure(db, slug)
    if existing:
        raise ValueError(f"Figure with slug '{slug}' already exists")
    rel_path, w, h = save_figure_image(slug, image_data, ext)
    source_path = None
    if source_data:
        source_path, _, _ = save_figure_image(
            slug, source_data, source_ext, kind="source"
        )
    db_figure = AnatomyFigure(
        slug=slug,
        label=label,
        figure_key=figure_key,
        view_key=view_key,
        image_path=rel_path,
        source_image_path=source_path,
        width=w,
        height=h,
        sort_order=sort_order,
        is_active=is_active,
    )
    db.add(db_figure)
    await db.commit()
    await db.refresh(db_figure)
    return db_figure


async def update_anatomy_figure(
    db: AsyncSession,
    figure: AnatomyFigure,
    *,
    label: Optional[str] = None,
    figure_key: Optional[str] = None,
    view_key: Optional[str] = None,
    sort_order: Optional[int] = None,
    is_active: Optional[bool] = None,
    image_data: Optional[bytes] = None,
    ext: str = "webp",
    source_data: Optional[bytes] = None,
    source_ext: str = "webp",
    clear_source: bool = False,
) -> AnatomyFigure:
    if label is not None:
        figure.label = label
    if figure_key is not None:
        figure.figure_key = figure_key
    if view_key is not None:
        figure.view_key = view_key
    if sort_order is not None:
        figure.sort_order = sort_order
    if is_active is not None:
        figure.is_active = is_active
    if image_data:
        old = figure_image_abspath(figure)
        if old and old.exists():
            old.unlink()
        rel_path, w, h = save_figure_image(figure.slug, image_data, ext)
        figure.image_path = rel_path
        figure.width = w
        figure.height = h
    if source_data:
        old_src = figure_source_abspath(figure)
        if old_src and old_src.exists():
            old_src.unlink()
        source_path, _, _ = save_figure_image(
            figure.slug, source_data, source_ext, kind="source"
        )
        figure.source_image_path = source_path
    elif clear_source:
        old_src = figure_source_abspath(figure)
        if old_src and old_src.exists():
            old_src.unlink()
        figure.source_image_path = None
    await db.commit()
    await db.refresh(figure)
    return figure


async def delete_anatomy_figure(db: AsyncSession, figure: AnatomyFigure) -> None:
    for p in (figure_image_abspath(figure), figure_source_abspath(figure)):
        if p and p.exists():
            p.unlink()
    await db.delete(figure)
    await db.commit()
