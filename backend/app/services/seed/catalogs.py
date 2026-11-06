"""Default catalog, biomarker panel and state seeding."""

import json
import logging

from sqlalchemy import and_ as sa_and
from sqlalchemy import or_ as sa_or
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.services.catalog.concept_service import concepts_with_kind

logger = logging.getLogger(__name__)


class CatalogSeedMixin:
    async def seed_default_catalog(self, session: AsyncSession = None) -> dict[str, int]:
        """Seed the default biomarker catalog (units + biomarker definitions).

        Loads ``data/seeds/default_catalog.json`` and upserts via
        :class:`CatalogImportService`. Must run AFTER :meth:`seed_concepts`
        so that ``biomarker_class`` concepts exist for ``class_concept_id``
        resolution.

        Returns the standard stats shape ``{added, updated, skipped, errors}``
        with a ``details`` sub-dict preserving the units/biomarkers breakdown
        from the catalog importer.
        """
        from app.schemas.biomarker import CatalogImportPayload
        from app.services.catalog.catalog_import_service import CatalogImportService

        def _wrap(details: dict[str, int]) -> dict[str, int]:
            return {
                "added": details.get("units_added", 0) + details.get("biomarkers_added", 0),
                "updated": details.get("units_updated", 0) + details.get("biomarkers_updated", 0),
                "skipped": 0,
                "errors": details.get("errors", 0),
                "details": details,
            }

        file_path = self.seeds_dir / "default_catalog.json"
        if not file_path.exists():
            logger.warning(f"Default catalog seed file not found: {file_path}")
            return _wrap(
                {
                    "units_added": 0,
                    "units_updated": 0,
                    "biomarkers_added": 0,
                    "biomarkers_updated": 0,
                }
            )

        try:
            with open(file_path) as f:
                data = json.load(f)
            payload = CatalogImportPayload.model_validate(data)
        except Exception as e:
            logger.error(f"Failed to load default catalog seed: {e}")
            return _wrap(
                {
                    "units_added": 0,
                    "units_updated": 0,
                    "biomarkers_added": 0,
                    "biomarkers_updated": 0,
                    "errors": 1,
                }
            )

        if session:
            svc = CatalogImportService(session)
            return _wrap(await svc.import_catalog(payload))
        else:
            async with AsyncSessionLocal() as new_session:
                svc = CatalogImportService(new_session)
                result = await svc.import_catalog(payload)
                return _wrap(result)

    async def seed_biomarker_panels(self, session: AsyncSession = None) -> dict[str, int]:
        """Seed biomarker panel membership edges (MEMBER_OF).

        Loads ``data/seeds/biomarker_panels.json`` — ``{metadata, items}`` where
        each item carries ``{panel_slug, biomarker_slug}`` — and creates
        ``concept_edges`` rows of ``src_type=biomarker, relation=MEMBER_OF,
        dst_type=concept``.

        Must run AFTER :meth:`seed_concepts` (panels exist) AND
        :meth:`seed_default_catalog` (biomarkers exist).
        """
        from app.models.biomarker_model import BiomarkerDefinition
        from app.models.concept_model import Concept, ConceptEdge
        from app.models.enums import (
            ConceptKind,
            ConceptProvenance,
            ConceptRelationType,
            EdgeApprovalStatus,
            EdgeEndpointType,
        )

        file_path = self.seeds_dir / "biomarker_panels.json"
        if not file_path.exists():
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load biomarker panels seed: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        items = data.get("items", []) if isinstance(data, dict) else data

        async def _process(s: AsyncSession) -> dict[str, int]:
            stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}
            # Batch-resolve panels + biomarkers once (avoid N+1 over the membership list).
            panel_slugs = {it["panel_slug"] for it in items if it.get("panel_slug")}
            bio_slugs = {it["biomarker_slug"] for it in items if it.get("biomarker_slug")}
            panel_rows = (
                (
                    await s.execute(
                        select(Concept).where(
                            Concept.slug.in_(panel_slugs),
                            concepts_with_kind(ConceptKind.BIOMARKER_PANEL),
                            Concept.tenant_id.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            panel_by_slug = {p.slug: p for p in panel_rows}
            bio_rows = (
                (
                    await s.execute(
                        select(BiomarkerDefinition).where(BiomarkerDefinition.slug.in_(bio_slugs))
                    )
                )
                .scalars()
                .all()
            )
            bio_by_slug = {b.slug: b for b in bio_rows}

            for it in items:
                try:
                    panel = panel_by_slug.get(it.get("panel_slug"))
                    bio = bio_by_slug.get(it.get("biomarker_slug"))
                    if not panel or not bio:
                        stats["skipped"] += 1
                        continue

                    existing = await s.scalar(
                        select(ConceptEdge).where(
                            ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                            ConceptEdge.src_id == bio.id,
                            ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                            ConceptEdge.dst_id == panel.id,
                            ConceptEdge.relation == ConceptRelationType.MEMBER_OF,
                            ConceptEdge.tenant_id.is_(None),
                        )
                    )
                    if existing:
                        stats["updated"] += 1
                        continue

                    edge = ConceptEdge(
                        src_type=EdgeEndpointType.BIOMARKER,
                        src_id=bio.id,
                        dst_type=EdgeEndpointType.CONCEPT,
                        dst_id=panel.id,
                        relation=ConceptRelationType.MEMBER_OF,
                        tenant_id=None,
                        source=ConceptProvenance.SEED,
                        status=EdgeApprovalStatus.APPROVED,
                    )
                    s.add(edge)
                    stats["added"] += 1
                except Exception as e:
                    logger.error(f"Error seeding panel membership {it}: {e}")
                    stats["errors"] += 1

            await s.flush()
            return stats

        if session:
            return await _process(session)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await _process(new_session)
                await new_session.commit()
                return result

    async def seed_biomarker_states(self, session: AsyncSession = None) -> dict[str, int]:
        """Seed the canonical ``biomarker_states`` catalog (universal, no tenant).

        Loads ``data/seeds/biomarker_states.json`` and upserts by
        ``(code, system)`` — the controlled vocabulary of categorical values
        a STATE biomarker can take. Codes are drawn from HL7
        v3-ObservationInterpretation, SNOMED CT and FHIR DataAbsentReason
        (plus a ``urn:uuid:health-assistant:custom-state`` namespace for
        proprietary codes like WITHIN_LIMITS / TRACE). Idempotent: re-runs
        reconcile display/description/sort_order to the JSON.

        Standalone — no upstream dependency. A STATE biomarker picks its
        accepted subset via the ``biomarker_allowed_states`` join table
        (managed by the biomarker CRUD endpoints, not the seed).
        """
        from app.models.biomarker_model import BiomarkerState

        file_path = self.seeds_dir / "biomarker_states.json"
        if not file_path.exists():
            logger.warning(f"Biomarker states seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load biomarker states seed: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        items = data.get("items", []) if isinstance(data, dict) else data

        async def _process(s: AsyncSession) -> dict[str, int]:
            stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}
            if not await self._table_exists(s, "biomarker_states"):
                logger.error("Table 'biomarker_states' does not exist. Skipping state seed.")
                return {"added": 0, "updated": 0, "skipped": len(items), "errors": 0}
            # Pre-fetch existing rows indexed by (code, system) so the upsert
            # is a single round-trip rather than N queries.
            code_system_pairs = {(it["code"], it["system"]) for it in items}
            existing_rows = (
                (
                    await s.execute(
                        select(BiomarkerState).where(
                            # tuple_in doesn't translate cleanly across PG;
                            # build OR pairs. The catalog is small (<30 rows).
                            sa_or(
                                *[
                                    sa_and(
                                        BiomarkerState.code == code,
                                        BiomarkerState.system == system,
                                    )
                                    for code, system in code_system_pairs
                                ]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            existing = {(r.code, r.system): r for r in existing_rows}

            for item in items:
                try:
                    key = (item["code"], item["system"])
                    row = existing.get(key)
                    if row:
                        row.slug = item["slug"]
                        row.display = item["display"]
                        row.description = item.get("description")
                        row.category = item.get("category")
                        row.sort_order = item.get("sort_order", 0)
                        stats["updated"] += 1
                    else:
                        s.add(
                            BiomarkerState(
                                slug=item["slug"],
                                code=item["code"],
                                system=item["system"],
                                display=item["display"],
                                description=item.get("description"),
                                category=item.get("category"),
                                sort_order=item.get("sort_order", 0),
                            )
                        )
                        stats["added"] += 1
                except Exception as e:
                    logger.error(f"Error seeding biomarker state {item.get('slug')}: {e}")
                    stats["errors"] += 1
            await s.flush()
            return stats

        if session:
            return await _process(session)
        async with AsyncSessionLocal() as new_session:
            result = await _process(new_session)
            await new_session.commit()
            return result
