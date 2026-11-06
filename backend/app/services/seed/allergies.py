"""Allergy catalog seeding."""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.fhir.allergy import AllergyCatalog

logger = logging.getLogger(__name__)


class AllergySeedMixin:
    async def seed_allergies(self, session: AsyncSession = None) -> dict[str, int]:
        """
        Sync allergies from JSON to Database.
        """
        file_path = self.seeds_dir / "allergies.json"
        if not file_path.exists():
            logger.warning(f"Allergy seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path) as f:
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
        self, session: AsyncSession, data: list[dict[str, Any]]
    ) -> dict[str, int]:
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
                    db_allergy.description = item.get("description", db_allergy.description)
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
                        created_at=datetime.now(UTC),
                    )
                    session.add(new_allergy)
                    stats["added"] += 1
            except Exception as e:
                logger.error(f"Error seeding allergy {item.get('name')}: {e}")
                stats["errors"] += 1

        return stats
