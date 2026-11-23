
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.catalogs.policy import (
    DEFAULT_CATALOG_POLICY,
    CatalogReferenceError,
    CatalogUniqueViolation,
)
from app.core.config import settings
from app.models.anatomy_model import AnatomyFigure, AnatomyStructure
from app.models.concept_model import ConceptEdge
from app.models.enums import (
    ConceptKind,
    ConceptRelationType,
    EdgeApprovalStatus,
    EdgeEndpointType,
)
from app.schemas.anatomy import (
    AnatomyRelationCreate,
    AnatomyStructureCreate,
    AnatomyStructureUpdate,
)
from app.services.catalog.concept_service import (
    require_visible_concept,
    resolve_concept_by_slug,
)
from app.services.seed.provenance import mark_user_modified_for_model


async def get_anatomy_structures(
    db: AsyncSession,
    tenant_id: UUID | None = None,
    class_concept_id: UUID | None = None,
    class_concept_ids: list[UUID] | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AnatomyStructure], int]:

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
    db: AsyncSession, identifier: str, tenant_id: UUID | None = None
) -> AnatomyStructure | None:

    query = select(AnatomyStructure)

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


async def _resolve_anatomy_class(
    db: AsyncSession,
    data: dict,
    tenant_id: UUID | None,
    *,
    sent_fields: set[str] | None = None,
) -> None:
    """Resolve/validate the anatomy class reference in a create/update dict
    (in place; pops the ``class_concept_slug`` input convenience).

    Precedence matches the other catalog types: a sent ``class_concept_id``
    wins and is visibility-checked (system rows or the caller's tenant only) —
    on the update path (``sent_fields`` = the schema's ``model_fields_set``)
    an explicit ``null`` id clears the link and any slug sent alongside is
    ignored; otherwise a ``class_concept_slug`` must resolve tenant-explicitly
    — an unknown slug raises the typed 400, never a silent drop. An explicit
    ``null`` slug (or, on create, the absence of both) clears the link.

    ``sent_fields=None`` (the create path, whose full ``model_dump()`` cannot
    distinguish "not sent" from "explicit null") keeps the create reading: a
    non-null id wins, otherwise the slug resolves.
    """
    slug = data.pop("class_concept_slug", None)
    if sent_fields is not None:
        if "class_concept_id" in sent_fields:
            data["class_concept_id"] = await require_visible_concept(
                db, data.get("class_concept_id"), tenant_id
            )
            return
        if slug is None:
            return
    elif data.get("class_concept_id") is not None:
        data["class_concept_id"] = await require_visible_concept(
            db, data["class_concept_id"], tenant_id
        )
        return
    resolved = (
        await resolve_concept_by_slug(db, slug, ConceptKind.ANATOMY_CLASS, tenant_id=tenant_id)
        if slug
        else None
    )
    if slug and resolved is None:
        raise CatalogReferenceError("class concept")
    data["class_concept_id"] = resolved


async def create_anatomy_structure(
    db: AsyncSession,
    structure_in: AnatomyStructureCreate,
    *,
    role: str,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
) -> AnatomyStructure:
    """Create a new anatomy structure. Scope/tenant_id/created_by are stamped
    by the shared ``DEFAULT_CATALOG_POLICY.assign_create_scope`` (same path
    biomarker/medication/allergy/vaccine use) — SYSTEM_ADMIN→system,
    ADMIN/MANAGER→tenant, USER→user."""
    data = structure_in.model_dump()
    await _resolve_anatomy_class(db, data, tenant_id)
    db_structure = AnatomyStructure(**data)
    DEFAULT_CATALOG_POLICY.assign_create_scope(role, db_structure, tenant_id, user_id)
    db.add(db_structure)
    await db.flush()
    await mark_user_modified_for_model(db, AnatomyStructure, db_structure.id)
    await db.commit()
    await db.refresh(db_structure)
    return db_structure


