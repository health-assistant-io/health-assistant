"""Concept service — CRUD, tenancy, RBAC, and graph traversal.

Encapsulates all business logic for the unified ``concepts`` table and the
polymorphic ``concept_edges`` graph. Enforces:

- **Tenancy**: global rows (``tenant_id IS NULL``) are visible to everyone;
  tenant rows are visible only within their tenant. Writes follow the same
  rule plus the RBAC gate below.
- **RBAC**: only ``SYSTEM_ADMIN`` may create / edit / delete **global**
  concepts or edges. ``ADMIN`` / ``MANAGER`` may manage **tenant-scoped** rows.
  ``USER`` is read-only. ``SYSTEM_ADMIN`` bypasses all checks.
- **Lifecycle**: concepts are soft-deleted (``deleted_at``). A concept with
  active edges or FK references refuses hard operations — it is retired
  (``status = RETIRED``) instead. Only ``status='approved'`` edges count for
  graph queries; ``proposed`` edges are HITL-pending.
- **Polymorphic edges**: there is no cross-table FK on ``concept_edges`` —
  the service layer validates endpoint existence before insert.
"""

from __future__ import annotations

from typing import Any, List, Optional
from uuid import UUID

from sqlalchemy import select, or_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.concept_model import Concept, ConceptEdge, ConceptKindTag
from app.models.enums import (
    ConceptKind,
    ConceptStatus,
    ConceptProvenance,
    EdgeApprovalStatus,
    EdgeEndpointType,
    ConceptRelationType,
    Role,
)


def concepts_with_kind(kind: ConceptKind):
    """SQLAlchemy predicate: concept ids carrying the given kind tag.

    Replaces every legacy ``Concept.kind == kind`` filter now that domain
    membership lives in the ``concept_kind_tags`` join table.
    """
    return Concept.id.in_(
        select(ConceptKindTag.concept_id).where(ConceptKindTag.kind == kind)
    )


def sync_concept_kind_tags(concept: Concept, desired: List[ConceptKind]) -> None:
    """Reconcile ``concept.kind_tags`` to match ``desired`` — diffing in place.

    Removes tags no longer desired, adds missing ones, leaves shared tags
    untouched. Mutating in place (rather than reassigning the collection) avoids
    the ``(concept_id, kind)`` unique-constraint violation that wholesale
    replacement hits on flush (SQLAlchemy emits INSERTs before DELETEs).
    Also updates ``primary_kind`` if the current value falls out of the set.

    Shared by ``ConceptService.update_concept`` and the concept seed loader so
    both write paths keep a concept's kind tags in sync with their input.
    """
    if not desired:
        raise ValueError("at least one kind is required")
    desired_set = set(desired)
    for tag in list(concept.kind_tags):
        if tag.kind not in desired_set:
            concept.kind_tags.remove(tag)
    existing = {tag.kind for tag in concept.kind_tags}
    for k in desired:
        if k not in existing:
            concept.kind_tags.append(ConceptKindTag(kind=k))
    if concept.primary_kind is None or concept.primary_kind not in desired_set:
        concept.primary_kind = desired[0]


async def resolve_concept_by_slug(
    db: AsyncSession,
    slug: str,
    kind: Optional[ConceptKind] = None,
    tenant_id: Optional[UUID] = None,
) -> Optional[UUID]:
    """Look up a concept by ``slug`` (optionally + ``kind`` tag) and return its ID.

    Tenant-aware: matches either global rows (``tenant_id IS NULL``) or rows
    scoped to ``tenant_id``. Slug is globally unique per tenant after the
    multi-kind consolidation, so ``kind`` is now optional — when provided it
    narrows the match to concepts carrying that kind tag (a safety check for
    callers that expect a specific domain).
    """
    if not slug:
        return None
    stmt = select(Concept.id).where(
        Concept.slug == slug,
        or_(
            Concept.tenant_id.is_(None),
            Concept.tenant_id == tenant_id,
        ),
        Concept.deleted_at.is_(None),
    )
    if kind is not None:
        stmt = stmt.where(concepts_with_kind(kind))
    row = (await db.execute(stmt)).first()
    return row[0] if row else None


