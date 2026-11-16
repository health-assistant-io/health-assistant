"""Catalog service adapters — the descriptor-facing read + write surface.

Each adapter wraps a catalog's existing storage and exposes the uniform
:class:`~app.catalogs.protocol.CatalogServiceProtocol` (CRUD + search,
returning JSON-serializable dicts). Reads tenant-scope via the standard
``or_(tenant_id == caller, tenant_id IS NULL)`` idiom.

**Writes delegate to the unified write contract**
(:mod:`app.services.catalog.catalog_write_service`) — the adapters carry no
write business logic of their own. The generic ``/catalogs/{type}`` routes
validate the payload against the descriptor's typed schema (``extra="forbid"``
→ 422) and call the adapter, which forwards to the same service function the
domain routes call. ``_writable``-as-validation is gone: unknown or read-only
fields are rejected by the typed schemas, never silently dropped.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs.policy import DEFAULT_CATALOG_POLICY, CatalogAccessPolicy
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept, ConceptKindTag
from app.models.enums import ConceptKind
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.vaccine import VaccineCatalog


def _w():
    """The unified write contract (lazy — importing it at module level would
    cycle through ``app.catalogs.__init__`` → registrations → adapters while
    sibling services import the policy sub-module)."""
    from app.services.catalog import catalog_write_service

    return catalog_write_service


class BaseCatalogAdapter:
    """Shared list/get/search + write-delegation logic.

    Subclasses set the class attributes (``model``, ``search_columns``,
    ``order_by``, ``soft_delete``) and may override :meth:`serialize`. Writes
    forward to the per-type functions in
    :mod:`app.services.catalog.catalog_write_service`; delete/promote use the
    shared generic helpers there.
    """

    model: type
    search_columns: tuple[str, ...] = ()
    order_by: tuple[str, ...] = ("name",)
    soft_delete: bool = False
    policy: CatalogAccessPolicy = DEFAULT_CATALOG_POLICY
    # Column whose value becomes the hit ``label`` in search results.
    label_column: str = "name"
    # The registry type key ("biomarker", "medication", ...) — stamped onto each
    # adapter by ``registrations.py`` so audit rows record the catalog type.
    catalog_type: str = ""
    # The FK column linking items to their taxonomy ``class_concept`` (e.g.
    # ``class_concept_id``). Stamped by ``registrations.py`` from the
    # descriptor's ``ConceptLink``. Drives the generic ``?class=<slug>`` filter.
    concept_link_column: str | None = None

    # --- reads -------------------------------------------------------------

    async def list(
        self,
        db: AsyncSession,
        tenant_id: UUID | None,
        *,
        search: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        concept_class: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        stmt = self._base_stmt()
        stmt = self._apply_tenant(stmt, tenant_id)
        if self.soft_delete:
            stmt = stmt.where(self.model.deleted_at.is_(None))
        if scope:
            stmt = self._apply_scope(stmt, scope)
        if search:
            stmt = self._apply_search(stmt, search)
        if kind:
            stmt = self._apply_kind(stmt, kind)
        if concept_class and self.concept_link_column:
            concept_ids = await self._resolve_class_concept_ids(db, concept_class)
            if not concept_ids:
                # Unknown class slug → no matches.
                return {"items": [], "total": 0}
            col = getattr(self.model, self.concept_link_column)
            stmt = stmt.where(col.in_(concept_ids))
        total = await self._count(db, stmt)
        stmt = self._apply_order(stmt).offset(offset).limit(limit)
        result = await db.execute(stmt)
        items = [self.serialize(row, viewer_tenant_id=tenant_id) for row in result.scalars().all()]
        return {"items": items, "total": total}

    async def get(
        self,
        db: AsyncSession,
        tenant_id: UUID | None,
        item_id: UUID,
    ) -> dict[str, Any] | None:
        obj = await self._load(db, item_id, tenant_id)
        return self.serialize(obj, viewer_tenant_id=tenant_id) if obj else None

    # --- search ------------------------------------------------------------

    async def search(
        self,
        db: AsyncSession,
        tenant_id: UUID | None,
        q: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Hybrid (trigram + FTS + RRF) search via the unified backend.

        Delegates to :func:`catalog_search_service._hybrid_search_one` using
        the per-catalog spec keyed by ``self.catalog_type``. Returns uniform
        ``[{"id", "label"}, ...]`` hits (the dispatcher tags them with the
        catalog ``type`` and the LLM tools enrich them further). Empty /
        too-short queries return ``[]``.

        Catalogs without a registered spec (none today) fall back to the
        plain trigram-only path.
        """
        from app.services.catalog.catalog_search_service import (
            _hybrid_search_one,
            _specs_by_type,
        )

        spec = _specs_by_type().get(self.catalog_type)
        if spec is not None:
            hits = await _hybrid_search_one(db, spec, q, tenant_id, limit=limit)
            return [{"id": str(h.row_id), "label": h.label} for h in hits]

        # Legacy fallback (trigram + ilike over search_columns).
        from app.services.catalog.catalog_search_service import (
            DEFAULT_THRESHOLD,
            _normalize,
            _set_similarity_threshold,
        )

        norm = _normalize(q)
        if norm is None:
            return []
        await _set_similarity_threshold(db, DEFAULT_THRESHOLD)
        stmt = self._base_stmt()
        stmt = self._apply_tenant(stmt, tenant_id)
        if self.soft_delete:
            stmt = stmt.where(self.model.deleted_at.is_(None))
        cols = [getattr(self.model, c) for c in self.search_columns]
        stmt = (
            stmt.where(
                or_(
                    *[c.op("%")(norm) for c in cols],
                    *[c.ilike(f"%{norm}%") for c in cols],
                )
            )
            .order_by(func.similarity(getattr(self.model, self.label_column), norm).desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [{"id": str(r.id), "label": getattr(r, self.label_column)} for r in rows]

    # --- writes (delegate to the unified write contract) --------------------

    async def create(self, db: AsyncSession, actor: Any, data: Any) -> dict[str, Any]:
        raise NotImplementedError("adapters must bind a typed create service")

    async def update(
        self, db: AsyncSession, actor: Any, item_id: UUID, data: Any
    ) -> dict[str, Any] | None:
        raise NotImplementedError("adapters must bind a typed update service")

    async def delete(self, db: AsyncSession, actor: Any, item_id: UUID) -> bool:
        return await _w().delete_catalog_item(
            db,
            actor,
            item_id,
            model=self.model,
            catalog_type=self.catalog_type,
            soft_delete=self.soft_delete,
        )

    async def promote_scope(
        self,
        db: AsyncSession,
        actor: Any,
        item_id: UUID,
        target_scope: str,
    ) -> dict[str, Any] | None:
        """Transition a catalog item's scope (plan §1.3) via the shared
        generic helper. Role-gated: user↔tenant requires ADMIN/MANAGER; any
        transition involving system requires SYSTEM_ADMIN. Raises
        :class:`~app.catalogs.policy.CatalogConflict` (→ 409) if another row
        already owns the same slug at the target scope tier."""
        return await _w().promote_catalog_item(
            db,
            actor,
            item_id,
            target_scope,
            model=self.model,
            catalog_type=self.catalog_type,
            serialize=self._serialize_after_write,
            soft_delete=self.soft_delete,
        )

    def _serialize_after_write(
        self, db: AsyncSession, obj: Any, *, viewer_tenant_id: UUID | None = None
    ) -> dict[str, Any]:
        """Serialize a freshly-committed row (override when the shape needs
        post-commit relationship loading)."""
        return self.serialize(obj, viewer_tenant_id=viewer_tenant_id)

    # --- helpers -----------------------------------------------------------

    def _base_stmt(self):
        return select(self.model)

    def _apply_tenant(self, stmt, tenant_id: UUID | None):
        col = self.model.tenant_id
        return stmt.where(or_(col.is_(None), col == tenant_id))

    def _apply_scope(self, stmt, scope: str):
        col = self.model.scope
        return stmt.where(col == scope)

    def _apply_search(self, stmt, q: str):
        term = f"%{q.strip()}%"
        return stmt.where(or_(*[getattr(self.model, c).ilike(term) for c in self.search_columns]))

    def _apply_kind(self, stmt, kind: str):
        """Filter by a domain ``kind``. Default no-op; overridden by catalogs
        that have a meaningful kind (e.g. ``ConceptCatalogAdapter`` →
        ``primary_kind``). Accepts comma-separated values."""
        return stmt

    async def _resolve_class_concept_ids(self, db: AsyncSession, concept_class: str) -> list:
        """Resolve one or more taxonomy-class concept slugs (comma-separated)
        to their concept ids. Generic over the catalog: works for any catalog
        whose items carry a ``class_concept_id`` FK (anatomy, biomarker,
        medication, allergy, vaccine)."""
        from app.models.concept_model import Concept as _Concept

        slugs = [s.strip().lower() for s in concept_class.split(",") if s.strip()]
        if not slugs:
            return []
        res = await db.execute(select(_Concept.id).where(_Concept.slug.in_(slugs)))
        return [row[0] for row in res.all()]

    def _apply_order(self, stmt):
        return stmt.order_by(*[getattr(self.model, c).asc() for c in self.order_by])

    async def _load(self, db: AsyncSession, item_id: UUID, tenant_id: UUID | None):
        stmt = self._base_stmt().where(self.model.id == item_id)
        stmt = self._apply_tenant(stmt, tenant_id)
        if self.soft_delete:
            stmt = stmt.where(self.model.deleted_at.is_(None))
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _count(self, db: AsyncSession, stmt) -> int:
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        result = await db.execute(count_stmt)
        return result.scalar() or 0

    def serialize(self, obj, *, viewer_tenant_id: UUID | None = None) -> dict[str, Any]:
        """Serialized row. The base reads go through the write contract's
        ``serialize_catalog_row`` so the class-concept display projection
        (``class_concept_slug`` / ``class_concept_name``) is tenant-aware for
        every catalog that carries the FK."""
        return _w().serialize_catalog_row(obj, viewer_tenant_id)


class MedicationCatalogAdapter(BaseCatalogAdapter):
    model = MedicationCatalog
    search_columns = ("name", "description", "indications")
    order_by = ("name",)

    async def create(self, db: AsyncSession, actor: Any, data) -> dict[str, Any]:
        return await _w().create_medication(db, actor, data)

    async def update(self, db: AsyncSession, actor: Any, item_id: UUID, data) -> dict | None:
        return await _w().update_medication(db, actor, item_id, data)


class VaccineCatalogAdapter(BaseCatalogAdapter):
    """Phase 5 — mirrors MedicationCatalogAdapter. ``VaccineCatalog`` has both
    ``to_dict()`` and ``to_fhir_dict()`` (→ Medication), so it uses the base
    CRUD + search + FHIR-gate paths unchanged."""

    model = VaccineCatalog
    search_columns = ("name", "description", "code")
    order_by = ("name",)

    async def create(self, db: AsyncSession, actor: Any, data) -> dict[str, Any]:
        return await _w().create_vaccine(db, actor, data)

    async def update(self, db: AsyncSession, actor: Any, item_id: UUID, data) -> dict | None:
        return await _w().update_vaccine(db, actor, item_id, data)


class AllergyCatalogAdapter(BaseCatalogAdapter):
    model = AllergyCatalog
    search_columns = ("name", "description")
    order_by = ("name",)

    async def create(self, db: AsyncSession, actor: Any, data) -> dict[str, Any]:
        return await _w().create_allergy(db, actor, data)

    async def update(self, db: AsyncSession, actor: Any, item_id: UUID, data) -> dict | None:
        return await _w().update_allergy(db, actor, item_id, data)


class AnatomyCatalogAdapter(BaseCatalogAdapter):
    model = AnatomyStructure
    search_columns = ("name", "slug", "standard_code", "description")
    order_by = ("name",)

    async def create(self, db: AsyncSession, actor: Any, data) -> dict[str, Any]:
        return await _w().create_anatomy(db, actor, data)

    async def update(self, db: AsyncSession, actor: Any, item_id: UUID, data) -> dict | None:
        return await _w().update_anatomy(db, actor, item_id, data)


class ConceptCatalogAdapter(BaseCatalogAdapter):
    """Read-mostly adapter for the taxonomy's ``Concept`` nodes.

    Reads flow through the generic list/get/search machinery (kind filtering
    joins ``concept_kind_tags`` so a multi-kind concept appears under every
    domain it belongs to, not just its ``primary_kind``). Writes dispatch to
    :class:`~app.services.catalog.concept_service.ConceptService` — the single
    taxonomy write authority (kind-sync, retire-on-edges, curated-taxonomy
    RBAC, audit) — so the meta-layer and ``/concepts`` behave identically.
    ``promote_scope`` is not a concept operation (scope is derived from
    ``tenant_id`` at create; there is no tier transition) — it stays 405 via
    the descriptor's ``supports_promote=False``.
    """

    model = Concept
    search_columns = ("name", "slug", "description")
    order_by = ("display_order", "name")
    soft_delete = True

    def _apply_kind(self, stmt, kind: str):
        # Multi-kind filter via the tag join. A concept carrying several kind
        # tags must match under *each* of its domains, not only its
        # ``primary_kind`` (the denormalized column only mirrors one tag).
        # Comma-separated values are accepted.
        kinds = [k.strip().lower() for k in kind.split(",") if k.strip()]
        if not kinds:
            return stmt
        try:
            resolved = [ConceptKind(k) for k in kinds]
        except ValueError:
            return stmt.where(False)
        return (
            stmt.join(ConceptKindTag, ConceptKindTag.concept_id == Concept.id)
            .where(ConceptKindTag.kind.in_(resolved))
            .distinct()
        )

    async def list(
        self,
        db: AsyncSession,
        tenant_id: UUID | None,
        *,
        search: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        concept_class: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        out = await super().list(
            db,
            tenant_id,
            search=search,
            kind=kind,
            scope=scope,
            concept_class=concept_class,
            limit=limit,
            offset=offset,
        )
        # Batch-resolve ``parent_slug`` for the whole page so the parent picker
        # can render by slug without an N+1 (the adapter is a singleton, so we
        # must not cache on ``self`` — mutate the serialized dicts directly).
        await self._attach_parent_slugs(db, out["items"], tenant_id)
        return out

    async def get(
        self,
        db: AsyncSession,
        tenant_id: UUID | None,
        item_id: UUID,
    ) -> dict[str, Any] | None:
        d = await super().get(db, tenant_id, item_id)
        if d:
            await self._attach_parent_slugs(db, [d], tenant_id)
        return d

    async def create(self, db: AsyncSession, actor: Any, data) -> dict[str, Any]:
        return await _w().create_concept(db, actor, data)

    async def update(self, db: AsyncSession, actor: Any, item_id: UUID, data) -> dict | None:
        return await _w().update_concept(db, actor, item_id, data)

    async def delete(self, db: AsyncSession, actor: Any, item_id: UUID) -> bool:
        # Retire-or-soft-delete via ConceptService — never a hard delete (the
        # polymorphic graph must not dangle).
        return await _w().delete_concept(db, actor, item_id)

    async def _attach_parent_slugs(
        self, db: AsyncSession, items: list[dict[str, Any]], tenant_id: UUID | None
    ) -> None:
        """Resolve ``parent_id`` → ``parent_slug`` for a batch of serialized
        concept dicts (one indexed query, not one per row). The lookup follows
        the read visibility rule — a parent hidden from the caller (another
        tenant's private row or soft-deleted) projects to ``None`` instead of
        echoing its slug."""
        parent_ids = {UUID(it["parent_id"]) for it in items if it.get("parent_id")}
        if not parent_ids:
            return
        rows = (
            await db.execute(
                select(Concept.id, Concept.slug).where(
                    Concept.id.in_(parent_ids),
                    or_(Concept.tenant_id.is_(None), Concept.tenant_id == tenant_id),
                    Concept.deleted_at.is_(None),
                )
            )
        ).all()
        slug_map = {str(pid): slug for pid, slug in rows}
        for it in items:
            pid = it.get("parent_id")
            if pid:
                it["parent_slug"] = slug_map.get(pid)

    def serialize(self, obj, *, viewer_tenant_id: UUID | None = None) -> dict[str, Any]:
        # ``Concept.to_dict()`` emits every field the form + Info tab need
        # (incl. ``kinds``, ``scope``). ``parent_slug`` is attached after the
        # fact by ``list``/``get`` (batch-resolved).
        return obj.to_dict()


class BiomarkerCatalogAdapter(BaseCatalogAdapter):
    """Specialised reads: biomarkers serialize through the unified write
    contract's serializer (unit symbol, class concept, reference ranges,
    allowed states, scope/tenant/creator). Writes delegate to
    :mod:`app.services.catalog.catalog_write_service` — the same functions the
    ``/biomarkers`` domain routes call. No FHIR gate (biomarker definitions
    are not FHIR resources; their values surface on FHIR ``Observation``).
    """

    model = BiomarkerDefinition
    policy: CatalogAccessPolicy = DEFAULT_CATALOG_POLICY

    # --- reads -------------------------------------------------------------

    async def list(
        self,
        db: AsyncSession,
        tenant_id: UUID | None,
        *,
        search: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        concept_class: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        filters = self._filters(tenant_id, search, scope=scope)
        if concept_class:
            ids = await self._resolve_class_concept_ids(db, concept_class)
            if not ids:
                return {"items": [], "total": 0}
            filters.append(BiomarkerDefinition.class_concept_id.in_(ids))
        count_stmt = select(func.count()).select_from(
            select(BiomarkerDefinition).where(*filters).subquery()
        )
        total = (await db.execute(count_stmt)).scalar() or 0

        stmt = (
            select(BiomarkerDefinition)
            .where(*filters)
            .order_by(BiomarkerDefinition.name.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()
        items = [_w().serialize_biomarker(bio, viewer_tenant_id=tenant_id) for bio in rows]
        return {"items": items, "total": total}

    async def get(
        self,
        db: AsyncSession,
        tenant_id: UUID | None,
        item_id: UUID,
    ) -> dict[str, Any] | None:
        bio = await self._load(db, item_id, tenant_id)
        if bio is None:
            return None
        return _w().serialize_biomarker(bio, viewer_tenant_id=tenant_id)

    # --- search ------------------------------------------------------------

    async def search(
        self,
        db: AsyncSession,
        tenant_id: UUID | None,
        q: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Hybrid (trigram + FTS + alias + RRF) search via the unified backend.

        Delegates to :func:`catalog_search_service._hybrid_search_one` using
        the biomarker spec — searches name/slug/code via trigram + description
        /info/aliases via FTS, so "TSH" matches the alias of "Thyroid
        Stimulating Hormone".
        """
        from app.services.catalog.catalog_search_service import (
            _hybrid_search_one,
            _specs_by_type,
        )

        spec = _specs_by_type()["biomarker"]
        hits = await _hybrid_search_one(db, spec, q, tenant_id, limit=limit)
        return [{"id": str(h.row_id), "label": h.label} for h in hits]

    # --- writes (delegate to the unified write contract) --------------------

    async def create(self, db: AsyncSession, actor: Any, data) -> dict[str, Any]:
        return await _w().create_biomarker(db, actor, data)

    async def update(self, db: AsyncSession, actor: Any, item_id: UUID, data) -> dict | None:
        return await _w().update_biomarker(db, actor, item_id, data)

    async def _serialize_after_write(
        self, db: AsyncSession, obj: Any, *, viewer_tenant_id: UUID | None = None
    ) -> dict[str, Any]:
        # After a commit+refresh the selectin relationships may be stale —
        # reload eager-loaded before serializing (async lazy loads raise).
        bio = await _w().reload_biomarker(db, obj.id)
        return _w().serialize_biomarker(bio, viewer_tenant_id=viewer_tenant_id)

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _filters(
        tenant_id: UUID | None,
        search: str | None,
        *,
        scope: str | None = None,
    ):
        col = BiomarkerDefinition.tenant_id
        flt = [or_(col.is_(None), col == tenant_id)]
        if scope:
            flt.append(BiomarkerDefinition.scope == scope)
        if search:
            term = f"%{search.strip()}%"
            flt.append(
                or_(
                    BiomarkerDefinition.name.ilike(term),
                    BiomarkerDefinition.slug.ilike(term),
                    BiomarkerDefinition.code.ilike(term),
                )
            )
        return flt

    async def _load(self, db: AsyncSession, item_id: UUID, tenant_id: UUID | None):
        stmt = select(BiomarkerDefinition).where(
            BiomarkerDefinition.id == item_id, *self._filters(tenant_id, None)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    def serialize(self, obj, *, viewer_tenant_id: UUID | None = None) -> dict[str, Any]:
        return _w().serialize_biomarker(obj, viewer_tenant_id=viewer_tenant_id)
