import asyncio
import sys
import os
import logging

# Add backend to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.core.database import AsyncSessionLocal
from app.models.fhir.patient import Observation
from app.models.biomarker_model import BiomarkerDefinition
from app.models.telemetry_model import TelemetryDataModel

# Configure logging to see progress
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def run_migration():
    async with AsyncSessionLocal() as db:
        # Get heart rate definition
        result = await db.execute(select(BiomarkerDefinition).where(BiomarkerDefinition.slug == 'heart-rate'))
        db_biomarker = result.scalar_one_or_none()
        
        if not db_biomarker:
            logger.error("Heart rate biomarker not found.")
            return

        logger.info(f"Found biomarker {db_biomarker.id}")
        
        # Batch size
        batch_size = 5000
        total_migrated = 0
        
        while True:
            # Fetch a batch of observations
            obs_res = await db.execute(
                select(Observation)
                .where(Observation.biomarker_id == db_biomarker.id)
                .limit(batch_size)
            )
            observations = obs_res.scalars().all()
            
            if not observations:
                break
                
            telemetry_records = []
            obs_ids_to_delete = []
            
            for obs in observations:
                slug = db_biomarker.slug.lower() if db_biomarker.slug else ""
                val = getattr(obs, "normalized_value", None) or getattr(obs, "raw_value", None) or (obs.value_quantity.get("value") if getattr(obs, "value_quantity", None) else None)
                
                hr = val if slug == "8867-4" or "heart-rate" in slug else None
                steps = val if slug == "41950-7" or "steps" in slug else None
                cal = val if "calories" in slug else None
                
                data_payload = {}
                if not hr and not steps and not cal:
                    data_payload[slug] = val
                    data_payload[f"{slug}_unit"] = obs.value_quantity.get("unit", "") if getattr(obs, "value_quantity", None) else ""

                telemetry_records.append(TelemetryDataModel(
                    tenant_id=obs.tenant_id,
                    device_id="fhir_migration",
                    timestamp=obs.effective_datetime,
                    heart_rate=hr,
                    steps=steps,
                    calories=cal,
                    data=data_payload if data_payload else None
                ))
                obs_ids_to_delete.append(obs.id)
            
            if telemetry_records:
                db.add_all(telemetry_records)
                await db.execute(delete(Observation).where(Observation.id.in_(obs_ids_to_delete)))
                await db.commit()
                
                total_migrated += len(observations)
                logger.info(f"Migrated {total_migrated} records so far...")
            
        logger.info(f"Successfully finished migrating {total_migrated} total records.")

if __name__ == "__main__":
    asyncio.run(run_migration())
