import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select
from app.core.config import settings
from app.models.biomarker_model import (
    Unit,
    BiomarkerDefinition,
    BiomarkerEventCorrelation,
)
from app.models.clinical_event import ClinicalEventType
from app.models.enums import QuantityType, CodingSystem
from app.services.concept_service import resolve_biomarker_class_concept

engine = create_async_engine(settings.DATABASE_URL)
LocalSession = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


async def seed_data():
    async with LocalSession() as db:
        # Seed Units
        units = [
            # Mass Concentration
            {
                "symbol": "mg/dL",
                "name": "Milligrams per deciliter",
                "quantity_type": QuantityType.MASS_CONCENTRATION,
                "conversion_multiplier": 1.0,
            },
            {
                "symbol": "g/dL",
                "name": "Grams per deciliter",
                "quantity_type": QuantityType.MASS_CONCENTRATION,
                "conversion_multiplier": 1000.0,
            },
            {
                "symbol": "g/L",
                "name": "Grams per liter",
                "quantity_type": QuantityType.MASS_CONCENTRATION,
                "conversion_multiplier": 100.0,
            },
            {
                "symbol": "µg/dL",
                "name": "Micrograms per deciliter",
                "quantity_type": QuantityType.MASS_CONCENTRATION,
                "conversion_multiplier": 0.001,
            },
            # Molar Concentration
            {
                "symbol": "mmol/L",
                "name": "Millimoles per liter",
                "quantity_type": QuantityType.MOLAR_CONCENTRATION,
                "conversion_multiplier": 1.0,
            },
            {
                "symbol": "µmol/L",
                "name": "Micromoles per liter",
                "quantity_type": QuantityType.MOLAR_CONCENTRATION,
                "conversion_multiplier": 0.001,
            },
            # Number Concentration
            {
                "symbol": "10^9/L",
                "name": "Billion cells per liter",
                "quantity_type": QuantityType.NUMBER_CONCENTRATION,
                "conversion_multiplier": 1.0,
            },
            {
                "symbol": "10^12/L",
                "name": "Trillion cells per liter",
                "quantity_type": QuantityType.NUMBER_CONCENTRATION,
                "conversion_multiplier": 1000.0,
            },
            # Others
            {
                "symbol": "%",
                "name": "Percentage",
                "quantity_type": QuantityType.PERCENTAGE,
                "conversion_multiplier": 1.0,
            },
            {
                "symbol": "mmHg",
                "name": "Millimeters of mercury",
                "quantity_type": QuantityType.PRESSURE,
                "conversion_multiplier": 1.0,
            },
            {
                "symbol": "bpm",
                "name": "Beats per minute",
                "quantity_type": QuantityType.OTHER,
                "conversion_multiplier": 1.0,
            },
            {
                "symbol": "mIU/L",
                "name": "Milli-international units per liter",
                "quantity_type": QuantityType.OTHER,
                "conversion_multiplier": 1.0,
            },
            {
                "symbol": "U/L",
                "name": "Units per liter",
                "quantity_type": QuantityType.OTHER,
                "conversion_multiplier": 1.0,
            },
            # Ophthalmic Units
            {
                "symbol": "D",
                "name": "Diopter",
                "quantity_type": QuantityType.OTHER,
                "conversion_multiplier": 1.0,
            },
            {
                "symbol": "°",
                "name": "Degrees",
                "quantity_type": QuantityType.OTHER,
                "conversion_multiplier": 1.0,
            },
        ]

        unit_map = {}
        for u_data in units:
            result = await db.execute(
                select(Unit).where(Unit.symbol == u_data["symbol"])
            )
            unit = result.scalar_one_or_none()
            if not unit:
                unit = Unit(**u_data)
                db.add(unit)
                await db.flush()
            unit_map[unit.symbol] = unit.id

        # Seed Minimal Biomarkers (User should import catalog for complete list)
        biomarkers = [
            {
                "slug": "systolic-bp",
                "coding_system": CodingSystem.LOINC,
                "code": "8480-6",
                "name": "Systolic Blood Pressure",
                "category": "vital_signs",
                "preferred_unit_symbol": "mmHg",
                "aliases": [
                    "Systolic BP",
                    "Systolic",
                    "SBP",
                    "Systolic Blood Pressure",
                ],
            },
            {
                "slug": "diastolic-bp",
                "coding_system": CodingSystem.LOINC,
                "code": "8462-4",
                "name": "Diastolic Blood Pressure",
                "category": "vital_signs",
                "preferred_unit_symbol": "mmHg",
                "aliases": [
                    "Diastolic BP",
                    "Diastolic",
                    "DBP",
                    "Diastolic Blood Pressure",
                ],
            },
            {
                "slug": "heart-rate",
                "coding_system": CodingSystem.LOINC,
                "code": "8867-4",
                "name": "Heart Rate",
                "category": "vital_signs",
                "preferred_unit_symbol": "bpm",
                "aliases": ["Pulse", "HR"],
                "is_telemetry": True,
            },
            {
                "slug": "oxygen-saturation",
                "coding_system": CodingSystem.LOINC,
                "code": "2708-6",
                "name": "Oxygen Saturation",
                "category": "vital_signs",
                "preferred_unit_symbol": "%",
                "aliases": ["SpO2", "O2 Sat"],
                "is_telemetry": True,
            },
            {
                "slug": "steps",
                "coding_system": CodingSystem.LOINC,
                "code": "41950-7",
                "name": "Step Count",
                "category": "activity",
                "aliases": ["Steps"],
                "is_telemetry": True,
            },
            {
                "slug": "calories-burned",
                "coding_system": CodingSystem.LOINC,
                "code": "41979-6",
                "name": "Calories Burned",
                "category": "activity",
                "aliases": ["Active Energy", "Calories"],
                "is_telemetry": True,
            },
        ]

        bio_map = {}
        for b_data in biomarkers:
            pref_sym = b_data.pop("preferred_unit_symbol", None)
            if pref_sym and pref_sym in unit_map:
                b_data["preferred_unit_id"] = unit_map[pref_sym]

            # Resolve legacy ``category`` (e.g. "vital_signs") to a
            # ``biomarker_class`` concept ID; pop it off so the dict matches
            # the new BiomarkerDefinition columns.
            legacy_category = b_data.pop("category", None)
            b_data["class_concept_id"] = await resolve_biomarker_class_concept(
                db, legacy_category
            )

            result = await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.slug == b_data["slug"]
                )
            )
            bio = result.scalar_one_or_none()
            if not bio:
                bio = BiomarkerDefinition(**b_data)
                db.add(bio)
                await db.flush()
            bio_map[bio.slug] = bio.id

        # Seed Correlations
        correlations = [
            {
                "event_type_slug": "pain-episode",
                "biomarker_slugs": ["systolic-bp", "diastolic-bp"],
            },
        ]

        for corr_data in correlations:
            # Get event type
            result = await db.execute(
                select(ClinicalEventType).where(
                    ClinicalEventType.slug == corr_data["event_type_slug"]
                )
            )
            event_type = result.scalar_one_or_none()
            if not event_type:
                continue

            for b_slug in corr_data["biomarker_slugs"]:
                if b_slug in bio_map:
                    # check if correlation exists
                    corr_result = await db.execute(
                        select(BiomarkerEventCorrelation).where(
                            BiomarkerEventCorrelation.event_type_id == event_type.id,
                            BiomarkerEventCorrelation.biomarker_id == bio_map[b_slug],
                        )
                    )
                    if not corr_result.scalar_one_or_none():
                        correlation = BiomarkerEventCorrelation(
                            event_type_id=event_type.id,
                            biomarker_id=bio_map[b_slug],
                            correlation_type="monitoring",
                        )
                        db.add(correlation)

        await db.commit()
        print("Successfully seeded Units, Biomarkers, and Correlations.")


if __name__ == "__main__":
    asyncio.run(seed_data())