def biomarker_category_to_concept_slug(category: Optional[str]) -> Optional[str]:
    """Convert a legacy biomarker ``category`` string (e.g. ``blood_laboratory``,
    ``vital_signs``) into the matching concept slug.

    The legacy strings used underscores; concept slugs use hyphens
    (e.g. ``blood_laboratory`` -> ``blood-laboratory``). After the multi-kind
    consolidation the biomarker-class concepts share their slug with the
    examination/document categories (no ``-class`` suffix), so a single concept
    carries all relevant kind tags.
    Returns ``None`` if no mapping is possible.
    """
    if not category:
        return None
    raw = category.strip().lower()
    if not raw:
        return None
    return raw.replace("_", "-")


async def resolve_biomarker_class_concept(
    db: AsyncSession,
    category: Optional[str],
    tenant_id: Optional[UUID] = None,
) -> Optional[UUID]:
    """Resolve a legacy biomarker ``category`` string to a concept ID."""
    slug = biomarker_category_to_concept_slug(category)
    if not slug:
        return None
    return await resolve_concept_by_slug(
        db, slug, ConceptKind.BIOMARKER_CLASS, tenant_id=tenant_id
    )


class ConceptService:
    """Service for Concept + ConceptEdge CRUD and graph traversal.

    Instantiate per request/task with the active session::

        svc = ConceptService(db)
        concepts = await svc.list_concepts(kind=ConceptKind.SPECIALTY, tenant_id=tid)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Concept reads
    # ------------------------------------------------------------------

    async def list_concepts(
        self,
        tenant_id: Optional[UUID],
        kind: Optional[ConceptKind] = None,
        status: Optional[ConceptStatus] = None,
        parent_id: Optional[UUID] = None,
        include_retired: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Concept]:
        """List concepts visible to the caller's tenant, optionally filtered."""
        stmt = select(Concept).where(
            or_(
                Concept.tenant_id.is_(None),
                Concept.tenant_id == tenant_id,
            ),
            Concept.deleted_at.is_(None),
        )
        if kind is not None:
            stmt = stmt.where(concepts_with_kind(kind))
        if parent_id is not None:
            stmt = stmt.where(Concept.parent_id == parent_id)
        if not include_retired:
            stmt = stmt.where(Concept.status == ConceptStatus.ACTIVE)
        elif status is not None:
            stmt = stmt.where(Concept.status == status)
        stmt = (
            stmt.order_by(
                Concept.display_order.asc(),
                Concept.name.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_concept(
        self, concept_id: UUID, tenant_id: Optional[UUID]
    ) -> Optional[Concept]:
        """Fetch a single concept by ID, enforcing tenancy."""
        stmt = select(Concept).where(
            Concept.id == concept_id,
            Concept.deleted_at.is_(None),
            or_(
                Concept.tenant_id.is_(None),
                Concept.tenant_id == tenant_id,
            ),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Concept writes
    # ------------------------------------------------------------------

    async def create_concept(
        self,
        *,
        slug: str,
        name: str,
        tenant_id: Optional[UUID],
        role: str,
        kind: Optional[ConceptKind] = None,
        kinds: Optional[List[ConceptKind]] = None,
        description: Optional[str] = None,
        parent_id: Optional[UUID] = None,
        coding_system: Optional[str] = None,
        code: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        icon: Optional[dict] = None,
        color: Optional[str] = None,
        display_order: int = 0,
        meta_data: Optional[dict] = None,
        created_by: Optional[UUID] = None,
    ) -> Concept:
        """Create a concept. Global concepts (tenant_id=None) require SYSTEM_ADMIN.

        ``kind`` (legacy, single) and ``kinds`` (new, multi) are mutually
        conveniences; at least one kind must be supplied. ``primary_kind`` is
        set to the first kind.

        Raises ``PermissionError`` if a non-SYSTEM_ADMIN tries to create a
        global concept, or a USER tries any create.
        """
        self._check_write_role(role)
        if tenant_id is None and role != Role.SYSTEM_ADMIN.value:
            raise PermissionError("Only SYSTEM_ADMIN can create global concepts")

        resolved_kinds: List[ConceptKind] = (
            list(kinds) if kinds is not None else ([kind] if kind is not None else [])
        )
        if not resolved_kinds:
            raise ValueError("at least one kind is required")

        concept = Concept(
            slug=slug.strip(),
            name=name.strip(),
            primary_kind=resolved_kinds[0],
            tenant_id=tenant_id,
            description=description,
            parent_id=parent_id,
            coding_system=coding_system,
            code=code,
            aliases=aliases or [],
            icon=icon,
            color=color,
            display_order=display_order,
            meta_data=meta_data,
            status=ConceptStatus.ACTIVE,
            created_by=created_by,
        )
        for k in resolved_kinds:
            concept.kind_tags.append(ConceptKindTag(kind=k))
        self.db.add(concept)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ValueError(f"Concept with slug='{slug}' already exists") from exc
        return concept

    async def update_concept(
        self,
        concept_id: UUID,
        tenant_id: Optional[UUID],
        role: str,
        **fields: Any,
    ) -> Concept:
        """Update a concept's mutable fields. Enforces ownership + RBAC."""
        concept = await self.get_concept(concept_id, tenant_id)
        if concept is None:
            raise ValueError("Concept not found")

        if concept.is_global and role != Role.SYSTEM_ADMIN.value:
            raise PermissionError("Only SYSTEM_ADMIN can edit global concepts")
        if not concept.is_global and role == Role.USER.value:
            raise PermissionError("USER role cannot edit concepts")

        allowed = {
            "name",
            "description",
            "parent_id",
            "coding_system",
            "code",
            "aliases",
            "icon",
            "color",
            "status",
            "display_order",
            "meta_data",
            "primary_kind",
        }
        for key, value in fields.items():
            if key in allowed:
                setattr(concept, key, value)
            elif key == "kinds":
                # Replace the full kind-tag set (diffed in place — see helper).
                resolved = [ConceptKind(k) for k in value]
                sync_concept_kind_tags(concept, resolved)
            elif key == "slug":
                raise ValueError("slug is immutable after creation")

        await self.db.flush()
        return concept

    async def delete_concept(
        self, concept_id: UUID, tenant_id: Optional[UUID], role: str
    ) -> None:
        """Soft-delete a concept (sets ``deleted_at``).

        If the concept has active edges, it is **retired** instead of deleted
        to preserve graph integrity. A truly orphaned concept (no edges, no FK
        references) is soft-deleted.
        """
        concept = await self.get_concept(concept_id, tenant_id)
        if concept is None:
            raise ValueError("Concept not found")

        if concept.is_global and role != Role.SYSTEM_ADMIN.value:
            raise PermissionError("Only SYSTEM_ADMIN can delete global concepts")
        if not concept.is_global and role == Role.USER.value:
            raise PermissionError("USER role cannot delete concepts")

        edge_count = await self._count_active_edges_for_concept(concept_id, tenant_id)
        if edge_count > 0:
            concept.status = ConceptStatus.RETIRED
            await self.db.flush()
            return

        concept.status = ConceptStatus.RETIRED
        from datetime import datetime, timezone

        concept.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()

    # ------------------------------------------------------------------
    # Edge reads
    # ------------------------------------------------------------------

    async def get_edges(
        self,
        tenant_id: Optional[UUID],
        src_type: Optional[EdgeEndpointType] = None,
        src_id: Optional[UUID] = None,
        dst_type: Optional[EdgeEndpointType] = None,
        dst_id: Optional[UUID] = None,
        relation: Optional[ConceptRelationType] = None,
        include_proposed: bool = False,
        limit: int = 200,
    ) -> List[ConceptEdge]:
        """List edges matching the given filters, tenant-scoped."""
        stmt = select(ConceptEdge).where(
            or_(
                ConceptEdge.tenant_id.is_(None),
                ConceptEdge.tenant_id == tenant_id,
            ),
        )
        if src_type is not None:
            stmt = stmt.where(ConceptEdge.src_type == src_type)
        if src_id is not None:
            stmt = stmt.where(ConceptEdge.src_id == src_id)
        if dst_type is not None:
            stmt = stmt.where(ConceptEdge.dst_type == dst_type)
        if dst_id is not None:
            stmt = stmt.where(ConceptEdge.dst_id == dst_id)
        if relation is not None:
            stmt = stmt.where(ConceptEdge.relation == relation)
        if not include_proposed:
            stmt = stmt.where(ConceptEdge.status == EdgeApprovalStatus.APPROVED)
        stmt = stmt.order_by(ConceptEdge.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_neighbors(
        self,
        concept_id: UUID,
        tenant_id: Optional[UUID],
        relation: Optional[ConceptRelationType] = None,
        include_proposed: bool = False,
    ) -> List[dict]:
        """One-hop neighbor lookup: returns edges + resolved polymorphic endpoints.

        Returns a list of dicts: ``{edge, direction, endpoint}`` where
        ``endpoint`` is a display payload ``{type, id, label, icon, color,
        kind}`` for the node on the other end of the edge — resolved whether
        the endpoint is a concept, an anatomy_structure, a biomarker, an
        examination, or any other registered type. Stale/unknown endpoints
        get a ``"{type}:{id-prefix}"`` fallback label.
        """
        from app.services.concept_endpoint_resolver import resolve_endpoints

        edges = await self.get_edges(
            tenant_id=tenant_id,
            include_proposed=include_proposed,
            limit=500,
        )
        relevant = [
            e
            for e in edges
            if (e.src_type == EdgeEndpointType.CONCEPT and e.src_id == concept_id)
            or (e.dst_type == EdgeEndpointType.CONCEPT and e.dst_id == concept_id)
        ]
        if relation is not None:
            relevant = [e for e in relevant if e.relation == relation]

        # The "other end" of each edge — its type tells us which table to resolve.
        pairs: list[tuple[EdgeEndpointType, UUID]] = []
        for e in relevant:
            if e.src_id == concept_id:
                pairs.append((e.dst_type, e.dst_id))
            else:
                pairs.append((e.src_type, e.src_id))
        resolved = await resolve_endpoints(self.db, pairs) if pairs else {}

        out = []
        for e in relevant:
            other_id = e.dst_id if e.src_id == concept_id else e.src_id
            direction = "outgoing" if e.src_id == concept_id else "incoming"
            out.append(
                {
                    "edge": e,
                    "direction": direction,
                    "endpoint": resolved.get(other_id),
                }
            )
        return out

    async def get_entity_concepts(
        self,
        entity_type: EdgeEndpointType,
        entity_id: UUID,
        tenant_id: Optional[UUID],
        relation: Optional[ConceptRelationType] = None,
    ) -> List[Concept]:
        """Return all concepts linked to a domain entity (e.g. biomarker panels).

        ``entity_type`` is the polymorphic tag (``BIOMARKER``, ``DOCTOR``, …);
        ``entity_id`` is the row's UUID. Typical use: "what panels is LDL in?"
        → ``get_entity_concepts(BIOMARKER, ldl_id, tid, MEMBER_OF)``.
        """
        edges = await self.get_edges(
            tenant_id=tenant_id,
            src_type=entity_type,
            src_id=entity_id,
            relation=relation,
        )
        concept_ids = [
            e.dst_id for e in edges if e.dst_type == EdgeEndpointType.CONCEPT
        ]
        if not concept_ids:
            return []
        stmt = select(Concept).where(
            Concept.id.in_(concept_ids),
            Concept.deleted_at.is_(None),
            Concept.status == ConceptStatus.ACTIVE,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Edge writes
    # ------------------------------------------------------------------

    async def create_edge(
        self,
        *,
        src_type: EdgeEndpointType,
        src_id: UUID,
        dst_type: EdgeEndpointType,
        dst_id: UUID,
        relation: ConceptRelationType,
        tenant_id: Optional[UUID],
        role: str,
        properties: Optional[dict] = None,
        evidence: Optional[dict] = None,
        source: ConceptProvenance = ConceptProvenance.MANUAL,
        status: EdgeApprovalStatus = EdgeApprovalStatus.APPROVED,
        created_by: Optional[UUID] = None,
    ) -> ConceptEdge:
        """Create a typed edge. Validates concept endpoints exist.

        AI-proposed edges land with ``source=AI, status=PROPOSED`` and do not
        count for graph queries until a human approves them.
        """
        self._check_write_role(role)
        if tenant_id is None and role != Role.SYSTEM_ADMIN.value:
            raise PermissionError("Only SYSTEM_ADMIN can create global edges")

        if src_type == EdgeEndpointType.CONCEPT:
            await self._require_concept(src_id, tenant_id)
        if dst_type == EdgeEndpointType.CONCEPT:
            await self._require_concept(dst_id, tenant_id)

        edge = ConceptEdge(
            src_type=src_type,
            src_id=src_id,
            dst_type=dst_type,
            dst_id=dst_id,
            relation=relation,
            tenant_id=tenant_id,
            properties=properties,
            evidence=evidence,
            source=source,
            status=status,
            created_by=created_by,
        )
        self.db.add(edge)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ValueError(
                f"Edge already exists: {src_type.value}:{src_id} "
                f"-[{relation.value}]-> {dst_type.value}:{dst_id}"
            ) from exc
        return edge

    async def delete_edge(
        self, edge_id: UUID, tenant_id: Optional[UUID], role: str
    ) -> None:
        """Hard-delete an edge (edges are cheap to recreate; no soft-delete)."""
        self._check_write_role(role)
        stmt = select(ConceptEdge).where(ConceptEdge.id == edge_id)
        edge = (await self.db.execute(stmt)).scalar_one_or_none()
        if edge is None:
            raise ValueError("Edge not found")
        if edge.tenant_id is None:
            if role != Role.SYSTEM_ADMIN.value:
                raise PermissionError("Only SYSTEM_ADMIN can delete global edges")
        elif role == Role.USER.value:
            raise PermissionError("USER role cannot delete edges")
        await self.db.delete(edge)
        await self.db.flush()

    async def approve_edge(self, edge_id: UUID, role: str) -> ConceptEdge:
        """Approve a proposed edge (SYSTEM_ADMIN / ADMIN only)."""
        self._check_write_role(role)
        stmt = select(ConceptEdge).where(ConceptEdge.id == edge_id)
        edge = (await self.db.execute(stmt)).scalar_one_or_none()
        if edge is None:
            raise ValueError("Edge not found")
        edge.status = EdgeApprovalStatus.APPROVED
        await self.db.flush()
        return edge

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_write_role(self, role: str) -> None:
        if role == Role.USER.value:
            raise PermissionError("USER role cannot modify the taxonomy")

    async def _require_concept(
        self, concept_id: UUID, tenant_id: Optional[UUID]
    ) -> None:
        """Assert that a concept exists and is visible to the caller."""
        c = await self.get_concept(concept_id, tenant_id)
        if c is None:
            raise ValueError(f"Concept {concept_id} not found")

    async def _count_active_edges_for_concept(
        self, concept_id: UUID, tenant_id: Optional[UUID]
    ) -> int:
        """Count approved edges touching this concept (either direction)."""
        stmt = (
            select(func.count())
            .select_from(ConceptEdge)
            .where(
                ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                or_(
                    ConceptEdge.tenant_id.is_(None),
                    ConceptEdge.tenant_id == tenant_id,
                ),
                or_(
                    (ConceptEdge.src_type == EdgeEndpointType.CONCEPT)
                    & (ConceptEdge.src_id == concept_id),
                    (ConceptEdge.dst_type == EdgeEndpointType.CONCEPT)
                    & (ConceptEdge.dst_id == concept_id),
                ),
            )
        )
        return await self.db.scalar(stmt) or 0
