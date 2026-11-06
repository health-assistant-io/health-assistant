"""Vaccine catalog seeding."""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class VaccineSeedMixin:
    async def seed_vaccines(self, session: AsyncSession = None) -> dict[str, int]:
        """Sync CVX-coded vaccine reference definitions from JSON (Phase 5).

        Upserts by ``slug`` (global, ``tenant_id=None``). ``PREVENTS`` edges to
        disease concepts are seeded separately in Phase 6.
        """
        file_path = self.seeds_dir / "vaccines.json"
        if not file_path.exists():
            logger.warning(f"Vaccine seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}
        try:
            with open(file_path) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load vaccine seeds: {e}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 1}
        if session:
            return await self._process_vaccines(session, data)
        async with AsyncSessionLocal() as new_session:
            result = await self._process_vaccines(new_session, data)
            await new_session.commit()
            return result

    async def _process_vaccines(
        self, session: AsyncSession, data: list[dict[str, Any]]
    ) -> dict[str, int]:
        from app.models.fhir.vaccine import VaccineCatalog

        stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0}
        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "vaccine_catalog"):
            logger.error("Table 'vaccine_catalog' does not exist. Skipping vaccines.")
            return {"added": 0, "updated": 0, "skipped": len(data), "errors": 0}
        for item in data:
            try:
                stmt = select(VaccineCatalog).where(
                    VaccineCatalog.slug == item["slug"],
                    VaccineCatalog.tenant_id.is_(None),
                )
                db_vac = (await session.execute(stmt)).scalar_one_or_none()
                if db_vac:
                    db_vac.name = item.get("name", db_vac.name)
                    db_vac.description = item.get("description", db_vac.description)
                    db_vac.code = item.get("code", db_vac.code)
                    db_vac.coding_system = item.get("coding_system", db_vac.coding_system)
                    db_vac.target_diseases = item.get("target_diseases", db_vac.target_diseases)
                    db_vac.dose_schedule = item.get("dose_schedule", db_vac.dose_schedule)
                    db_vac.contraindications = item.get(
                        "contraindications", db_vac.contraindications
                    )
                    db_vac.side_effects = item.get("side_effects", db_vac.side_effects)
                    stats["updated"] += 1
                else:
                    new_vac = VaccineCatalog(
                        id=uuid4(),
                        slug=item["slug"],
                        name=item["name"],
                        description=item.get("description"),
                        coding_system=item.get("coding_system", "cvx"),
                        code=item.get("code"),
                        target_diseases=item.get("target_diseases"),
                        dose_schedule=item.get("dose_schedule"),
                        contraindications=item.get("contraindications"),
                        side_effects=item.get("side_effects"),
                        tenant_id=None,  # system-wide
                        created_at=datetime.now(UTC),
                    )
                    session.add(new_vac)
                    stats["added"] += 1
            except Exception as e:
                logger.error(f"Error seeding vaccine {item.get('slug')}: {e}")
                stats["errors"] += 1
        return stats
