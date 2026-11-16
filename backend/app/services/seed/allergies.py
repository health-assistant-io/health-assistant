"""Allergy catalog seeding (identity upsert — ADR 0055).

Every curated item carries an immutable qualified ``seed_key``
(``allergy:<kebab-key>``); the row id is the deterministic ``uuid5``
derived from it. Upsert is by id, then ``seed_key`` (``tenant_id IS
NULL``) — never by name. ``coding_system``/``code`` (SNOMED allergen
codes) update only when the seed provides them and are never fabricated.

Transaction ownership (ADR 0056): the seeder takes a required session and
parsed pack items, flushes, and never commits — the caller owns the
transaction.
"""

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seed_pack.bundled import bundled_stage_items
from app.models.fhir.allergy import AllergyCatalog
from app.services.seed.identity import item_label, require_seed_key, upsert_by_identity

logger = logging.getLogger(__name__)

_JSON_FIELDS = ("typical_reactions",)


def allergy_values(item: dict[str, Any]) -> dict[str, Any]:
    """The mutable fields of an allergy seed item (codes only when provided)."""
    values: dict[str, Any] = {"name": item["name"], "category": item["category"]}
    if item.get("description") is not None:
        values["description"] = item["description"]
    if item.get("typical_reactions") is not None:
        values["typical_reactions"] = item["typical_reactions"]
    if "coding_system" in item or "code" in item:
        values["coding_system"] = item.get("coding_system")
        values["code"] = item.get("code")
    return values


class AllergySeedMixin:
    async def seed_allergies(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Upsert the bundled (or given) allergy pack items by identity.

        ``items`` defaults to the bundled pack's parsed allergies; the caller
        owns the session and the transaction (flush only, no commit).
        """
        if items is None:
            items = bundled_stage_items("allergies", self.seeds_dir)
        return await self._process_allergies(session, list(items))

    async def _process_allergies(
        self, session: AsyncSession, data: Sequence[dict[str, Any]]
    ) -> dict[str, int]:
        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}

        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "allergy_catalog"):
            logger.error("Table 'allergy_catalog' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "unchanged": 0, "skipped": len(data), "errors": 0}

        for item in data:
            try:
                qualified = require_seed_key(item, "allergy")
                _, action = await upsert_by_identity(
                    session,
                    AllergyCatalog,
                    qualified,
                    allergy_values(item),
                    tenant_id=self.tenant_id,
                    explicit_id=item.get("id"),
                    json_fields=_JSON_FIELDS,
                )
                stats[action] += 1
            except Exception as e:
                self.note_item_error("allergies", item, e)
                logger.error(f"Error seeding allergy {item_label(item)}: {e}")
                stats["errors"] += 1

        await session.flush()
        return stats
