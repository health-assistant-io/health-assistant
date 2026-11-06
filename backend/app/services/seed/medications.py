"""Medication catalog seeding."""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.fhir.medication import MedicationCatalog

logger = logging.getLogger(__name__)


class MedicationSeedMixin:
    async def seed_medications(self, session: AsyncSession = None) -> dict[str, int]:
        """
        Sync medications from JSON to Database.
        Returns a summary of changes.
        """
        file_path = self.seeds_dir / "medications.json"
        if not file_path.exists():
            logger.warning(f"Medication seed file not found: {file_path}")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        try:
            with open(file_path) as f:
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

    async def _process_medications(
        self, session: AsyncSession, data: list[dict[str, Any]]
    ) -> dict[str, int]:
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
                        created_at=datetime.now(UTC),
                    )
                    session.add(new_med)
                    stats["added"] += 1
            except Exception as e:
                logger.error(f"Error seeding medication {item.get('name')}: {e}")
                stats["errors"] += 1

        return stats