async def update_anatomy_structure(
    db: AsyncSession,
    structure: AnatomyStructure,
    structure_in: AnatomyStructureUpdate,
    *,
    tenant_id: UUID | None = None,
) -> AnatomyStructure:
    """Patch an existing anatomy structure with the provided fields.

    The class reference follows the shared update precedence (see
    :func:`_resolve_anatomy_class`): a sent ``class_concept_id`` wins — an
    explicit ``null`` clears the link, any slug sent alongside is ignored —
    and a bare slug must resolve tenant-explicitly. A new slug is
    pre-checked against the caller's visible rows — the table carries no
    unique index on ``slug`` — and a duplicate raises the same typed 409 the
    create path returns."""
    update_data = structure_in.model_dump(exclude_unset=True)
    await _resolve_anatomy_class(
        db, update_data, tenant_id, sent_fields=set(structure_in.model_fields_set)
    )
    new_slug = update_data.get("slug")
    if new_slug and new_slug != structure.slug:
        existing = await get_anatomy_structure_by_id_or_slug(db, new_slug, tenant_id)
        if existing is not None and existing.id != structure.id:
            raise CatalogUniqueViolation(
                "slug", new_slug, message=f"structure with slug '{new_slug}' already exists"
            )
    for field, value in update_data.items():
        setattr(structure, field, value)
    await mark_user_modified_for_model(db, AnatomyStructure, structure.id)
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


async def create_relation(db: AsyncSession, relation_in: AnatomyRelationCreate) -> ConceptEdge:
    """Create an anatomy→anatomy edge in the unified ``concept_edges`` table.

    The ``anatomy_relations`` table is gone (migration
    ``h9c0d1e2f3a4``); all anatomy hierarchy edges now live in
    ``concept_edges`` with ``src_type='anatomy', dst_type='anatomy'``.
    """
    rel_type = ConceptRelationType(relation_in.relation_type.value)

    # Dedup: check if an identical edge already exists.
    existing = await db.execute(
        select(ConceptEdge).where(
            ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
            ConceptEdge.src_id == relation_in.source_id,
            ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
            ConceptEdge.dst_id == relation_in.target_id,
            ConceptEdge.relation == rel_type,
        )
    )
    found = existing.scalars().first()
    if found:
        return found

    edge = ConceptEdge(
        src_type=EdgeEndpointType.ANATOMY,
        src_id=relation_in.source_id,
        dst_type=EdgeEndpointType.ANATOMY,
        dst_id=relation_in.target_id,
        relation=rel_type,
        status=EdgeApprovalStatus.APPROVED,
    )
    db.add(edge)
    await db.commit()
    await db.refresh(edge)
    return edge


async def get_related_structures(
    db: AsyncSession,
    structure_id: UUID,
    relation_type: ConceptRelationType | None = None,
    direction: str = "both",  # "outgoing", "incoming", "both"
) -> dict[str, Any]:
    """Returns related anatomy structures for a given node, queried from the
    unified ``concept_edges`` table (anatomy→anatomy edges only).

    Returns ``{"outgoing": [{"relation_type": str, "structure": AnatomyStructure}],
    "incoming": [...]}`` — the endpoint serializes this directly.
    """
    response: dict[str, Any] = {"outgoing": [], "incoming": []}

    if direction in ("both", "outgoing"):
        q = (
            select(ConceptEdge, AnatomyStructure)
            .join(
                AnatomyStructure,
                AnatomyStructure.id == ConceptEdge.dst_id,
            )
            .where(
                ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.src_id == structure_id,
                ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.status == EdgeApprovalStatus.APPROVED,
            )
        )
        if relation_type:
            q = q.where(ConceptEdge.relation == relation_type)
        for edge, struct in (await db.execute(q)).all():
            response["outgoing"].append({"relation_type": edge.relation, "structure": struct})

    if direction in ("both", "incoming"):
        q = (
            select(ConceptEdge, AnatomyStructure)
            .join(
                AnatomyStructure,
                AnatomyStructure.id == ConceptEdge.src_id,
            )
            .where(
                ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.dst_id == structure_id,
                ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.status == EdgeApprovalStatus.APPROVED,
            )
        )
        if relation_type:
            q = q.where(ConceptEdge.relation == relation_type)
        for edge, struct in (await db.execute(q)).all():
            response["incoming"].append({"relation_type": edge.relation, "structure": struct})

    return response


