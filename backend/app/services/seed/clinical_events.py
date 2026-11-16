"""Clinical event-type seeding (typed metadata schemas, identity upsert — ADR 0055).

Event types carry an immutable qualified ``seed_key``
(``clinical-event-type:<kebab-key>``) in the seed JSON; the row id is the
deterministic ``uuid5`` derived from it (the table has no ``seed_key``
column — identity lives in the id). Upsert is by id, ``tenant_id IS
NULL``.

Transaction ownership (ADR 0056): the seeder takes a required session and
parsed pack items, flushes, and never commits — the caller owns the
transaction.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seed_pack.bundled import bundled_stage_items
from app.models.clinical_event import ClinicalEventType
from app.models.enums import ScheduleKind
from app.services.catalog.concept_service import concepts_with_kind
from app.services.seed.identity import (
    apply_values,
    find_identity_row,
    identity_id,
    item_label,
    require_seed_key,
)

logger = logging.getLogger(__name__)

_JSON_FIELDS = ("icon", "metadata_schema")


def event_type_values(type_item: dict[str, Any], cat_concept_id: Any) -> dict[str, Any]:
    """The mutable fields of a clinical event type seed item."""
    values: dict[str, Any] = {
        "name": type_item["name"],
        "slug": type_item["slug"],
    }
    for field in ("description", "icon", "color", "metadata_schema"):
        if type_item.get(field) is not None:
            values[field] = type_item[field]
    if "schedule_kind" in type_item:
        values["schedule_kind"] = (
            ScheduleKind.from_string(type_item.get("schedule_kind")) or ScheduleKind.STATE
        )
    if cat_concept_id:
        values["category_concept_id"] = cat_concept_id
    return values


class ClinicalEventSeedMixin:
    async def seed_clinical_event_types(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Sync clinical event types from the bundled (or given) pack items.

        The caller owns the session and the transaction (flush only, no
        commit).
        """
        if items is None:
            items = bundled_stage_items("clinical_event_types", self.seeds_dir)
        return await self._process_clinical_event_types(session, list(items))

    async def _process_clinical_event_types(
        self, session: AsyncSession, data: Sequence[dict[str, Any]]
    ) -> dict[str, int]:
        from pydantic import ValidationError

        from app.models.concept_model import Concept
        from app.models.enums import ConceptKind
        from app.schemas.clinical_event import MetadataSchema

        items = data if isinstance(data, list) else data.get("items", [])

        # Standard stats contract (see local planning notes/seed-system-robustness-2026-07-07.md
        # §Phase 3).
        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}

        if not await self._table_exists(session, "clinical_event_types"):
            logger.error("Table 'clinical_event_types' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "unchanged": 0, "skipped": len(items), "errors": 0}

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
                        self.note_item_error("clinical_event_types", type_item, ve)
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

                # The clinical_event_types table stores no ``seed_key`` column
                # (the identity lives in the id), so an export cannot carry
                # one back — derive it from the slug (the curated form is
                # exactly ``clinical-event-type:<slug>``).
                type_item = {
                    **type_item,
                    "seed_key": type_item.get("seed_key")
                    or f"clinical-event-type:{type_item['slug']}",
                }
                qualified = require_seed_key(type_item, "clinical-event-type")
                db_type = await find_identity_row(
                    session,
                    ClinicalEventType,
                    qualified,
                    tenant_id=self.tenant_id,
                    explicit_id=type_item.get("id"),
                )

                if db_type:
                    changed = apply_values(
                        db_type, event_type_values(type_item, cat_concept_id), _JSON_FIELDS
                    )
                    stats["updated" if changed else "unchanged"] += 1
                else:
                    values = event_type_values(type_item, cat_concept_id)
                    # Phase 8a: schedule_kind is required (NOT NULL) — the
                    # loader falls back to STATE when the JSON omits the
                    # field; every shipped seed already declares it.
                    values.setdefault("schedule_kind", ScheduleKind.STATE)
                    new_type = ClinicalEventType(
                        id=identity_id(qualified, type_item.get("id"), tenant_id=self.tenant_id),
                        tenant_id=self.tenant_id,
                        created_at=datetime.now(UTC),
                        **values,
                    )
                    session.add(new_type)
                    stats["added"] += 1
            except Exception as e:
                self.note_item_error("clinical_event_types", type_item, e)
                logger.error(f"Error seeding clinical event type {item_label(type_item)}: {e}")
                stats["errors"] += 1

        await session.flush()
        return stats
