"""Taxonomy + anatomy restore (concepts, edges, anatomy, catalog upserts)."""

import logging

from app.services.imports.types import (
    UUID,
    AllergyCatalog,
    AllergyCategory,
    AnatomyStructure,
    Any,
    CodingSystem,
    Concept,
    ConceptEdge,
    ConceptKind,
    ConceptKindTag,
    ConceptProvenance,
    ConceptRelationType,
    ConceptStatus,
    EdgeApprovalStatus,
    EdgeEndpointType,
    MedicationCatalog,
    _uuid,
    func,
    or_,
    select,
)

logger = logging.getLogger(__name__)


class TaxonomyAnatomyMixin:
    # ---------------- taxonomy + anatomy restore ----------------

    async def _resolve_concept_fk(
        self,
        source_id: Any,
        slug: str | None,
        kind: ConceptKind | None,
        tenant_id: UUID,
        id_remap: dict[str, str],
    ) -> UUID | None:
        """Resolve an exported concept FK to a target-tenant concept id.

        Order: (1) id_remap (a concept just imported via ``_restore_concepts``),
        (2) the source id verbatim if a matching in-tenant row already exists,
        (3) slug lookup against the target's visible taxonomy (covers global/
        seeded concepts we deliberately did not export). Returns ``None`` if
        nothing resolves — callers leave the FK NULL (soft-fail policy)."""
        if not source_id:
            return None
        key = str(source_id)
        if key in id_remap:
            return _uuid(id_remap[key])
        cand = _uuid(key)
        if cand is not None:
            res = await self.db.execute(
                select(Concept.id).where(
                    Concept.id == cand,
                    or_(
                        Concept.tenant_id == tenant_id,
                        Concept.tenant_id.is_(None),
                    ),
                    Concept.deleted_at.is_(None),
                )
            )
            row = res.first()
            if row:
                return row[0]
        if slug:
            from app.services.catalog.concept_service import resolve_concept_by_slug

            return await resolve_concept_by_slug(self.db, slug, kind, tenant_id=tenant_id)
        return None

    async def _restore_concepts(
        self,
        payload: dict[str, Any],
        tenant_id: UUID,
        id_remap: dict[str, str],
    ) -> int:
        """Upsert tenant-scoped concepts and record old→new ids in id_remap.

        Idempotent: upsert by ``(slug, tenant_id)``. Kind tags are reconciled
        additively (missing tags added; existing extras preserved — matches the
        seed behavior). ``parent_id`` is resolved via id_remap, with a deferred
        second pass for children that appear before their parent in the payload.
        """
        raw = payload.get("concepts", []) if isinstance(payload, dict) else []
        # Two-pass: parents may follow children in the export ordering.
        deferred: list[dict[str, Any]] = []
        count = 0

        async def _upsert_one(c: dict[str, Any]) -> str | None:
            slug = c.get("slug")
            if not slug:
                return None
            try:
                res = await self.db.execute(
                    select(Concept).where(
                        Concept.slug == slug,
                        or_(
                            Concept.tenant_id == tenant_id,
                            Concept.tenant_id.is_(None),
                        ),
                        Concept.deleted_at.is_(None),
                    )
                )
                existing = res.scalar_one_or_none()
                kind_strs = c.get("kinds") or []
                if not kind_strs and c.get("primary_kind"):
                    kind_strs = [c.get("primary_kind")]

                parent_id: UUID | None = None
                parent_raw = c.get("parent_id")
                if parent_raw:
                    parent_key = str(parent_raw)
                    if parent_key in id_remap:
                        parent_id = _uuid(id_remap[parent_key])
                    else:
                        return "__defer__"

                if existing and existing.tenant_id == tenant_id:
                    # Update tenant-scoped row in place; reconcile kind tags.
                    existing.name = c.get("name") or existing.name
                    existing.description = c.get("description", existing.description)
                    existing.coding_system = c.get("coding_system", existing.coding_system)
                    existing.code = c.get("code", existing.code)
                    existing.aliases = c.get("aliases") or existing.aliases
                    existing.icon = c.get("icon", existing.icon)
                    existing.color = c.get("color", existing.color)
                    existing.display_order = c.get("display_order", existing.display_order)
                    if c.get("meta_data") is not None:
                        existing.meta_data = c.get("meta_data")
                    if parent_id is not None:
                        existing.parent_id = parent_id
                    await self._reconcile_kind_tags(existing, kind_strs)
                    await self.db.flush()
                    id_remap[str(c["id"])] = str(existing.id)
                    return str(existing.id)
                if existing:
                    # Global row already present; remap to it without mutating.
                    id_remap[str(c["id"])] = str(existing.id)
                    return str(existing.id)
                primary_raw = c.get("primary_kind")
                try:
                    primary_kind = ConceptKind(primary_raw) if primary_raw else None
                except ValueError:
                    primary_kind = None
                new_c = Concept(
                    tenant_id=tenant_id,
                    name=c.get("name") or slug,
                    slug=slug,
                    primary_kind=primary_kind,
                    parent_id=parent_id,
                    description=c.get("description"),
                    coding_system=c.get("coding_system"),
                    code=c.get("code"),
                    aliases=c.get("aliases") or [],
                    icon=c.get("icon"),
                    color=c.get("color"),
                    status=ConceptStatus.ACTIVE,
                    display_order=c.get("display_order", 0),
                    meta_data=c.get("meta_data"),
                )
                await self._reconcile_kind_tags(new_c, kind_strs)
                self.db.add(new_c)
                await self.db.flush()
                id_remap[str(c["id"])] = str(new_c.id)
                return str(new_c.id)
            except Exception as e:
                logger.warning(f"concept skipped: {e}")
                return None

        for c in raw:
            if not isinstance(c, dict) or not c.get("id") or not c.get("slug"):
                continue
            result = await _upsert_one(c)
            if result == "__defer__":
                deferred.append(c)
            elif result:
                count += 1
        for c in deferred:
            result = await _upsert_one(c)
            if result and result != "__defer__":
                count += 1
            else:
                # Parent still unresolved: insert with parent_id NULL rather
                # than drop the concept entirely.
                c = {**c, "parent_id": None}
                result = await _upsert_one(c)
                if result:
                    count += 1
        return count

    async def _reconcile_kind_tags(self, concept: Concept, kind_strs: list[str]) -> None:
        """Add missing kind tags; preserve extras (additive reconciliation)."""
        existing = {t.kind for t in (concept.kind_tags or [])}
        for k in kind_strs:
            if not k:
                continue
            try:
                ke = ConceptKind(k)
            except ValueError:
                continue
            if ke not in existing:
                concept.kind_tags.append(ConceptKindTag(kind=ke))
                existing.add(ke)

    async def _restore_anatomy(
        self,
        payload: dict[str, Any],
        tenant_id: UUID,
        id_remap: dict[str, str],
    ) -> tuple[int, int]:
        """Upsert custom/tenant anatomy structures + the relations between
        them. ``class_concept_id`` is remapped via id_remap (concepts restored
        first) with slug fallback. Returns (structures_count, relations_count)."""
        structures = payload.get("structures", []) if isinstance(payload, dict) else []
        relations = payload.get("relations", []) if isinstance(payload, dict) else []
        struct_count = 0
        for s in structures:
            if not isinstance(s, dict) or not s.get("slug"):
                continue
            try:
                res = await self.db.execute(
                    select(AnatomyStructure).where(
                        AnatomyStructure.slug == s["slug"],
                        or_(
                            AnatomyStructure.tenant_id == tenant_id,
                            AnatomyStructure.tenant_id.is_(None),
                        ),
                    )
                )
                existing = res.scalar_one_or_none()
                class_concept_id = await self._resolve_concept_fk(
                    s.get("class_concept_id"),
                    None,
                    ConceptKind.ANATOMY_CLASS,
                    tenant_id,
                    id_remap,
                )
                std_system = None
                ss = s.get("standard_system")
                if ss:
                    try:
                        std_system = CodingSystem(ss)
                    except ValueError:
                        std_system = None
                if existing and existing.tenant_id == tenant_id:
                    existing.name = s.get("name") or existing.name
                    existing.description = s.get("description", existing.description)
                    if class_concept_id is not None:
                        existing.class_concept_id = class_concept_id
                    existing.display = s.get("display", existing.display)
                    existing.is_custom = bool(s.get("is_custom", existing.is_custom))
                    existing.standard_system = std_system
                    existing.standard_code = s.get("standard_code")
                    id_remap[str(s["id"])] = str(existing.id)
                    struct_count += 1
                elif existing:
                    id_remap[str(s["id"])] = str(existing.id)
                else:
                    new_s = AnatomyStructure(
                        tenant_id=tenant_id,
                        name=s.get("name") or s["slug"],
                        slug=s["slug"],
                        class_concept_id=class_concept_id,
                        standard_system=std_system,
                        standard_code=s.get("standard_code"),
                        description=s.get("description"),
                        is_custom=bool(s.get("is_custom", True)),
                        display=s.get("display"),
                    )
                    self.db.add(new_s)
                    await self.db.flush()
                    id_remap[str(s["id"])] = str(new_s.id)
                    struct_count += 1
            except Exception as e:
                logger.warning(f"anatomy structure skipped: {e}")
        await self.db.flush()

        rel_count = 0
        for r in relations:
            if not isinstance(r, dict):
                continue
            try:
                src = self._remap_anatomy_endpoint(r.get("source_id"), id_remap)
                dst = self._remap_anatomy_endpoint(r.get("target_id"), id_remap)
                if src is None or dst is None:
                    logger.warning(
                        "anatomy relation skipped: unresolved endpoint (%s,%s)",
                        r.get("source_id"),
                        r.get("target_id"),
                    )
                    continue
                rel_type = r.get("relation_type")
                if not rel_type:
                    continue
                try:
                    rt = ConceptRelationType(rel_type)
                except ValueError:
                    continue
                res = await self.db.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                        ConceptEdge.src_id == src,
                        ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                        ConceptEdge.dst_id == dst,
                        ConceptEdge.relation == rt,
                    )
                )
                if res.scalar_one_or_none():
                    continue
                self.db.add(
                    ConceptEdge(
                        src_type=EdgeEndpointType.ANATOMY,
                        src_id=src,
                        dst_type=EdgeEndpointType.ANATOMY,
                        dst_id=dst,
                        relation=rt,
                        status=EdgeApprovalStatus.APPROVED,
                    )
                )
                rel_count += 1
            except Exception as e:
                logger.warning(f"anatomy relation skipped: {e}")
        await self.db.flush()
        return struct_count, rel_count

    @staticmethod
    def _remap_anatomy_endpoint(raw: Any, id_remap: dict[str, str]) -> UUID | None:
        if not raw:
            return None
        key = str(raw)
        if key in id_remap:
            return _uuid(id_remap[key])
        return _uuid(key)

    async def _restore_concept_edges(
        self,
        payload: dict[str, Any],
        tenant_id: UUID,
        id_remap: dict[str, str],
    ) -> int:
        """Upsert polymorphic concept edges (the knowledge graph).

        Endpoints are remapped via id_remap first. For CONCEPT/ANATOMY
        endpoints not present in the remap (e.g. an edge pointing at a global
        concept we did not export), fall back to the source id only if a
        matching in-tenant/global row exists; otherwise skip the edge with a
        warning. Edges referencing biomarkers/examinations/doctors are remapped
        through id_remap when present (those sidecars/FHIR entries are restored
        before edges). Upsert by the natural key
        ``(src_type, src_id, dst_type, dst_id, relation)`` for idempotency."""
        edges = payload.get("edges", []) if isinstance(payload, dict) else []
        count = 0
        for e in edges:
            if not isinstance(e, dict):
                continue
            try:
                src = await self._resolve_edge_endpoint(
                    e.get("src_type"), e.get("src_id"), tenant_id, id_remap
                )
                dst = await self._resolve_edge_endpoint(
                    e.get("dst_type"), e.get("dst_id"), tenant_id, id_remap
                )
                if src is None or dst is None:
                    logger.warning(
                        "concept edge skipped: unresolved endpoint src=%s/%s dst=%s/%s",
                        e.get("src_type"),
                        e.get("src_id"),
                        e.get("dst_type"),
                        e.get("dst_id"),
                    )
                    continue
                try:
                    relation = ConceptRelationType(e.get("relation"))
                except (ValueError, TypeError):
                    continue
                res = await self.db.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.src_type == src[0],
                        ConceptEdge.src_id == src[1],
                        ConceptEdge.dst_type == dst[0],
                        ConceptEdge.dst_id == dst[1],
                        ConceptEdge.relation == relation,
                    )
                )
                existing = res.scalar_one_or_none()
                source_val = e.get("source")
                try:
                    prov = ConceptProvenance(source_val) if source_val else ConceptProvenance.MANUAL
                except ValueError:
                    prov = ConceptProvenance.MANUAL
                status_val = e.get("status")
                try:
                    status = (
                        EdgeApprovalStatus(status_val)
                        if status_val
                        else EdgeApprovalStatus.APPROVED
                    )
                except ValueError:
                    status = EdgeApprovalStatus.APPROVED
                if existing:
                    existing.status = status
                    existing.properties = e.get("properties", existing.properties)
                    existing.evidence = e.get("evidence", existing.evidence)
                    count += 1
                    continue
                self.db.add(
                    ConceptEdge(
                        tenant_id=tenant_id,
                        src_type=src[0],
                        src_id=src[1],
                        dst_type=dst[0],
                        dst_id=dst[1],
                        relation=relation,
                        properties=e.get("properties"),
                        evidence=e.get("evidence"),
                        source=prov,
                        status=status,
                    )
                )
                count += 1
            except Exception as ex:
                logger.warning(f"concept edge skipped: {ex}")
        await self.db.flush()
        return count

    async def _resolve_edge_endpoint(
        self,
        type_str: str | None,
        id_raw: Any,
        tenant_id: UUID,
        id_remap: dict[str, str],
    ) -> tuple[EdgeEndpointType, UUID] | None:
        """Resolve an edge endpoint to ``(type, uuid)``.

        Remaps via id_remap when the source id was carried in the backup
        (concept/anatomy/biomarker/exam restored earlier). For CONCEPT and
        ANATOMY endpoints absent from id_remap, accepts the bare id if a
        matching visible row exists (global/seeded target). Returns None if the
        endpoint cannot be resolved — the caller skips the edge."""
        if not type_str or not id_raw:
            return None
        try:
            etype = EdgeEndpointType(type_str)
        except ValueError:
            return None
        key = str(id_raw)
        if key in id_remap:
            resolved = _uuid(id_remap[key])
            if resolved is not None:
                return (etype, resolved)
        cand = _uuid(key)
        if cand is None:
            return None
        # Existence check for concept/anatomy (the types whose rows may be
        # global and thus absent from id_remap). Biomarker/exam/doctor endpoints
        # must have been remapped if they were exported; otherwise the edge is
        # genuinely dangling and skipped.
        if etype == EdgeEndpointType.CONCEPT:
            res = await self.db.execute(
                select(Concept.id).where(
                    Concept.id == cand,
                    or_(
                        Concept.tenant_id == tenant_id,
                        Concept.tenant_id.is_(None),
                    ),
                    Concept.deleted_at.is_(None),
                )
            )
            if res.first():
                return (etype, cand)
            return None
        if etype == EdgeEndpointType.ANATOMY:
            res = await self.db.execute(
                select(AnatomyStructure.id).where(
                    AnatomyStructure.id == cand,
                    or_(
                        AnatomyStructure.tenant_id == tenant_id,
                        AnatomyStructure.tenant_id.is_(None),
                    ),
                )
            )
            if res.first():
                return (etype, cand)
            return None
        return (etype, cand)

    async def _restore_medication_catalog(self, payload: dict[str, Any], tenant_id: UUID) -> int:
        """Upsert MedicationCatalog entries by name (idempotent with seeds)."""
        count = 0
        for m in payload.get("medications", []):
            name = m.get("name")
            if not name:
                continue
            try:
                res = await self.db.execute(
                    select(MedicationCatalog).where(
                        func.lower(MedicationCatalog.name) == func.lower(name)
                    )
                )
                existing = res.scalar_one_or_none()
                if existing:
                    existing.description = m.get("description", existing.description)
                    existing.indications = m.get("indications", existing.indications)
                    existing.side_effects = m.get("side_effects", existing.side_effects)
                    existing.contraindications = m.get(
                        "contraindications", existing.contraindications
                    )
                    existing.dosage_info = m.get("dosage_info", existing.dosage_info)
                else:
                    self.db.add(
                        MedicationCatalog(
                            tenant_id=tenant_id,
                            name=name,
                            description=m.get("description"),
                            indications=m.get("indications"),
                            side_effects=m.get("side_effects"),
                            contraindications=m.get("contraindications"),
                            dosage_info=m.get("dosage_info"),
                        )
                    )
                count += 1
            except Exception as e:
                logger.warning(f"medication catalog row skipped: {e}")
        await self.db.flush()
        return count

    async def _restore_allergy_catalog(self, payload: dict[str, Any], tenant_id: UUID) -> int:
        """Upsert AllergyCatalog entries by name (idempotent with seeds)."""
        count = 0
        for a in payload.get("allergies", []):
            name = a.get("name")
            if not name:
                continue
            try:
                category_raw = (a.get("category") or "").strip().upper()
                try:
                    category = AllergyCategory(category_raw) if category_raw else None
                except ValueError:
                    category = AllergyCategory.OTHER if category_raw else None
                res = await self.db.execute(
                    select(AllergyCatalog).where(
                        func.lower(AllergyCatalog.name) == func.lower(name)
                    )
                )
                existing = res.scalar_one_or_none()
                if existing:
                    if category is not None:
                        existing.category = category
                    existing.description = a.get("description", existing.description)
                    existing.typical_reactions = a.get(
                        "typical_reactions", existing.typical_reactions
                    )
                else:
                    self.db.add(
                        AllergyCatalog(
                            tenant_id=tenant_id,
                            name=name,
                            category=category or AllergyCategory.OTHER,
                            description=a.get("description"),
                            typical_reactions=a.get("typical_reactions"),
                        )
                    )
                count += 1
            except Exception as e:
                logger.warning(f"allergy catalog row skipped: {e}")
        await self.db.flush()
        return count
