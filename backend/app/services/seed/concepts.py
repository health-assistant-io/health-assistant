"""Concept, disease and concept-edge seeding."""

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.services.catalog.concept_service import sync_concept_kind_tags

logger = logging.getLogger(__name__)


class ConceptSeedMixin:
    async def seed_concepts(self, session: AsyncSession = None) -> dict[str, int]:
        """Sync the unified concept taxonomy from JSON to Database."""

        file_path = self.seeds_dir / "concepts.json"
        if not file_path.exists():
            logger.warning(f"Concepts seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load concepts seed: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        if session:
            return await self._process_concepts(session, data)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await self._process_concepts(new_session, data)
                await new_session.commit()
                return result

    async def _process_concepts(
        self, session: AsyncSession, data: list[dict[str, Any]]
    ) -> dict[str, int]:
        from app.models.concept_model import Concept, ConceptKindTag
        from app.models.enums import CatalogScope, ConceptKind, ConceptStatus

        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "concepts"):
            logger.error("Table 'concepts' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "skipped": len(data), "errors": 0}

        slug_to_id: dict[str, Any] = {}

        for item in data:
            try:
                # Accept both the single "kind" and the "kinds" array.
                raw_kinds = item.get("kinds")
                if raw_kinds:
                    kinds = [ConceptKind(k) for k in raw_kinds]
                else:
                    kinds = [ConceptKind(item["kind"])]
                slug = item["slug"]

                # Slug is globally unique per tenant now (post multi-kind), so
                # look up by slug alone — NOT (kind, slug). Filtering by kinds[0]
                # would miss an existing concept whose kind tags were edited,
                # then try to INSERT and hit the (slug, tenant) unique index.
                stmt = select(Concept).where(
                    Concept.slug == slug,
                    Concept.tenant_id.is_(None),
                )
                result = await session.execute(stmt)
                db_concept = result.scalar_one_or_none()

                parent_id = None
                parent_slug = item.get("parent_slug")
                if parent_slug and parent_slug in slug_to_id:
                    parent_id = slug_to_id[parent_slug]

                if db_concept:
                    db_concept.name = item.get("name", db_concept.name)
                    db_concept.description = item.get("description", db_concept.description)
                    db_concept.coding_system = item.get("coding_system", db_concept.coding_system)
                    db_concept.code = item.get("code", db_concept.code)
                    db_concept.aliases = item.get("aliases", db_concept.aliases)
                    db_concept.icon = item.get("icon", db_concept.icon)
                    db_concept.color = item.get("color", db_concept.color)
                    db_concept.display_order = item.get("display_order", db_concept.display_order)
                    if parent_id:
                        db_concept.parent_id = parent_id
                    # Reconcile kind tags to the JSON's `kinds` — without this,
                    # changing a concept's kinds in the seed file and re-seeding
                    # would silently do nothing (the create path sets tags, the
                    # update path used to skip them).
                    sync_concept_kind_tags(db_concept, kinds)
                    stats["updated"] += 1
                    slug_to_id[slug] = db_concept.id
                else:
                    new_concept = Concept(
                        slug=slug,
                        name=item["name"],
                        primary_kind=kinds[0],
                        tenant_id=None,
                        scope=CatalogScope.SYSTEM,
                        description=item.get("description"),
                        coding_system=item.get("coding_system"),
                        code=item.get("code"),
                        aliases=item.get("aliases", []),
                        icon=item.get("icon"),
                        color=item.get("color"),
                        display_order=item.get("display_order", 0),
                        parent_id=parent_id,
                        status=ConceptStatus.ACTIVE,
                    )
                    for k in kinds:
                        new_concept.kind_tags.append(ConceptKindTag(kind=k))
                    session.add(new_concept)
                    await session.flush()
                    stats["added"] += 1
                    slug_to_id[slug] = new_concept.id
            except Exception as e:
                logger.error(f"Error seeding concept {item.get('slug')}: {e}")
                stats["errors"] += 1

        await session.flush()
        return stats

    async def seed_diseases(self, session: AsyncSession = None) -> dict[str, int]:
        """Sync disease reference concepts (``kind=disease``, ICD-10 codes) from JSON.

        Diseases live as concepts — they inherit full CRUD/search/edges/FHIR for
        free via the concept layer. This stage loads ``diseases.json`` (same
        envelope format as ``concepts.json``) through :meth:`_process_concepts`
        so the upsert + kind-tag reconciliation logic is shared. Must run AFTER
        :meth:`seed_concepts` (for any parent_slug refs) and BEFORE
        :meth:`seed_concept_edges` (so specialty/medication/vaccine → disease
        edges resolve).
        """
        file_path = self.seeds_dir / "diseases.json"
        if not file_path.exists():
            logger.warning(f"Diseases seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load diseases seed: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        if session:
            return await self._process_concepts(session, data)
        async with AsyncSessionLocal() as new_session:
            result = await self._process_concepts(new_session, data)
            await new_session.commit()
            return result

    async def seed_concept_edges(self, session: AsyncSession = None) -> dict[str, int]:
        """Sync concept relationships (edges) from JSON to Database."""
        file_path = self.seeds_dir / "concept_edges.json"
        if not file_path.exists():
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load concept edges seed: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        if session:
            return await self._process_concept_edges(session, data)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await self._process_concept_edges(new_session, data)
                await new_session.commit()
                return result

    async def _process_concept_edges(
        self, session: AsyncSession, data: list[dict[str, Any]]
    ) -> dict[str, int]:
        from app.models.concept_model import Concept, ConceptEdge
        from app.models.enums import (
            ConceptProvenance,
            ConceptRelationType,
            EdgeApprovalStatus,
            EdgeEndpointType,
        )

        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "concept_edges"):
            logger.error("Table 'concept_edges' does not exist. Skipping.")
            return {"added": 0, "updated": 0, "skipped": len(data), "errors": 0}

        # Resolve a slug to an endpoint {type, id} per the seed item's declared
        # type. Defaults to "concept" so existing seed entries (which omit the
        # type) keep working unchanged. Add a branch here to support more
        # polymorphic endpoint tables (biomarker, examination, …).
        from app.models.anatomy_model import AnatomyStructure
        from app.models.biomarker_model import BiomarkerDefinition
        from app.models.fhir.medication import MedicationCatalog
        from app.models.fhir.vaccine import VaccineCatalog

        async def _resolve_endpoint(slug: str, etype: str):
            if not slug:
                return None
            if etype == "concept":
                row = await session.execute(
                    select(Concept.id).where(Concept.slug == slug, Concept.tenant_id.is_(None))
                )
                eid = row.scalar_one_or_none()
                return (EdgeEndpointType.CONCEPT, eid) if eid else None
            if etype == "anatomy":
                row = await session.execute(
                    select(AnatomyStructure.id).where(AnatomyStructure.slug == slug)
                )
                eid = row.scalar_one_or_none()
                return (EdgeEndpointType.ANATOMY, eid) if eid else None
            if etype == "biomarker":
                row = await session.execute(
                    select(BiomarkerDefinition.id).where(BiomarkerDefinition.slug == slug)
                )
                eid = row.scalar_one_or_none()
                return (EdgeEndpointType.BIOMARKER, eid) if eid else None
            if etype == "medication":
                # MedicationCatalog has no slug — resolve by name (case-insensitive).
                row = await session.execute(
                    select(MedicationCatalog.id).where(
                        func.lower(MedicationCatalog.name) == slug.lower(),
                        MedicationCatalog.tenant_id.is_(None),
                    )
                )
                eid = row.scalar_one_or_none()
                return (EdgeEndpointType.MEDICATION, eid) if eid else None
            if etype == "vaccine":
                # VaccineCatalog has a slug — resolve directly (global rows only).
                row = await session.execute(
                    select(VaccineCatalog.id).where(
                        VaccineCatalog.slug == slug,
                        VaccineCatalog.tenant_id.is_(None),
                    )
                )
                eid = row.scalar_one_or_none()
                return (EdgeEndpointType.IMMUNIZATION, eid) if eid else None
            logger.warning(f"Unknown seed endpoint type '{etype}' (slug={slug}); skipping.")
            return None

        for item in data:
            try:
                src_type = item.get("src_type", "concept")
                dst_type = item.get("dst_type", "concept")
                relation = ConceptRelationType(item["relation"])

                src = await _resolve_endpoint(item.get("src_slug"), src_type)
                dst = await _resolve_endpoint(item.get("dst_slug"), dst_type)

                if not src or not dst:
                    stats["skipped"] += 1
                    continue

                src_etype, src_id = src
                dst_etype, dst_id = dst

                existing = await session.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.src_type == src_etype,
                        ConceptEdge.src_id == src_id,
                        ConceptEdge.dst_type == dst_etype,
                        ConceptEdge.dst_id == dst_id,
                        ConceptEdge.relation == relation,
                        ConceptEdge.tenant_id.is_(None),
                    )
                )
                if existing.scalar_one_or_none():
                    stats["updated"] += 1
                    continue

                edge = ConceptEdge(
                    src_type=src_etype,
                    src_id=src_id,
                    dst_type=dst_etype,
                    dst_id=dst_id,
                    relation=relation,
                    tenant_id=None,
                    source=ConceptProvenance.SEED,
                    status=EdgeApprovalStatus.APPROVED,
                )
                session.add(edge)
                stats["added"] += 1
            except Exception as e:
                logger.error(f"Error seeding concept edge {item}: {e}")
                stats["errors"] += 1

        await session.flush()
        return stats
