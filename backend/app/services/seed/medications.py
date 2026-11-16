"""Medication catalog seeding (identity upsert — ADR 0055).

Every curated item carries an immutable qualified ``seed_key``
(``medication:<kebab-key>``); the row id is the deterministic
``uuid5`` derived from it. Upsert is by id, then ``seed_key``
(``tenant_id IS NULL``) — never by name. ``coding_system``/``code`` are
attributes: they update only when the seed provides them and are never
fabricated.

Transaction ownership (ADR 0056): the seeder takes a required session and
parsed pack items, flushes, and never commits — the caller owns the
transaction.
"""

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seed_pack.bundled import bundled_stage_items
from app.models.fhir.medication import MedicationCatalog
from app.services.seed.identity import item_label, require_seed_key, upsert_by_identity

logger = logging.getLogger(__name__)

_JSON_FIELDS = ("side_effects",)
_OPTIONAL_FIELDS = ("description", "indications", "contraindications", "dosage_info")


def medication_values(item: dict[str, Any]) -> dict[str, Any]:
    """The mutable fields of a medication seed item (codes only when provided)."""
    values: dict[str, Any] = {"name": item["name"]}
    for field in _OPTIONAL_FIELDS:
        if item.get(field) is not None:
            values[field] = item[field]
    if item.get("side_effects") is not None:
        values["side_effects"] = item["side_effects"]
    if "coding_system" in item or "code" in item:
        values["coding_system"] = item.get("coding_system")
        values["code"] = item.get("code")
    return values


class MedicationSeedMixin:
    async def seed_medications(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Upsert the bundled (or given) medication pack items by identity.

        ``items`` defaults to the bundled pack's parsed medications; the
        caller owns the session and the transaction (flush only, no commit).
        Returns the standard stats shape ``{added, updated, unchanged,
        skipped, errors}``.
        """
        if items is None:
            items = bundled_stage_items("medications", self.seeds_dir)
        return await self._process_medications(session, list(items))

    async def _process_medications(
        self, session: AsyncSession, data: Sequence[dict[str, Any]]
    ) -> dict[str, int]:
        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}

        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "medication_catalog"):
            logger.error("Table 'medication_catalog' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "unchanged": 0, "skipped": len(data), "errors": 0}

        for item in data:
            try:
                qualified = require_seed_key(item, "medication")
                _, action = await upsert_by_identity(
                    session,
                    MedicationCatalog,
                    qualified,
                    medication_values(item),
                    tenant_id=self.tenant_id,
                    explicit_id=item.get("id"),
                    json_fields=_JSON_FIELDS,
                )
                stats[action] += 1
            except Exception as e:
                self.note_item_error("medications", item, e)
                logger.error(f"Error seeding medication {item_label(item)}: {e}")
                stats["errors"] += 1

        await session.flush()
        return stats
