"""Ontology generation for previously-unknown biomarkers and medications.

Extracted from ``MedicalProcessingService`` (Phase 5b). These handlers run
NLP Pass 2 — generating full catalog definitions for entities the Pass-1
extractor couldn't match to the existing catalog — and persist them so the
catalog auto-expands. ``MedicalProcessingService`` keeps thin delegate methods
(``_process_unknown_biomarkers`` / ``_process_unknown_medications``) that
forward to the functions here.
"""
import logging
from typing import Any, Dict

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.enums import QuantityType
from app.models.fhir.medication import MedicationCatalog

logger = logging.getLogger(__name__)


async def process_unknown_biomarkers(
    db: AsyncSession, unknown_bios, nlp_extractor, tenant_id, slug_map: Dict[str, Any]
) -> None:
    new_bio_defs = await nlp_extractor.parse_document_pass_2_biomarkers(unknown_bios)

    # Pre-fetch units
    unit_res = await db.execute(select(Unit))
    unit_map = {u.symbol.lower(): u for u in unit_res.scalars().all()}

    for def_data in new_bio_defs.definitions:
        slug_map[def_data.raw_name_match] = def_data.proposed_slug

        existing = await db.execute(
            select(BiomarkerDefinition).where(
                BiomarkerDefinition.slug == def_data.proposed_slug
            )
        )
        if not existing.scalar_one_or_none():
            preferred_unit_id = None
            if def_data.preferred_unit_symbol:
                u_lower = def_data.preferred_unit_symbol.lower()
                if u_lower in unit_map:
                    preferred_unit_id = unit_map[u_lower].id
                else:
                    new_unit = Unit(
                        symbol=def_data.preferred_unit_symbol,
                        name=def_data.preferred_unit_symbol,
                        quantity_type=QuantityType.OTHER,
                        conversion_multiplier=1.0,
                    )
                    db.add(new_unit)
                    await db.flush()
                    unit_map[u_lower] = new_unit
                    preferred_unit_id = new_unit.id

            db.add(
                BiomarkerDefinition(
                    slug=def_data.proposed_slug,
                    coding_system=def_data.proposed_coding_system,
                    code=def_data.proposed_code,
                    name=def_data.name,
                    category=def_data.category,
                    aliases=def_data.suggested_aliases,
                    reference_range_min=def_data.reference_range_min,
                    reference_range_max=def_data.reference_range_max,
                    preferred_unit_id=preferred_unit_id,
                    info=def_data.info,
                    is_telemetry=def_data.is_telemetry,
                    tenant_id=tenant_id,
                )
            )


async def process_unknown_medications(
    db: AsyncSession, unknown_meds, nlp_extractor, tenant_id, name_map: Dict[str, Any]
) -> None:
    new_med_defs = await nlp_extractor.parse_document_pass_2_medications(unknown_meds)
    for def_data in new_med_defs.definitions:
        name_map[def_data.raw_name_match] = def_data.name
        existing = await db.execute(
            select(MedicationCatalog).where(
                func.lower(MedicationCatalog.name) == func.lower(def_data.name)
            )
        )
        if not existing.scalar_one_or_none():
            db.add(
                MedicationCatalog(
                    name=def_data.name,
                    description=def_data.description,
                    indications=def_data.indications,
                    side_effects=def_data.side_effects,
                    contraindications=def_data.contraindications,
                    dosage_info=def_data.dosage_info,
                    tenant_id=tenant_id,
                )
            )