async def get_anatomy_graph(
    db: AsyncSession,
    root: AnatomyStructure,
    tenant_id: UUID | None = None,
    depth: int = 1,
    relation_type: ConceptRelationType | None = None,
    direction: str = "both",
) -> dict[str, Any]:
    """Breadth-first traversal of the anatomy graph from ``root`` up to ``depth``
    hops, querying the unified ``concept_edges`` table (anatomy→anatomy edges
    only).

    Returns ``{"nodes": [{"structure": AnatomyStructure, "depth": int}, ...],
    "edges": [{"source_id": UUID, "target_id": UUID, "relation_type": str}, ...]}``.
    """
    if depth < 1:
        depth = 1

    root_id = root.id
    nodes: dict[UUID, dict[str, Any]] = {root_id: {"structure": root, "depth": 0}}
    visible_ids: set = {root_id}
    edges_seen: set = set()
    edge_rows: list[dict[str, Any]] = []
    frontier: set = {root_id}

    for hop in range(1, depth + 1):
        if not frontier:
            break

        neighbor_ids: set = set()
        new_edges: list[tuple] = []

        if direction in ("both", "outgoing"):
            q = select(ConceptEdge).where(
                ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.src_id.in_(frontier),
                ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.status == EdgeApprovalStatus.APPROVED,
            )
            if relation_type:
                q = q.where(ConceptEdge.relation == relation_type)
            for e in (await db.execute(q)).scalars().all():
                new_edges.append((e.src_id, e.dst_id, e.relation, e))
                neighbor_ids.add(e.dst_id)

        if direction in ("both", "incoming"):
            q = select(ConceptEdge).where(
                ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.dst_id.in_(frontier),
                ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.status == EdgeApprovalStatus.APPROVED,
            )
            if relation_type:
                q = q.where(ConceptEdge.relation == relation_type)
            for e in (await db.execute(q)).scalars().all():
                new_edges.append((e.src_id, e.dst_id, e.relation, e))
                neighbor_ids.add(e.src_id)

        for src_id, dst_id, rel, _edge in new_edges:
            key = (src_id, dst_id, rel)
            if key not in edges_seen:
                edges_seen.add(key)
                edge_rows.append(
                    {
                        "source_id": src_id,
                        "target_id": dst_id,
                        "relation_type": rel,
                    }
                )

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
        e for e in edge_rows if e["source_id"] in visible_ids and e["target_id"] in visible_ids
    ]

    return {"nodes": list(nodes.values()), "edges": edge_rows}


# --- Anatomy figures (DB-driven body atlas, raster images) ---

#: Legacy local subdirectory; pre-contract rows store ``<this>/<slug>.webp``.
FIGURES_DIR = "anatomy_figures"


def _figure_artifact(value: str | None) -> tuple[str | None, Path | None]:
    """Split a stored figure location into ``(key, fallback_path)``.

    New rows hold a tenant-prefixed object key; pre-contract rows hold the
    old ``anatomy_figures/...`` relative path, which the dual-read branch
    resolves under the local upload root while that volume is mounted. The
    pre-contract prefix is matched explicitly: a relative segment like
    ``anatomy_figures/man-front.webp`` would otherwise look like an object
    key and be looked up in the wrong place.
    """
    if not value:
        return None, None
    if value.startswith(f"{FIGURES_DIR}/"):
        return None, Path(str(settings.HA_UPLOAD_DIR)) / value
    from app.storage.artifacts import looks_like_key

    if looks_like_key(value):
        return value, None
    return None, Path(str(settings.HA_UPLOAD_DIR)) / value


def figure_key_for(slug: str, ext: str = "webp", *, source: bool = False) -> str:
    """Server-generated anatomy key (slug sanitized to one segment)."""
    import re

    from app.storage.artifacts import anatomy_key
    from app.storage.object_storage import safe_extension

    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(slug)).strip(".-") or "figure"
    extension = safe_extension(ext or "webp") or ".webp"
    return anatomy_key(safe_slug, extension, source=source)


def save_figure_image(
    slug: str, data: bytes, ext: str = "webp", kind: str = "image"
) -> tuple[str, int, int]:
    """Store image bytes and return ``(object_key, width, height)``.

    The slug is sanitised to a strict single segment (never a path); the
    object lives under ``tenants/system/anatomy/``. ``kind='source'`` stores
    the uncropped original alongside the rendered figure.
    """
    import io

    from PIL import Image as PILImage

    from app.storage.documents import document_storage

    key = figure_key_for(slug, ext, source=kind == "source")
    extension = Path(key).suffix.lower()
    media = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(extension, "application/octet-stream")
    document_storage().put_key(key, data, content_type=media)
    with PILImage.open(io.BytesIO(data)) as img:
        w, h = img.size
    return key, w, h


def figure_object_exists(slug: str, ext: str = "webp", *, source: bool = False) -> bool:
    """Whether the keyed figure object is already present in the store."""
    from app.storage.artifacts import ArtifactStorage

    return ArtifactStorage().exists(figure_key_for(slug, ext, source=source))


