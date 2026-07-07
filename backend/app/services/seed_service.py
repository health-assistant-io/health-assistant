import json
import logging
from pathlib import Path
from typing import List, Dict, Any
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.allergy import AllergyCatalog
from app.models.clinical_event import ClinicalEventType
from app.models.anatomy_model import AnatomyStructure, AnatomyRelation, AnatomyFigure
from app.models.enums import (
    AnatomyCategory,
    AnatomyRelationType,
    CodingSystem,
    ConceptKind,
)
from app.core.database import AsyncSessionLocal
from app.services.concept_service import (
    resolve_concept_by_slug,
    concepts_with_kind,
    sync_concept_kind_tags,
)
from uuid import uuid4
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Map the legacy ``AnatomyCategory`` enum values (stored uppercase in the seed
# JSON) to the seeded ``anatomy_class`` concept slugs (lowercase). The
# ``anatomy_structures.category`` column was replaced by ``class_concept_id``
# (FK to ``concepts.id``) in the taxonomy consolidation.
_ANATOMY_CATEGORY_TO_CONCEPT_SLUG = {
    AnatomyCategory.SYSTEM.value: "system",
    AnatomyCategory.REGION.value: "region",
    AnatomyCategory.ORGAN.value: "organ",
    AnatomyCategory.ORGAN_PART.value: "organ-part",
    AnatomyCategory.TISSUE.value: "tissue",
    AnatomyCategory.JOINT.value: "joint",
    AnatomyCategory.CELL.value: "other-anatomy",
    AnatomyCategory.SUBSTANCE.value: "other-anatomy",
    AnatomyCategory.OTHER.value: "other-anatomy",
}


async def _resolve_anatomy_class_concept(
    session: AsyncSession, raw_category: str
) -> Any:
    """Resolve the legacy uppercase category string to a concept ID."""
    slug = _ANATOMY_CATEGORY_TO_CONCEPT_SLUG.get(
        (raw_category or "").strip().upper(), "other-anatomy"
    )
    return await resolve_concept_by_slug(
        session, slug, ConceptKind.ANATOMY_CLASS, tenant_id=None
    )


class SeedService:
    def __init__(self):
        self.seeds_dir = Path(__file__).parent.parent.parent / "data" / "seeds"

    async def seed_clinical_event_types(
        self, session: AsyncSession = None
    ) -> Dict[str, int]:
        """
        Sync clinical event types from JSON to Database.
        """
        file_path = self.seeds_dir / "clinical_event_types.json"
        if not file_path.exists():
            logger.warning(f"Clinical event types seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
                event_types_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load clinical event types seeds: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        if session:
            return await self._process_clinical_event_types(session, event_types_data)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await self._process_clinical_event_types(
                    new_session, event_types_data
                )
                await new_session.commit()
                return result

    async def _process_clinical_event_types(
        self, session: AsyncSession, data: Any
    ) -> Dict[str, int]:
        from app.models.concept_model import Concept
        from app.models.enums import ConceptKind

        items = data if isinstance(data, list) else data.get("items", [])

        # Standard stats contract (see dev/plans/seed-system-robustness-2026-07-07.md §Phase 3).
        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        if not await self._table_exists(session, "clinical_event_types"):
            logger.error(
                "Table 'clinical_event_types' does not exist. Skipping seeding."
            )
            return {"added": 0, "updated": 0, "skipped": len(items), "errors": 0}

        cat_cache: Dict[str, Any] = {}

        for type_item in items:
            try:
                cat_slug = type_item.get("category_slug")
                cat_concept_id = None
                if cat_slug:
                    if cat_slug not in cat_cache:
                        cat_res = await session.scalar(
                            select(Concept.id).where(
                                Concept.slug == cat_slug,
                                concepts_with_kind(ConceptKind.EVENT_CATEGORY),
                                Concept.tenant_id.is_(None),
                            )
                        )
                        cat_cache[cat_slug] = cat_res
                    cat_concept_id = cat_cache[cat_slug]

                stmt = select(ClinicalEventType).where(
                    ClinicalEventType.slug == type_item["slug"]
                )
                db_type = await session.scalar(stmt)

                if db_type:
                    db_type.name = type_item.get("name", db_type.name)
                    db_type.description = type_item.get(
                        "description", db_type.description
                    )
                    db_type.icon = type_item.get("icon", db_type.icon)
                    db_type.color = type_item.get("color", db_type.color)
                    db_type.metadata_schema = type_item.get(
                        "metadata_schema", db_type.metadata_schema
                    )
                    if cat_concept_id:
                        db_type.category_id = cat_concept_id
                    stats["updated"] += 1
                else:
                    new_type = ClinicalEventType(
                        id=uuid4(),
                        name=type_item["name"],
                        slug=type_item["slug"],
                        description=type_item.get("description"),
                        icon=type_item.get("icon"),
                        color=type_item.get("color"),
                        metadata_schema=type_item.get("metadata_schema"),
                        category_id=cat_concept_id,
                        tenant_id=None,
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(new_type)
                    stats["added"] += 1
            except Exception as e:
                logger.error(
                    f"Error seeding clinical event type {type_item.get('slug')}: {e}"
                )
                stats["errors"] += 1

        return stats

    async def seed_medications(self, session: AsyncSession = None) -> Dict[str, int]:
        """
        Sync medications from JSON to Database.
        Returns a summary of changes.
        """
        file_path = self.seeds_dir / "medications.json"
        if not file_path.exists():
            logger.warning(f"Medication seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
                medications_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load medication seeds: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        # If session is provided (manual run), use it.
        # Otherwise create a new one (lifespan run).
        if session:
            return await self._process_medications(session, medications_data)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await self._process_medications(new_session, medications_data)
                await new_session.commit()
                return result

    async def seed_body_parts(self, session: AsyncSession = None) -> Dict[str, int]:
        """
        Sync anatomy structures and relations from JSON to Database.

        Reads two standard-envelope seed files — ``anatomy_structures.json``
        (nodes) and ``anatomy_relations.json`` (edges) — and feeds them to
        :meth:`_process_body_parts` as a combined ``{nodes, edges}`` payload.
        """
        nodes = self._load_seed_json("anatomy_structures.json")
        edges = self._load_seed_json("anatomy_relations.json")
        if nodes is None or edges is None:
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        anatomy_data = {
            "nodes": nodes.get("items", []) if isinstance(nodes, dict) else nodes,
            "edges": edges.get("items", []) if isinstance(edges, dict) else edges,
        }

        if session:
            return await self._process_body_parts(session, anatomy_data)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await self._process_body_parts(new_session, anatomy_data)
                await new_session.commit()
                return result

    def _load_seed_json(self, filename: str) -> Any:
        """Load a seed JSON file, returning None + logging on any failure."""
        file_path = self.seeds_dir / filename
        if not file_path.exists():
            logger.warning(f"Seed file not found: {file_path}")
            return None
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}")
            return None

    async def _process_body_parts(
        self, session: AsyncSession, data: Dict[str, Any]
    ) -> Dict[str, int]:
        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        if not await self._table_exists(session, "anatomy_structures"):
            logger.error("Table 'anatomy_structures' does not exist. Skipping seeding.")
            return {
                "added": 0,
                "updated": 0,
                "skipped": len(data.get("nodes", [])),
                "errors": 0,
            }

        slug_to_id = {}
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        for item in nodes:
            try:
                stmt = select(AnatomyStructure).where(
                    AnatomyStructure.slug == item["slug"]
                )
                result = await session.execute(stmt)
                db_part = result.scalar_one_or_none()

                # Resolve the legacy uppercase category string to a concept ID.
                raw_category = item.get("category", "OTHER")
                class_concept_id = await _resolve_anatomy_class_concept(
                    session, raw_category
                )

                standard_sys = None
                if item.get("standard_system"):
                    try:
                        standard_sys = CodingSystem(item["standard_system"])
                    except ValueError:
                        pass

                if db_part:
                    db_part.name = item.get("name", db_part.name)
                    db_part.class_concept_id = class_concept_id
                    db_part.standard_system = standard_sys
                    db_part.standard_code = item.get(
                        "standard_code", db_part.standard_code
                    )
                    db_part.description = item.get("description", db_part.description)
                    db_part.display = item.get("display", db_part.display)
                    stats["updated"] += 1
                    slug_to_id[db_part.slug] = db_part.id
                else:
                    new_part = AnatomyStructure(
                        id=uuid4(),
                        name=item["name"],
                        slug=item["slug"],
                        class_concept_id=class_concept_id,
                        standard_system=standard_sys,
                        standard_code=item.get("standard_code"),
                        description=item.get("description"),
                        display=item.get("display"),
                        is_custom=False,
                        tenant_id=None,
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(new_part)
                    await session.flush()
                    stats["added"] += 1
                    slug_to_id[new_part.slug] = new_part.id
            except Exception as e:
                logger.error(f"Error seeding anatomy node {item.get('slug')}: {e}")
                stats["errors"] += 1

        all_nodes_result = await session.execute(
            select(AnatomyStructure.slug, AnatomyStructure.id)
        )
        for slug, node_id in all_nodes_result.all():
            slug_to_id[slug] = node_id

        for edge in edges:
            try:
                source_id = slug_to_id.get(edge["source_slug"])
                target_id = slug_to_id.get(edge["target_slug"])
                if not source_id or not target_id:
                    logger.warning(
                        f"Skipping edge: missing slug {edge.get('source_slug')} -> {edge.get('target_slug')}"
                    )
                    stats["errors"] += 1
                    continue

                rel_type_val = AnatomyRelationType.PART_OF
                try:
                    rel_type_val = AnatomyRelationType(edge["relation_type"])
                except ValueError:
                    pass

                existing_edge = await session.execute(
                    select(AnatomyRelation).where(
                        and_(
                            AnatomyRelation.source_id == source_id,
                            AnatomyRelation.target_id == target_id,
                            AnatomyRelation.relation_type == rel_type_val,
                        )
                    )
                )
                if not existing_edge.scalar_one_or_none():
                    new_relation = AnatomyRelation(
                        id=uuid4(),
                        source_id=source_id,
                        target_id=target_id,
                        relation_type=rel_type_val,
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(new_relation)
            except Exception as e:
                logger.error(
                    f"Error seeding anatomy edge {edge.get('source_slug')} -> {edge.get('target_slug')}: {e}"
                )
                stats["errors"] += 1

        return stats

    async def seed_anatomy_figures(
        self, session: AsyncSession = None
    ) -> Dict[str, int]:
        """
        Seed the four default body figures (man/woman x front/back) from WebP
        files under data/seeds/anatomy_figures/. Each file is copied into
        UPLOAD_DIR/anatomy_figures/ and the DB row records its path + pixel
        dimensions. Wikimedia surface diagrams, CC BY-SA 3.0 (see NOTICE).

        Idempotent: inserts missing rows and refreshes the image file when the
        seed changes. Markers are normalized 0-1 against pixel dimensions.
        """
        spec = [
            ("man-front", "man-front.webp", "Male \u2014 Front", "man", "front", 0),
            ("man-back", "man-back.webp", "Male \u2014 Back", "man", "back", 1),
            (
                "woman-front",
                "woman-front.webp",
                "Female \u2014 Front",
                "woman",
                "front",
                2,
            ),
            ("woman-back", "woman-back.webp", "Female \u2014 Back", "woman", "back", 3),
        ]
        seeds_dir = self.seeds_dir / "anatomy_figures"
        from app.services.anatomy_service import _figures_base_dir, FIGURES_DIR
        from app.core.config import settings

        async def _do(s: AsyncSession) -> Dict[str, int]:
            local = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}
            if not await self._table_exists(s, "anatomy_figures"):
                logger.error(
                    "Table 'anatomy_figures' does not exist. Skipping figure seeding."
                )
                local["errors"] = 1
                return local
            dest_dir = _figures_base_dir()
            for slug, fname, label, fkey, vkey, order in spec:
                try:
                    src = seeds_dir / fname
                    if not src.exists():
                        logger.warning(f"Figure seed not found: {src}")
                        local["errors"] += 1
                        continue
                    data = src.read_bytes()
                    from PIL import Image as PILImage
                    import io

                    with PILImage.open(io.BytesIO(data)) as img:
                        w, h = img.size
                    rel_path = f"{FIGURES_DIR}/{fname}"
                    dest = Path(str(settings.UPLOAD_DIR)) / rel_path
                    need_write = (not dest.exists()) or (dest.read_bytes() != data)
                    if need_write:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(data)
                    existing_res = await s.execute(
                        select(AnatomyFigure).where(AnatomyFigure.slug == slug)
                    )
                    existing = existing_res.scalar_one_or_none()
                    if existing:
                        if (
                            existing.image_path != rel_path
                            or existing.width != w
                            or existing.height != h
                        ):
                            existing.image_path = rel_path
                            existing.width, existing.height = w, h
                            existing.label = label
                            existing.sort_order = order
                            local["updated"] += 1
                        continue
                    s.add(
                        AnatomyFigure(
                            id=uuid4(),
                            slug=slug,
                            label=label,
                            figure_key=fkey,
                            view_key=vkey,
                            image_path=rel_path,
                            width=w,
                            height=h,
                            sort_order=order,
                            is_active=True,
                            created_at=datetime.now(timezone.utc),
                        )
                    )
                    local["added"] += 1
                except Exception as e:
                    logger.error(f"Error seeding figure {slug}: {e}")
                    local["errors"] += 1
            await s.commit()
            return local

        if session:
            return await _do(session)
        async with AsyncSessionLocal() as new_session:
            return await _do(new_session)

    async def _table_exists(self, session: AsyncSession, table_name: str) -> bool:
        def check(sync_session):
            from sqlalchemy import inspect

            # Get connection from session
            conn = sync_session.connection()
            ins = inspect(conn)
            return ins.has_table(table_name)

        return await session.run_sync(check)

    async def _process_medications(
        self, session: AsyncSession, data: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "medication_catalog"):
            logger.error("Table 'medication_catalog' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "skipped": len(data), "errors": 0}

        for item in data:
            try:
                # Check if medication exists by name (case-insensitive search)
                stmt = select(MedicationCatalog).where(
                    func.lower(MedicationCatalog.name) == func.lower(item["name"])
                )
                result = await session.execute(stmt)
                db_med = result.scalar_one_or_none()

                if db_med:
                    # Update existing if needed (Optional: only update if fields changed)
                    db_med.description = item.get("description", db_med.description)
                    db_med.indications = item.get("indications", db_med.indications)
                    db_med.side_effects = item.get("side_effects", db_med.side_effects)
                    db_med.contraindications = item.get(
                        "contraindications", db_med.contraindications
                    )
                    db_med.dosage_info = item.get("dosage_info", db_med.dosage_info)
                    stats["updated"] += 1
                else:
                    # Create new
                    new_med = MedicationCatalog(
                        id=uuid4(),
                        name=item["name"],
                        description=item.get("description"),
                        indications=item.get("indications"),
                        side_effects=item.get("side_effects"),
                        contraindications=item.get("contraindications"),
                        dosage_info=item.get("dosage_info"),
                        tenant_id=None,  # System-wide
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(new_med)
                    stats["added"] += 1
            except Exception as e:
                logger.error(f"Error seeding medication {item.get('name')}: {e}")
                stats["errors"] += 1

        return stats

    async def seed_allergies(self, session: AsyncSession = None) -> Dict[str, int]:
        """
        Sync allergies from JSON to Database.
        """
        file_path = self.seeds_dir / "allergies.json"
        if not file_path.exists():
            logger.warning(f"Allergy seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
                allergies_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load allergy seeds: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        if session:
            return await self._process_allergies(session, allergies_data)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await self._process_allergies(new_session, allergies_data)
                await new_session.commit()
                return result

    async def _process_allergies(
        self, session: AsyncSession, data: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "allergy_catalog"):
            logger.error("Table 'allergy_catalog' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "skipped": len(data), "errors": 0}

        for item in data:
            try:
                stmt = select(AllergyCatalog).where(
                    func.lower(AllergyCatalog.name) == func.lower(item["name"])
                )
                result = await session.execute(stmt)
                db_allergy = result.scalar_one_or_none()

                if db_allergy:
                    db_allergy.category = item.get("category", db_allergy.category)
                    db_allergy.description = item.get(
                        "description", db_allergy.description
                    )
                    db_allergy.typical_reactions = item.get(
                        "typical_reactions", db_allergy.typical_reactions
                    )
                    stats["updated"] += 1
                else:
                    new_allergy = AllergyCatalog(
                        id=uuid4(),
                        name=item["name"],
                        category=item["category"],
                        description=item.get("description"),
                        typical_reactions=item.get("typical_reactions"),
                        tenant_id=None,
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(new_allergy)
                    stats["added"] += 1
            except Exception as e:
                logger.error(f"Error seeding allergy {item.get('name')}: {e}")
                stats["errors"] += 1

        return stats

    async def seed_concepts(self, session: AsyncSession = None) -> Dict[str, int]:
        """Sync the unified concept taxonomy from JSON to Database."""

        file_path = self.seeds_dir / "concepts.json"
        if not file_path.exists():
            logger.warning(f"Concepts seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
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
        self, session: AsyncSession, data: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        from app.models.concept_model import Concept, ConceptKindTag
        from app.models.enums import ConceptKind, ConceptStatus

        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "concepts"):
            logger.error("Table 'concepts' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "skipped": len(data), "errors": 0}

        slug_to_id: Dict[str, Any] = {}

        for item in data:
            try:
                # Accept both legacy single "kind" and new "kinds" array.
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
                    db_concept.description = item.get(
                        "description", db_concept.description
                    )
                    db_concept.coding_system = item.get(
                        "coding_system", db_concept.coding_system
                    )
                    db_concept.code = item.get("code", db_concept.code)
                    db_concept.aliases = item.get("aliases", db_concept.aliases)
                    db_concept.icon = item.get("icon", db_concept.icon)
                    db_concept.color = item.get("color", db_concept.color)
                    db_concept.display_order = item.get(
                        "display_order", db_concept.display_order
                    )
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

    async def seed_concept_edges(self, session: AsyncSession = None) -> Dict[str, int]:
        """Sync concept relationships (edges) from JSON to Database."""
        file_path = self.seeds_dir / "concept_edges.json"
        if not file_path.exists():
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
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
        self, session: AsyncSession, data: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        from app.models.concept_model import Concept, ConceptEdge
        from app.models.enums import (
            ConceptRelationType,
            ConceptProvenance,
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

        async def _resolve_endpoint(slug: str, etype: str):
            if not slug:
                return None
            if etype == "concept":
                row = await session.execute(
                    select(Concept.id).where(
                        Concept.slug == slug, Concept.tenant_id.is_(None)
                    )
                )
                eid = row.scalar_one_or_none()
                return (EdgeEndpointType.CONCEPT, eid) if eid else None
            if etype == "anatomy":
                row = await session.execute(
                    select(AnatomyStructure.id).where(AnatomyStructure.slug == slug)
                )
                eid = row.scalar_one_or_none()
                return (EdgeEndpointType.ANATOMY, eid) if eid else None
            logger.warning(
                f"Unknown seed endpoint type '{etype}' (slug={slug}); skipping."
            )
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

    async def seed_default_catalog(
        self, session: AsyncSession = None
    ) -> Dict[str, int]:
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
        from app.services.catalog_import_service import CatalogImportService

        def _wrap(details: Dict[str, int]) -> Dict[str, int]:
            return {
                "added": details.get("units_added", 0)
                + details.get("biomarkers_added", 0),
                "updated": details.get("units_updated", 0)
                + details.get("biomarkers_updated", 0),
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
            with open(file_path, "r") as f:
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

    async def seed_biomarker_panels(
        self, session: AsyncSession = None
    ) -> Dict[str, int]:
        """Seed biomarker panel membership edges (MEMBER_OF).

        Loads ``data/seeds/biomarker_panels.json`` — ``{metadata, items}`` where
        each item carries ``{panel_slug, biomarker_slug}`` — and creates
        ``concept_edges`` rows of ``src_type=biomarker, relation=MEMBER_OF,
        dst_type=concept``.

        Must run AFTER :meth:`seed_concepts` (panels exist) AND
        :meth:`seed_default_catalog` (biomarkers exist).
        """
        from app.models.concept_model import Concept, ConceptEdge
        from app.models.biomarker_model import BiomarkerDefinition
        from app.models.enums import (
            EdgeEndpointType,
            ConceptRelationType,
            ConceptProvenance,
            EdgeApprovalStatus,
            ConceptKind,
        )

        file_path = self.seeds_dir / "biomarker_panels.json"
        if not file_path.exists():
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load biomarker panels seed: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        items = data.get("items", []) if isinstance(data, dict) else data

        async def _process(s: AsyncSession) -> Dict[str, int]:
            stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}
            # Batch-resolve panels + biomarkers once (avoid N+1 over the membership list).
            panel_slugs = {it["panel_slug"] for it in items if it.get("panel_slug")}
            bio_slugs = {
                it["biomarker_slug"] for it in items if it.get("biomarker_slug")
            }
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
                        select(BiomarkerDefinition).where(
                            BiomarkerDefinition.slug.in_(bio_slugs)
                        )
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

    # Ordered list populated at call time (methods are bound to ``self``).
    # Dependencies are documented inline — moving a stage here is the single
    # place to review ordering, replacing the hardcoded call sequence that
    # used to live in ``main.py``.
    _SEED_STAGE_NAMES: list[str] = [
        "medications",  # standalone
        "clinical_event_types",  # standalone
        "allergies",  # standalone
        "body_parts",  # standalone (anatomy_structures)
        "anatomy_figures",  # after body_parts
        "concepts",  # standalone (taxonomy)
        "concept_edges",  # after concepts + body_parts
        "default_catalog",  # after concepts (biomarker_class)
        "biomarker_panels",  # after concepts + default_catalog
    ]

    async def seed_all(self) -> Dict[str, Dict[str, int]]:
        """Run every seed stage in declared dependency order.

        Returns ``{stage_name: stats}``. Order is explicit in
        :attr:`_SEED_STAGE_NAMES` so dependencies (e.g. ``biomarker_class``
        concepts must exist before the default catalog; ``body_parts`` before
        ``concept_edges`` that resolve anatomy slugs) are reviewable in one
        place rather than scattered across call sites.
        """
        out: Dict[str, Dict[str, int]] = {}
        for name in self._SEED_STAGE_NAMES:
            logger.info("Seeding %s...", name)
            fn = getattr(self, f"seed_{name}")
            out[name] = await fn()
            logger.info("Seeded %s: %s", name, out[name])
        return out


seed_service = SeedService()
