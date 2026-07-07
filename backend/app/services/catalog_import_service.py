import logging
from typing import Dict
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.enums import QuantityType
from app.schemas.biomarker import CatalogImportPayload
from app.services.concept_service import resolve_biomarker_class_concept

logger = logging.getLogger(__name__)


class CatalogImportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def fetch_catalog_from_url(self, url: str) -> CatalogImportPayload:
        """Fetch JSON catalog from a URL and parse it."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                return CatalogImportPayload.model_validate(data)
        except Exception as e:
            logger.error(f"Failed to fetch or parse catalog from {url}: {e}")
            raise ValueError(f"Failed to load catalog from URL: {str(e)}")

    async def import_catalog(self, payload: CatalogImportPayload) -> Dict[str, int]:
        """
        Import units and biomarkers from the payload into the database.
        Returns statistics about the import.
        """
        stats = {
            "units_added": 0,
            "units_updated": 0,
            "biomarkers_added": 0,
            "biomarkers_updated": 0,
        }

        # 1. Process Units
        unit_map = {}
        for unit_data in payload.units:
            try:
                # Find existing unit by symbol
                result = await self.db.execute(
                    select(Unit).where(Unit.symbol == unit_data.symbol)
                )
                existing_unit = result.scalar_one_or_none()

                q_type = QuantityType.OTHER
                try:
                    if unit_data.quantity_type:
                        q_type = QuantityType(unit_data.quantity_type.upper())
                except ValueError:
                    logger.warning(
                        f"Invalid quantity type '{unit_data.quantity_type}' for unit '{unit_data.symbol}'. Using OTHER."
                    )

                if existing_unit:
                    existing_unit.name = unit_data.name
                    existing_unit.quantity_type = q_type
                    stats["units_updated"] += 1
                    unit_map[existing_unit.symbol.lower()] = existing_unit.id
                else:
                    new_unit = Unit(
                        symbol=unit_data.symbol,
                        name=unit_data.name,
                        quantity_type=q_type,
                    )
                    self.db.add(new_unit)
                    await self.db.flush()
                    stats["units_added"] += 1
                    unit_map[new_unit.symbol.lower()] = new_unit.id
            except Exception as e:
                logger.error(f"Error processing unit {unit_data.symbol}: {e}")

        # Re-fetch all units to ensure our map is complete for biomarker mapping
        all_units_result = await self.db.execute(select(Unit))
        for u in all_units_result.scalars().all():
            unit_map[u.symbol.lower()] = u.id

        # 2. Process Biomarkers
        for bio_data in payload.biomarkers:
            try:
                # Resolve preferred unit
                pref_unit_id = None
                if bio_data.preferred_unit_symbol:
                    pref_unit_id = unit_map.get(bio_data.preferred_unit_symbol.lower())

                # Find existing by slug
                result = await self.db.execute(
                    select(BiomarkerDefinition).where(
                        BiomarkerDefinition.slug == bio_data.slug
                    )
                )
                existing_bio = result.scalar_one_or_none()

                if existing_bio:
                    existing_bio.name = bio_data.name
                    existing_bio.coding_system = bio_data.coding_system
                    existing_bio.code = bio_data.code
                    if bio_data.class_concept_id is not None:
                        existing_bio.class_concept_id = bio_data.class_concept_id
                    else:
                        existing_bio.class_concept_id = (
                            await resolve_biomarker_class_concept(
                                self.db, bio_data.category
                            )
                        )
                    existing_bio.aliases = bio_data.aliases
                    existing_bio.info = bio_data.info
                    existing_bio.reference_range_min = bio_data.reference_range_min
                    existing_bio.reference_range_max = bio_data.reference_range_max
                    if pref_unit_id:
                        existing_bio.preferred_unit_id = pref_unit_id
                    stats["biomarkers_updated"] += 1
                else:
                    class_concept_id = bio_data.class_concept_id
                    if class_concept_id is None:
                        class_concept_id = await resolve_biomarker_class_concept(
                            self.db, bio_data.category
                        )
                    new_bio = BiomarkerDefinition(
                        slug=bio_data.slug,
                        name=bio_data.name,
                        coding_system=bio_data.coding_system,
                        code=bio_data.code,
                        class_concept_id=class_concept_id,
                        aliases=bio_data.aliases,
                        info=bio_data.info,
                        reference_range_min=bio_data.reference_range_min,
                        reference_range_max=bio_data.reference_range_max,
                        preferred_unit_id=pref_unit_id,
                    )
                    self.db.add(new_bio)
                    stats["biomarkers_added"] += 1
            except Exception as e:
                logger.error(f"Error processing biomarker {bio_data.slug}: {e}")

        await self.db.commit()
        return stats