def figure_image_key(figure: AnatomyFigure) -> tuple[str | None, Path | None]:
    return _figure_artifact(figure.image_path)


def figure_source_key(figure: AnatomyFigure) -> tuple[str | None, Path | None]:
    return _figure_artifact(figure.source_image_path)


def read_figure_image(figure: AnatomyFigure, *, source: bool = False) -> bytes | None:
    """Read the figure bytes through the store (pre-contract local path)."""
    from app.storage.artifacts import ArtifactStorage

    key, fallback = figure_source_key(figure) if source else figure_image_key(figure)
    if key is None and fallback is None:
        return None
    return ArtifactStorage().read_with_fallback(key, fallback)


async def aread_figure_image(figure: AnatomyFigure, *, source: bool = False) -> bytes | None:
    """Async bridge: figure bytes without blocking the event loop."""
    import anyio

    return await anyio.to_thread.run_sync(
        lambda: read_figure_image(figure, source=source)
    )


def figure_media_type(figure: AnatomyFigure, *, source: bool = False) -> str:
    value = (figure.source_image_path if source else figure.image_path) or ""
    suffix = Path(value).suffix.lower()
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".png":
        return "image/png"
    return "image/jpeg"


async def list_anatomy_figures(
    db: AsyncSession,
    active_only: bool = True,
) -> list[AnatomyFigure]:
    query = select(AnatomyFigure).order_by(
        AnatomyFigure.figure_key.asc(), AnatomyFigure.sort_order.asc()
    )
    if active_only:
        query = query.where(AnatomyFigure.is_active.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_anatomy_figure(db: AsyncSession, slug: str) -> AnatomyFigure | None:
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
    source_data: bytes | None = None,
    source_ext: str = "webp",
    sort_order: int = 0,
    is_active: bool = True,
) -> AnatomyFigure:
    existing = await get_anatomy_figure(db, slug)
    if existing:
        raise ValueError(f"Figure with slug '{slug}' already exists")
    from functools import partial

    import anyio

    rel_path, w, h = await anyio.to_thread.run_sync(
        partial(save_figure_image, slug, image_data, ext)
    )
    source_path = None
    if source_data:
        source_path, _, _ = await anyio.to_thread.run_sync(
            partial(save_figure_image, slug, source_data, source_ext, kind="source")
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
    label: str | None = None,
    figure_key: str | None = None,
    view_key: str | None = None,
    sort_order: int | None = None,
    is_active: bool | None = None,
    image_data: bytes | None = None,
    ext: str = "webp",
    source_data: bytes | None = None,
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
    from functools import partial

    import anyio

    from app.storage.artifacts import ArtifactStorage

    if image_data:
        previous_key, previous_fallback = figure_image_key(figure)
        if previous_key or previous_fallback:
            await anyio.to_thread.run_sync(
                ArtifactStorage().delete_with_fallback, previous_key, previous_fallback
            )
        rel_path, w, h = await anyio.to_thread.run_sync(
            partial(save_figure_image, figure.slug, image_data, ext)
        )
        figure.image_path = rel_path
        figure.width = w
        figure.height = h
    if source_data:
        stale_key, stale_fallback = figure_source_key(figure)
        if stale_key or stale_fallback:
            await anyio.to_thread.run_sync(
                ArtifactStorage().delete_with_fallback, stale_key, stale_fallback
            )
        source_path, _, _ = await anyio.to_thread.run_sync(
            partial(save_figure_image, figure.slug, source_data, source_ext, kind="source")
        )
        figure.source_image_path = source_path
    elif clear_source:
        stale_key, stale_fallback = figure_source_key(figure)
        if stale_key or stale_fallback:
            await anyio.to_thread.run_sync(
                ArtifactStorage().delete_with_fallback, stale_key, stale_fallback
            )
        figure.source_image_path = None
    await db.commit()
    await db.refresh(figure)
    return figure


async def delete_anatomy_figure(db: AsyncSession, figure: AnatomyFigure) -> None:
    import anyio

    from app.storage.artifacts import ArtifactStorage

    storage = ArtifactStorage()
    for key, fallback in (figure_image_key(figure), figure_source_key(figure)):
        if key or fallback:
            await anyio.to_thread.run_sync(storage.delete_with_fallback, key, fallback)
    await db.delete(figure)
    await db.commit()
