import asyncio
import sys
import os
import logging

# Add backend to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.core.database import AsyncSessionLocal
from app.models.telemetry_model import TelemetryDataModel

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def run_deletion():
    async with AsyncSessionLocal() as db:
        logger.info("Starting deletion of migrated FHIR records in TimescaleDB...")
        
        # Batch size
        batch_size = 10000
        total_deleted = 0
        
        while True:
            # Fetch a batch of records specifically tagged as "fhir_migration"
            # Note: We fetch IDs first instead of a pure DELETE ... RETURNING because 
            # some TimescaleDB hypertable setups prefer explicit ID chunking for large deletes
            records_res = await db.execute(
                select(TelemetryDataModel.id)
                .where(TelemetryDataModel.device_id == 'fhir_migration')
                .limit(batch_size)
            )
            record_ids = records_res.scalars().all()
            
            if not record_ids:
                break
                
            # Delete the batch
            await db.execute(
                delete(TelemetryDataModel)
                .where(TelemetryDataModel.id.in_(record_ids))
            )
            await db.commit()
            
            total_deleted += len(record_ids)
            logger.info(f"Deleted {total_deleted} migrated records from TimescaleDB so far...")
            
        logger.info(f"Successfully finished deleting {total_deleted} total records. TimescaleDB is now clean of 'fhir_migration' data.")

if __name__ == "__main__":
    asyncio.run(run_deletion())
