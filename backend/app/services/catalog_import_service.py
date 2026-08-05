import logging
from typing import Dict, Optional
from uuid import UUID
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.biomarker_model import (
    BiomarkerAllowedState,
    BiomarkerDefinition,
    BiomarkerReferenceRange,
    BiomarkerState,
    Unit,
)
from app.models.enums import ConceptKind, QuantityType
from app.schemas.biomarker import CatalogImportPayload
from app.services.concept_service import (
    resolve_biomarker_class_concept,
    resolve_concept_by_slug,
)

logger = logging.getLogger(__name__)


class CatalogImportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _upsert_reference_ranges(self, biomarker_id: UUID, desired) -> None:
        """Idempotently upsert stratified reference ranges for a biomarker.

        Matches an existing row by its stratification key (sex, age_min,
        age_max, unit_id) and updates the bounds; inserts when no match.
        **Upsert-only** — rows not present in ``desired`` are left untouched,
        so user-added ranges survive a re-seed/re-import.
        """
        if not desired:
            return
        existing = (
            await self.db.execute(
                select(BiomarkerReferenceRange).where(
                    BiomarkerReferenceRange.biomarker_id == biomarker_id
                )
            )
        ).scalars().all()

        def _key(rr):
            return (
                getattr(rr, "sex", None),
                getattr(rr, "age_min", None),
                getattr(rr, "age_max", None),
                str(getattr(rr, "unit_id", None)) if getattr(rr, "unit_id", None) else None,
            )

        by_key = {}
        for rr in existing:
            by_key[_key(rr)] = rr

        for want in desired:
            k = _key(want)
            low = getattr(want, "low", None)
            high = getattr(want, "high", None)
            text = getattr(want, "text", None)
            applies_to = getattr(want, "applies_to", None)
            sex = getattr(want, "sex", None)
            age_min = getattr(want, "age_min", None)
            age_max = getattr(want, "age_max", None)
            unit_id = getattr(want, "unit_id", None)
            match = by_key.get(k)
            if match is not None:
                match.low = low
                match.high = high
                match.text = text
                match.applies_to = applies_to
            else:
                self.db.add(
                    BiomarkerReferenceRange(
                        biomarker_id=biomarker_id,
                        sex=sex,
                        age_min=age_min,
                        age_max=age_max,
                        unit_id=unit_id,
                        low=low,
                        high=high,
                        text=text,
                        applies_to=applies_to,
                    )
                )

    async def _upsert_allowed_states(self, biomarker_id: UUID, desired) -> None:
        """Idempotently upsert allowed_states for a STATE biomarker.

        ``desired`` is a list of ``AllowedStateSpec`` (state_slug, is_normal,
        sort_order). Slugs are resolved against the universal
        ``biomarker_states`` catalog. Replaces the existing set atomically
        (delete-then-insert) so the JSON is the single source of truth.
        """
        if not desired:
            return
        # Resolve slugs → state_ids.
        slugs = [getattr(s, "state_slug", None) or s.get("state_slug") for s in desired]
        slugs = [s for s in slugs if s]
        if not slugs:
            return
        state_rows = (
            (
                await self.db.execute(
                    select(BiomarkerState).where(BiomarkerState.slug.in_(slugs))
                )
            )
            .scalars()
            .all()
        )
        by_slug = {r.slug: r for r in state_rows}
        # Wipe existing and re-insert (atomic replace).
        from sqlalchemy import delete as sa_delete
        await self.db.execute(
            sa_delete(BiomarkerAllowedState).where(
                BiomarkerAllowedState.biomarker_id == biomarker_id
            )
        )
        for spec in desired:
            slug = getattr(spec, "state_slug", None) or spec.get("state_slug")
            state = by_slug.get(slug)
            if not state:
                logger.warning(
                    "Catalog import: unknown biomarker_state slug %r — skipping", slug
                )
                continue
            is_normal = getattr(spec, "is_normal", None)
            if is_normal is None:
                is_normal = spec.get("is_normal", False)
            sort_order = getattr(spec, "sort_order", None)
            if sort_order is None:
                sort_order = spec.get("sort_order", 0)
            self.db.add(
                BiomarkerAllowedState(
                    biomarker_id=biomarker_id,
                    state_id=state.id,
                    is_normal=bool(is_normal),
                    sort_order=int(sort_order),
                )
            )

    async def _resolve_class_concept(self, bio_data) -> Optional[UUID]:
        """Resolve a biomarker's class concept, preferring the explicit slug
        (the backup export path emits the concept slug, which round-trips
        cleanly) and falling back to the legacy ``category`` name→slug
        translation (the ontology-catalog-URL path, whose JSON uses the
        underscore convention ``blood_laboratory``)."""
        slug = getattr(bio_data, "class_concept_slug", None)
        if slug:
            cid = await resolve_concept_by_slug(
                self.db, slug, ConceptKind.BIOMARKER_CLASS
            )
            if cid:
                return cid
        return await resolve_biomarker_class_concept(self.db, bio_data.category)

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
                            await self._resolve_class_concept(bio_data)
                        )
                    existing_bio.aliases = bio_data.aliases
                    existing_bio.info = bio_data.info
                    existing_bio.reference_range_min = bio_data.reference_range_min
                    existing_bio.reference_range_max = bio_data.reference_range_max
                    if pref_unit_id:
                        existing_bio.preferred_unit_id = pref_unit_id
                    # State biomarker discriminator (plan Step 4/6).
                    if bio_data.value_type:
                        existing_bio.value_type = bio_data.value_type
                        existing_bio.supports_multi_state = (
                            bio_data.supports_multi_state or False
                        )
                    await self._upsert_reference_ranges(
                        existing_bio.id, getattr(bio_data, "reference_ranges", None)
                    )
                    await self._upsert_allowed_states(
                        existing_bio.id, getattr(bio_data, "allowed_states", None)
                    )
                    stats["biomarkers_updated"] += 1
                else:
                    class_concept_id = bio_data.class_concept_id
                    if class_concept_id is None:
                        class_concept_id = await self._resolve_class_concept(bio_data)
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
                        value_type=bio_data.value_type,
                        supports_multi_state=bio_data.supports_multi_state or False,
                    )
                    self.db.add(new_bio)
                    await self.db.flush()  # populate new_bio.id for the FK
                    await self._upsert_reference_ranges(
                        new_bio.id, getattr(bio_data, "reference_ranges", None)
                    )
                    await self._upsert_allowed_states(
                        new_bio.id, getattr(bio_data, "allowed_states", None)
                    )
                    stats["biomarkers_added"] += 1
            except Exception as e:
                logger.error(f"Error processing biomarker {bio_data.slug}: {e}")

        await self.db.commit()
        return stats
