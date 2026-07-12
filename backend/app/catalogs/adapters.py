"""Catalog service adapters.

Each adapter wraps a catalog's existing storage and exposes the uniform
:class:`~app.catalogs.protocol.CatalogServiceProtocol` (CRUD + search, returning
JSON-serializable dicts). Reads tenant-scope via the standard
``or_(tenant_id == caller, tenant_id IS NULL)`` idiom — including biomarkers,
whose legacy domain endpoint leaked across tenants (that domain endpoint is
fixed in Phase 1; the adapter did the right thing from the start).

Write methods enforce RBAC via the descriptor's
:class:`~app.catalogs.policy.CatalogAccessPolicy` (``self.policy``): they raise
:class:`~app.catalogs.policy.CatalogPermissionDenied` (mapped to HTTP 403 by the
global handler in ``main.py``). Creates are tenant-scoped (ADMIN/MANAGER+);
updates/deletes of global rows (``tenant_id IS NULL``) require SYSTEM_ADMIN.
Models with a ``to_fhir_dict()`` additionally pass the write-time FHIR gate
(:func:`~app.services.fhir_helpers.assert_valid_fhir`) before commit.

The shared query/tenant/count/pagination/CRUD logic lives in
:class:`BaseCatalogAdapter`; subclasses declare the model, search columns,
ordering, soft-delete flag, and serialization hook. :class:`BiomarkerCatalogAdapter`
is specialised because it joins ``Unit`` for the ``preferred_unit_symbol``
joined field and ``BiomarkerDefinition`` has no ``to_dict()`` / ``to_fhir_dict()``.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs.policy import DEFAULT_CATALOG_POLICY, CatalogAccessPolicy
from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.concept_model import Concept, ConceptKindTag
from app.models.enums import ConceptKind
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.anatomy_model import AnatomyStructure
from app.services.fhir_helpers import assert_valid_fhir

# Columns that must never be set directly from a write payload (mass-assignment
# guard). ``tenant_id`` is set explicitly by the adapter from the actor token.
_READONLY_FIELDS = frozenset(
    {
        "id",
        "tenant_id",
        "created_at",
        "updated_at",
        "deleted_at",
        "created_by",
        "updated_by",
        "version",
        "is_current",
    }
)


class BaseCatalogAdapter:
    """Shared list/get/create/update/delete logic.

    Subclasses set the class attributes (``model``, ``search_columns``,
    ``order_by``, ``soft_delete``) and may override :meth:`serialize`.
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
    concept_link_column: Optional[str] = None

    # --- audit -------------------------------------------------------------

    async def _audit(
        self,
        db: AsyncSession,
        actor: Any,
        operation: str,
        obj: Any,
        *,
        from_scope: Optional[str] = None,
        to_scope: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        """Best-effort audit record after a successful write.

        Never raises — the catalog change is already durable by the time this
        runs, so an audit failure must not abort the response serialization.
        """
        import logging as _logging

        from app.services.catalog_audit_service import record_from_obj

        try:
            await record_from_obj(
                db,
                actor=actor,
                catalog_type=self.catalog_type,
                obj=obj,
                operation=operation,
                from_scope=from_scope,
                to_scope=to_scope,
                details=details,
            )
        except Exception:
            _logging.getLogger(__name__).warning(
                "catalog audit record failed (type=%s op=%s)",
                self.catalog_type,
                operation,
                exc_info=True,
            )

    async def _audit_snapshot(
        self,
        db: AsyncSession,
        actor: Any,
        operation: str,
        item_id: UUID,
        item_name: str,
    ) -> None:
        """Best-effort audit record for a deleted item (obj no longer loaded)."""
        import logging as _logging

        from app.services.catalog_audit_service import record

        try:
            await record(
                db,
                actor=actor,
                catalog_type=self.catalog_type,
                item_id=item_id,
                item_name=item_name,
                operation=operation,
            )
        except Exception:
            _logging.getLogger(__name__).warning(
                "catalog audit record failed (type=%s op=%s item=%s)",
                self.catalog_type,
                operation,
                item_id,
                exc_info=True,
            )

    @staticmethod
    def _label(obj: Any) -> str:
        for attr in ("name", "slug", "code"):
            val = getattr(obj, attr, None)
            if val:
                return str(val)
        return ""

    # --- reads -------------------------------------------------------------

    async def list(
        self,
        db: AsyncSession,
        tenant_id: Optional[UUID],
        *,
        search: Optional[str] = None,
        kind: Optional[str] = None,
        scope: Optional[str] = None,
        concept_class: Optional[str] = None,
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
        items = [self.serialize(row) for row in result.scalars().all()]
        return {"items": items, "total": total}

    async def get(
        self,
        db: AsyncSession,
        tenant_id: Optional[UUID],
        item_id: UUID,
    ) -> Optional[dict[str, Any]]:
        obj = await self._load(db, item_id, tenant_id)
        return self.serialize(obj) if obj else None

    # --- search ------------------------------------------------------------

    async def search(
        self,
        db: AsyncSession,
        tenant_id: Optional[UUID],
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
        legacy trigram-only path.
        """
        from app.services.catalog_search_service import (
            _hybrid_search_one,
            _specs_by_type,
        )

        spec = _specs_by_type().get(self.catalog_type)
        if spec is not None:
            hits = await _hybrid_search_one(db, spec, q, tenant_id, limit=limit)
            return [{"id": str(h.row_id), "label": h.label} for h in hits]

        # Legacy fallback (trigram + ilike over search_columns).
        from app.services.catalog_search_service import (
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
            .order_by(
                func.similarity(getattr(self.model, self.label_column), norm).desc()
            )
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [{"id": str(r.id), "label": getattr(r, self.label_column)} for r in rows]

    # --- writes ------------------------------------------------------------

    async def create(
        self,
        db: AsyncSession,
        actor: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        fields = self._writable(payload)
        obj = self.model(**fields)
        self.policy.assign_create_scope(actor.role, obj, actor.tenant_id, actor.user_id)
        self._validate_fhir(obj)
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        await self._audit(db, actor, "create", obj)
        return self.serialize(obj)

    async def update(
        self,
        db: AsyncSession,
        actor: Any,
        item_id: UUID,
        payload: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        obj = await self._load(db, item_id, actor.tenant_id)
        if obj is None:
            return None
        self.policy.check_modify(
            actor.role,
            obj.scope,
            item_created_by=obj.created_by,
            actor_user_id=actor.user_id,
        )
        for key, value in self._writable(payload).items():
            setattr(obj, key, value)
        self._validate_fhir(obj)
        await db.commit()
        await db.refresh(obj)
        await self._audit(db, actor, "update", obj)
        return self.serialize(obj)

    async def delete(self, db: AsyncSession, actor: Any, item_id: UUID) -> bool:
        obj = await self._load(db, item_id, actor.tenant_id)
        if obj is None:
            return False
        self.policy.check_modify(
            actor.role,
            obj.scope,
            item_created_by=obj.created_by,
            actor_user_id=actor.user_id,
        )
        # Snapshot the item before deletion so the audit trail survives.
        snapshot_id, snapshot_name = obj.id, self._label(obj)
        await db.delete(obj)
        await db.commit()
        await self._audit_snapshot(db, actor, "delete", snapshot_id, snapshot_name)
        return True

    async def promote_scope(
        self,
        db: AsyncSession,
        actor: Any,
        item_id: UUID,
        target_scope: str,
    ) -> Optional[dict[str, Any]]:
        """Transition a catalog item's scope (plan §1.3).

        Role-gated: user↔tenant requires ADMIN/MANAGER; any transition
        involving system requires SYSTEM_ADMIN. On promote-to-system the
        ``tenant_id`` is cleared (canonical); on demote-to-tenant it is set to
        the actor's tenant.
        """
        from app.models.enums import CatalogScope

        obj = await self._load(db, item_id, actor.tenant_id)
        if obj is None:
            return None
        target = CatalogScope(target_scope)
        from_scope = obj.scope.value if obj.scope is not None else None
        self.policy.check_promote(actor.role, obj.scope, target)
        # Keep tenant_id consistent with the new scope.
        if target is CatalogScope.SYSTEM:
            obj.tenant_id = None
        elif target is CatalogScope.TENANT and obj.tenant_id is None:
            obj.tenant_id = actor.tenant_id
        obj.scope = target
        await db.commit()
        await db.refresh(obj)
        await self._audit(
            db,
            actor,
            "promote",
            obj,
            from_scope=from_scope,
            to_scope=target.value,
        )
        return self.serialize(obj)

    # --- helpers -----------------------------------------------------------

    def _base_stmt(self):
        return select(self.model)

    def _apply_tenant(self, stmt, tenant_id: Optional[UUID]):
        col = self.model.tenant_id
        return stmt.where(or_(col.is_(None), col == tenant_id))

    def _apply_scope(self, stmt, scope: str):
        col = self.model.scope
        return stmt.where(col == scope)

    def _apply_search(self, stmt, q: str):
        term = f"%{q.strip()}%"
        return stmt.where(
            or_(*[getattr(self.model, c).ilike(term) for c in self.search_columns])
        )

    def _apply_kind(self, stmt, kind: str):
        """Filter by a domain ``kind``. Default no-op; overridden by catalogs
        that have a meaningful kind (e.g. ``ConceptCatalogAdapter`` →
        ``primary_kind``). Accepts comma-separated values."""
        return stmt

    async def _resolve_class_concept_ids(
        self, db: AsyncSession, concept_class: str
    ) -> list:
        """Resolve one or more taxonomy-class concept slugs (comma-separated)
        to their concept ids. Generic over the catalog: works for any catalog
        whose items carry a ``class_concept_id`` FK (anatomy, biomarker,
        medication, allergy, vaccine)."""
        slugs = [s.strip().lower() for s in concept_class.split(",") if s.strip()]
        if not slugs:
            return []
        res = await db.execute(select(Concept.id).where(Concept.slug.in_(slugs)))
        return [row[0] for row in res.all()]

    def _apply_order(self, stmt):
        return stmt.order_by(*[getattr(self.model, c).asc() for c in self.order_by])

    async def _load(self, db: AsyncSession, item_id: UUID, tenant_id: Optional[UUID]):
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

    def _writable(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Restrict to real mapped columns. ``hasattr`` alone is too permissive:
        # it admits computed ``@property`` fields (e.g. ``MedicationCatalog.is_custom``,
        # derived from ``tenant_id``) and relationships, which have no setter and
        # crash ``setattr`` on update ("property 'is_custom' has no setter").
        from sqlalchemy import inspect as _sa_inspect

        column_keys = set(_sa_inspect(self.model).columns.keys())
        return {
            k: v
            for k, v in payload.items()
            if k in column_keys and k not in _READONLY_FIELDS
        }

    def _validate_fhir(self, obj) -> None:
        if hasattr(obj, "to_fhir_dict"):
            assert_valid_fhir(obj)

    def serialize(self, obj) -> dict[str, Any]:
        return obj.to_dict()


class MedicationCatalogAdapter(BaseCatalogAdapter):
    model = MedicationCatalog
    search_columns = ("name", "description", "indications")
    order_by = ("name",)


class VaccineCatalogAdapter(BaseCatalogAdapter):
    """Phase 5 — mirrors MedicationCatalogAdapter. ``VaccineCatalog`` has both
    ``to_dict()`` and ``to_fhir_dict()`` (→ Medication), so it uses the base
    CRUD + search + FHIR-gate paths unchanged."""

    from app.models.fhir.vaccine import VaccineCatalog as _VC

    model = _VC
    search_columns = ("name", "description", "code")
    order_by = ("name",)


class AllergyCatalogAdapter(BaseCatalogAdapter):
    model = AllergyCatalog
    search_columns = ("name", "description")
    order_by = ("name",)


class AnatomyCatalogAdapter(BaseCatalogAdapter):
    model = AnatomyStructure
    search_columns = ("name", "slug", "standard_code", "description")
    order_by = ("name",)


class ConceptCatalogAdapter(BaseCatalogAdapter):
    """Read-only adapter for the taxonomy's ``Concept`` nodes.

    Per the taxonomy/catalog merge plan (AD-1 r2), this adapter is **read-only**:
    it implements only ``list`` / ``get`` / ``search`` / ``_apply_kind`` /
    ``serialize`` from :class:`BaseCatalogAdapter`. Concept **writes** never go
    through the catalog meta-layer — the frontend dispatches them to the
    ``/concepts`` REST surface, and the generic catalog write endpoints return
    ``405`` for ``type == "concept"`` (plan §4.1/Phase 1b). All write logic
    (kind-sync, retire-on-edges, RBAC, audit) lives in :class:`ConceptService`,
    the single authority shared by REST, AI tools, and seeds.

    Kind filtering joins ``concept_kind_tags`` so a multi-kind concept appears
    under every domain it belongs to (not just its ``primary_kind``).
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
        tenant_id: Optional[UUID],
        *,
        search: Optional[str] = None,
        kind: Optional[str] = None,
        scope: Optional[str] = None,
        concept_class: Optional[str] = None,
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
        await self._attach_parent_slugs(db, out["items"])
        return out

    async def get(
        self,
        db: AsyncSession,
        tenant_id: Optional[UUID],
        item_id: UUID,
    ) -> Optional[dict[str, Any]]:
        d = await super().get(db, tenant_id, item_id)
        if d:
            await self._attach_parent_slugs(db, [d])
        return d

    async def _attach_parent_slugs(
        self, db: AsyncSession, items: list[dict[str, Any]]
    ) -> None:
        """Resolve ``parent_id`` → ``parent_slug`` for a batch of serialized
        concept dicts (one indexed query, not one per row)."""
        parent_ids = {
            UUID(it["parent_id"]) for it in items if it.get("parent_id")
        }
        if not parent_ids:
            return
        rows = (
            await db.execute(
                select(Concept.id, Concept.slug).where(Concept.id.in_(parent_ids))
            )
        ).all()
        slug_map = {str(pid): slug for pid, slug in rows}
        for it in items:
            pid = it.get("parent_id")
            if pid:
                it["parent_slug"] = slug_map.get(pid)

    def serialize(self, obj) -> dict[str, Any]:
        # ``Concept.to_dict()`` emits every field the form + Info tab need
        # (incl. ``kinds``, ``scope``). ``parent_slug`` is attached after the
        # fact by ``list``/``get`` (batch-resolved).
        return obj.to_dict()


class BiomarkerCatalogAdapter(BaseCatalogAdapter):
    """Specialised: joins ``Unit`` for ``preferred_unit_symbol`` and hand-builds
    the dict (``BiomarkerDefinition`` has no ``to_dict()`` / ``to_fhir_dict()``).

    Output keys match the legacy ``GET /biomarkers/`` endpoint so the meta-layer
    is a faithful delegate. No FHIR gate (biomarker definitions are not FHIR
    resources; their values surface on FHIR ``Observation``).

    Inherits the audit helpers (``_audit`` / ``_audit_snapshot`` / ``catalog_type``)
    from :class:`BaseCatalogAdapter` but overrides every CRUD/search method for
    the Unit join.
    """

    policy: CatalogAccessPolicy = DEFAULT_CATALOG_POLICY

    # --- reads -------------------------------------------------------------

    async def list(
        self,
        db: AsyncSession,
        tenant_id: Optional[UUID],
        *,
        search: Optional[str] = None,
        kind: Optional[str] = None,
        scope: Optional[str] = None,
        concept_class: Optional[str] = None,
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
            select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
            .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
            .where(*filters)
            .order_by(BiomarkerDefinition.name.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        items = [self._serialize(bio, sym) for bio, sym in rows]
        return {"items": items, "total": total}

    async def get(
        self,
        db: AsyncSession,
        tenant_id: Optional[UUID],
        item_id: UUID,
    ) -> Optional[dict[str, Any]]:
        stmt = (
            select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
            .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
            .where(BiomarkerDefinition.id == item_id, *self._filters(tenant_id, None))
        )
        row = (await db.execute(stmt)).first()
        if row is None:
            return None
        bio, sym = row
        return self._serialize(bio, sym)

    # --- search ------------------------------------------------------------

    async def search(
        self,
        db: AsyncSession,
        tenant_id: Optional[UUID],
        q: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Hybrid (trigram + FTS + alias + RRF) search via the unified backend.

        Delegates to :func:`catalog_search_service._hybrid_search_one` using
        the biomarker spec — searches name/slug/code via trigram + description
        /info/aliases via FTS, so "TSH" matches the alias of "Thyroid
        Stimulating Hormone".
        """
        from app.services.catalog_search_service import (
            _hybrid_search_one,
            _specs_by_type,
        )

        spec = _specs_by_type()["biomarker"]
        hits = await _hybrid_search_one(db, spec, q, tenant_id, limit=limit)
        return [{"id": str(h.row_id), "label": h.label} for h in hits]

    # --- writes ------------------------------------------------------------

    async def create(
        self,
        db: AsyncSession,
        actor: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        fields = self._writable(payload)
        bio = BiomarkerDefinition(**fields)
        self.policy.assign_create_scope(actor.role, bio, actor.tenant_id, actor.user_id)
        db.add(bio)
        await db.commit()
        await db.refresh(bio)
        await self._audit(db, actor, "create", bio)
        return await self._get_with_symbol(db, bio)

    async def update(
        self,
        db: AsyncSession,
        actor: Any,
        item_id: UUID,
        payload: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        bio = await self._load(db, item_id, actor.tenant_id)
        if bio is None:
            return None
        self.policy.check_modify(
            actor.role,
            bio.scope,
            item_created_by=bio.created_by,
            actor_user_id=actor.user_id,
        )
        for key, value in self._writable(payload).items():
            setattr(bio, key, value)
        await db.commit()
        await db.refresh(bio)
        await self._audit(db, actor, "update", bio)
        return await self._get_with_symbol(db, bio)

    async def delete(self, db: AsyncSession, actor: Any, item_id: UUID) -> bool:
        bio = await self._load(db, item_id, actor.tenant_id)
        if bio is None:
            return False
        self.policy.check_modify(
            actor.role,
            bio.scope,
            item_created_by=bio.created_by,
            actor_user_id=actor.user_id,
        )
        snapshot_id, snapshot_name = bio.id, bio.name or ""
        await db.delete(bio)
        await db.commit()
        await self._audit_snapshot(db, actor, "delete", snapshot_id, snapshot_name)
        return True

    async def promote_scope(
        self,
        db: AsyncSession,
        actor: Any,
        item_id: UUID,
        target_scope: str,
    ) -> Optional[dict[str, Any]]:
        from app.models.enums import CatalogScope

        bio = await self._load(db, item_id, actor.tenant_id)
        if bio is None:
            return None
        target = CatalogScope(target_scope)
        from_scope = bio.scope.value if bio.scope is not None else None
        self.policy.check_promote(actor.role, bio.scope, target)
        if target is CatalogScope.SYSTEM:
            bio.tenant_id = None
        elif target is CatalogScope.TENANT and bio.tenant_id is None:
            bio.tenant_id = actor.tenant_id
        bio.scope = target
        await db.commit()
        await db.refresh(bio)
        await self._audit(
            db,
            actor,
            "promote",
            bio,
            from_scope=from_scope,
            to_scope=target.value,
        )
        return await self._get_with_symbol(db, bio)

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _filters(
        tenant_id: Optional[UUID],
        search: Optional[str],
        *,
        scope: Optional[str] = None,
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

    async def _load(self, db: AsyncSession, item_id: UUID, tenant_id: Optional[UUID]):
        stmt = select(BiomarkerDefinition).where(
            BiomarkerDefinition.id == item_id, *self._filters(tenant_id, None)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def _get_with_symbol(
        self, db: AsyncSession, bio: BiomarkerDefinition
    ) -> dict:
        symbol = None
        if bio.preferred_unit_id:
            symbol = (
                await db.execute(
                    select(Unit.symbol).where(Unit.id == bio.preferred_unit_id)
                )
            ).scalar_one_or_none()
        return self._serialize(bio, symbol)

    @staticmethod
    def _writable(payload: dict[str, Any]) -> dict[str, Any]:
        # Restrict to real mapped columns (matches BaseCatalogAdapter).
        # ``hasattr`` alone is too permissive: it admits computed ``@property``
        # fields like ``category`` (derived from ``class_concept``) and
        # relationships, which have no setter and crash ``setattr`` on update
        # ("property 'category' of 'BiomarkerDefinition' has no setter").
        from sqlalchemy import inspect as _sa_inspect

        column_keys = set(_sa_inspect(BiomarkerDefinition).columns.keys())
        return {
            k: v
            for k, v in payload.items()
            if k in column_keys and k not in _READONLY_FIELDS
        }

    @staticmethod
    def _serialize(
        bio: BiomarkerDefinition, unit_symbol: Optional[str]
    ) -> dict[str, Any]:
        return {
            "id": bio.id,
            "slug": bio.slug,
            "coding_system": bio.coding_system,
            "code": bio.code,
            "name": bio.name,
            "category": bio.category,
            "class_concept_slug": bio.class_concept.slug if bio.class_concept else None,
            "class_concept_name": bio.class_concept.name if bio.class_concept else None,
            "aliases": bio.aliases,
            "preferred_unit_id": bio.preferred_unit_id,
            "info": bio.info,
            "reference_range_min": bio.reference_range_min,
            "reference_range_max": bio.reference_range_max,
            "is_telemetry": bio.is_telemetry,
            "meta_data": bio.meta_data,
            "preferred_unit_symbol": unit_symbol,
            "scope": bio.scope.value if bio.scope else "system",
            "tenant_id": str(bio.tenant_id) if bio.tenant_id else None,
            "created_by": str(bio.created_by) if bio.created_by else None,
        }
