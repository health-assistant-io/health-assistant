import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Any
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.allergy import AllergyCatalog, AllergyCategory
from app.models.clinical_event import ClinicalEventCategory, ClinicalEventType
from app.models.anatomy_model import AnatomyStructure, AnatomyRelation, AnatomyFigure
from app.models.enums import AnatomyCategory, AnatomyRelationType, CodingSystem
from app.core.database import AsyncSessionLocal
from uuid import uuid4
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


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
            return {"added": 0, "updated": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
                event_types_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load clinical event types seeds: {e}")
            return {"added": 0, "updated": 0, "errors": 1}

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
        self, session: AsyncSession, data: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        stats = {
            "added_categories": 0,
            "added_types": 0,
            "updated_categories": 0,
            "updated_types": 0,
            "errors": 0,
        }

        if not await self._table_exists(session, "clinical_event_categories"):
            logger.error(
                "Table 'clinical_event_categories' does not exist. Skipping seeding."
            )
            return {"added": 0, "updated": 0, "errors": len(data)}

        for category_group in data:
            try:
                cat_data = category_group.get("category")
                if not cat_data:
                    continue

                # Process Category
                stmt = select(ClinicalEventCategory).where(
                    ClinicalEventCategory.slug == cat_data["slug"]
                )
                result = await session.execute(stmt)
                db_cat = result.scalar_one_or_none()

                if db_cat:
                    db_cat.name = cat_data.get("name", db_cat.name)
                    db_cat.icon = cat_data.get("icon", db_cat.icon)
                    db_cat.color = cat_data.get("color", db_cat.color)
                    stats["updated_categories"] += 1
                else:
                    db_cat = ClinicalEventCategory(
                        id=uuid4(),
                        name=cat_data["name"],
                        slug=cat_data["slug"],
                        icon=cat_data.get("icon"),
                        color=cat_data.get("color"),
                        tenant_id=None,
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(db_cat)
                    stats["added_categories"] += 1

                await session.flush()  # Ensure db_cat.id is available

                # Process Types for this category
                types_data = category_group.get("types", [])
                for type_item in types_data:
                    try:
                        stmt = select(ClinicalEventType).where(
                            ClinicalEventType.slug == type_item["slug"]
                        )
                        result = await session.execute(stmt)
                        db_type = result.scalar_one_or_none()

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
                            db_type.category_id = db_cat.id
                            stats["updated_types"] += 1
                        else:
                            new_type = ClinicalEventType(
                                id=uuid4(),
                                name=type_item["name"],
                                slug=type_item["slug"],
                                description=type_item.get("description"),
                                icon=type_item.get("icon"),
                                color=type_item.get("color"),
                                metadata_schema=type_item.get("metadata_schema"),
                                category_id=db_cat.id,
                                tenant_id=None,
                                created_at=datetime.now(timezone.utc),
                            )
                            session.add(new_type)
                            stats["added_types"] += 1
                    except Exception as e:
                        logger.error(
                            f"Error seeding clinical event type {type_item.get('slug')}: {e}"
                        )
                        stats["errors"] += 1
            except Exception as e:
                logger.error(f"Error seeding clinical event category group: {e}")
                stats["errors"] += 1

        # Fix stats structure for API if needed, or just return as is
        return stats

    async def seed_medications(self, session: AsyncSession = None) -> Dict[str, int]:
        """
        Sync medications from JSON to Database.
        Returns a summary of changes.
        """
        file_path = self.seeds_dir / "medications.json"
        if not file_path.exists():
            logger.warning(f"Medication seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
                medications_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load medication seeds: {e}")
            return {"added": 0, "updated": 0, "errors": 1}

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
        """
        file_path = self.seeds_dir / "anatomy_base.json"
        if not file_path.exists():
            logger.warning(f"Anatomy seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
                anatomy_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load anatomy seeds: {e}")
            return {"added": 0, "updated": 0, "errors": 1}

        if session:
            return await self._process_body_parts(session, anatomy_data)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await self._process_body_parts(new_session, anatomy_data)
                await new_session.commit()
                return result

    async def _process_body_parts(
        self, session: AsyncSession, data: Dict[str, Any]
    ) -> Dict[str, int]:
        stats = {"added": 0, "updated": 0, "errors": 0}

        if not await self._table_exists(session, "anatomy_structures"):
            logger.error("Table 'anatomy_structures' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "errors": len(data.get("nodes", []))}

        slug_to_id = {}
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        for item in nodes:
            try:
                stmt = select(AnatomyStructure).where(AnatomyStructure.slug == item["slug"])
                result = await session.execute(stmt)
                db_part = result.scalar_one_or_none()

                category_val = AnatomyCategory.OTHER
                try:
                    category_val = AnatomyCategory(item.get("category", "OTHER"))
                except ValueError:
                    pass

                standard_sys = None
                if item.get("standard_system"):
                    try:
                        standard_sys = CodingSystem(item["standard_system"])
                    except ValueError:
                        pass

                if db_part:
                    db_part.name = item.get("name", db_part.name)
                    db_part.category = category_val
                    db_part.standard_system = standard_sys
                    db_part.standard_code = item.get("standard_code", db_part.standard_code)
                    db_part.description = item.get("description", db_part.description)
                    db_part.display = item.get("display", db_part.display)
                    stats["updated"] += 1
                    slug_to_id[db_part.slug] = db_part.id
                else:
                    new_part = AnatomyStructure(
                        id=uuid4(),
                        name=item["name"],
                        slug=item["slug"],
                        category=category_val,
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

        all_nodes_result = await session.execute(select(AnatomyStructure.slug, AnatomyStructure.id))
        for slug, node_id in all_nodes_result.all():
            slug_to_id[slug] = node_id

        for edge in edges:
            try:
                source_id = slug_to_id.get(edge["source_slug"])
                target_id = slug_to_id.get(edge["target_slug"])
                if not source_id or not target_id:
                    logger.warning(f"Skipping edge: missing slug {edge.get('source_slug')} -> {edge.get('target_slug')}")
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
                logger.error(f"Error seeding anatomy edge {edge.get('source_slug')} -> {edge.get('target_slug')}: {e}")
                stats["errors"] += 1

        return stats

    async def seed_anatomy_figures(self, session: AsyncSession = None) -> Dict[str, int]:
        """
        Seed the four default body figures (man/woman x front/back) from WebP
        files under data/seeds/anatomy_figures/. Each file is copied into
        UPLOAD_DIR/anatomy_figures/ and the DB row records its path + pixel
        dimensions. Wikimedia surface diagrams, CC BY-SA 3.0 (see NOTICE).

        Idempotent: inserts missing rows and refreshes the image file when the
        seed changes. Markers are normalized 0-1 against pixel dimensions.
        """
        spec = [
            ("man-front",   "man-front.webp",   "Male \u2014 Front",   "man",   "front", 0),
            ("man-back",    "man-back.webp",    "Male \u2014 Back",    "man",   "back",  1),
            ("woman-front", "woman-front.webp", "Female \u2014 Front", "woman", "front", 2),
            ("woman-back",  "woman-back.webp",  "Female \u2014 Back",  "woman", "back",  3),
        ]
        seeds_dir = self.seeds_dir / "anatomy_figures"
        from app.services.anatomy_service import _figures_base_dir, FIGURES_DIR
        from app.core.config import settings

        async def _do(s: AsyncSession) -> Dict[str, int]:
            local = {"added": 0, "updated": 0, "errors": 0}
            if not await self._table_exists(s, "anatomy_figures"):
                logger.error("Table 'anatomy_figures' does not exist. Skipping figure seeding.")
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
                        if (existing.image_path != rel_path
                                or existing.width != w or existing.height != h):
                            existing.image_path = rel_path
                            existing.width, existing.height = w, h
                            existing.label = label
                            existing.sort_order = order
                            local["updated"] += 1
                        continue
                    s.add(AnatomyFigure(
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
                    ))
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
        stats = {"added": 0, "updated": 0, "errors": 0}

        if not await self._table_exists(session, "medication_catalog"):
            logger.error("Table 'medication_catalog' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "errors": len(data)}

        for item in data:
            try:
                # Check if medication exists by name (case-insensitive search)
                stmt = select(MedicationCatalog).where(
                    MedicationCatalog.name.ilike(item["name"])
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
            return {"added": 0, "updated": 0, "errors": 0}

        try:
            with open(file_path, "r") as f:
                allergies_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load allergy seeds: {e}")
            return {"added": 0, "updated": 0, "errors": 1}

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
        stats = {"added": 0, "updated": 0, "errors": 0}

        if not await self._table_exists(session, "allergy_catalog"):
            logger.error("Table 'allergy_catalog' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "errors": len(data)}

        for item in data:
            try:
                stmt = select(AllergyCatalog).where(
                    AllergyCatalog.name.ilike(item["name"])
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


seed_service = SeedService()
