"""Clinical event-type seeding (typed metadata schemas)."""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.clinical_event import ClinicalEventType
from app.models.enums import ScheduleKind
from app.services.catalog.concept_service import concepts_with_kind

logger = logging.getLogger(__name__)


class ClinicalEventSeedMixin:
    async def seed_clinical_event_types(self, session: AsyncSession = None) -> dict[str, int]:
        """
        Sync clinical event types from JSON to Database.
        """
        file_path = self.seeds_dir / "clinical_event_types.json"
        if not file_path.exists():
            logger.warning(f"Clinical event types seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path) as f:
                event_types_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load clinical event types seeds: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}

        if session:
            return await self._process_clinical_event_types(session, event_types_data)
        else:
            async with AsyncSessionLocal() as new_session:
                result = await self._process_clinical_event_types(new_session, event_types_data)
                await new_session.commit()
                return result

    async def _process_clinical_event_types(
        self, session: AsyncSession, data: Any
    ) -> dict[str, int]:
        from pydantic import ValidationError

        from app.models.concept_model import Concept
        from app.models.enums import ConceptKind
        from app.schemas.clinical_event import MetadataSchema

        items = data if isinstance(data, list) else data.get("items", [])

        # Standard stats contract (see local planning notes/seed-system-robustness-2026-07-07.md
        # §Phase 3).
        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        if not await self._table_exists(session, "clinical_event_types"):
            logger.error("Table 'clinical_event_types' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "skipped": len(items), "errors": 0}

        cat_cache: dict[str, Any] = {}

        for type_item in items:
            try:
                # Fail-loud: validate the metadata_schema shape before upsert.
                # A malformed descriptor would silently render nothing in the
                # dynamic form; the typed Pydantic model rejects it here so the
                # seed run surfaces the error instead of writing a broken type.
                raw_schema = type_item.get("metadata_schema")
                if raw_schema is not None:
                    try:
                        MetadataSchema.model_validate(raw_schema)
                    except ValidationError as ve:
                        logger.error(
                            "Clinical event type '%s' has an invalid "
                            "metadata_schema; skipping. Details: %s",
                            type_item.get("slug"),
                            ve,
                        )
                        stats["errors"] += 1
                        continue

                # Phase 8e: category is required (NOT NULL on the column).
                # Seed entries should always declare a category_slug, but
                # fall back to the system "General" category if missing so
                # the seed never produces a row that violates the constraint.
                cat_slug = type_item.get("category_slug") or "general-event"
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

                stmt = select(ClinicalEventType).where(ClinicalEventType.slug == type_item["slug"])
                db_type = await session.scalar(stmt)

                if db_type:
                    db_type.name = type_item.get("name", db_type.name)
                    db_type.description = type_item.get("description", db_type.description)
                    db_type.icon = type_item.get("icon", db_type.icon)
                    db_type.color = type_item.get("color", db_type.color)
                    db_type.metadata_schema = type_item.get(
                        "metadata_schema", db_type.metadata_schema
                    )
                    if "schedule_kind" in type_item:
                        db_type.schedule_kind = ScheduleKind.from_string(
                            type_item.get("schedule_kind")
                        )
                    if cat_concept_id:
                        db_type.category_concept_id = cat_concept_id
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
                        # Phase 8a: required (NOT NULL). The seed loader falls
                        # back to STATE when the JSON omits the field; every
                        # shipped seed already declares it.
                        schedule_kind=ScheduleKind.from_string(type_item.get("schedule_kind"))
                        or ScheduleKind.STATE,
                        category_concept_id=cat_concept_id,
                        tenant_id=None,
                        created_at=datetime.now(UTC),
                    )
                    session.add(new_type)
                    stats["added"] += 1
            except Exception as e:
                logger.error(f"Error seeding clinical event type {type_item.get('slug')}: {e}")
                stats["errors"] += 1

        return stats
