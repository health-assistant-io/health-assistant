from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import logging
from app.models.wearable_model import WearableDataModel

logger = logging.getLogger(__name__)

async def upload_wearable_data(db: AsyncSession, device_id: str, data_points: List, tenant_id: str) -> int:
    """
    Save time-series wearable device data to the database.
    """
    try:
        records_to_insert = []
        for point in data_points:
            record = WearableDataModel(
                tenant_id=tenant_id,
                device_id=device_id,
                timestamp=point.timestamp,
                heart_rate=point.heart_rate,
                steps=point.steps,
                calories=point.calories,
                data=point.data
            )
            records_to_insert.append(record)
        
        if records_to_insert:
            db.add_all(records_to_insert)
            await db.commit()
            
        return len(records_to_insert)
    except Exception as e:
        logger.error(f"Failed to save wearable data: {e}")
        await db.rollback()
        raise

async def get_wearable_data(device_id: str, start_date: str, end_date: str, metrics: str = None) -> list:
    """Get wearable data for a device"""
    # This would query the database
    return []

async def get_wearable_summary(date: str, device_id: str = None) -> dict:
    """Get daily summary for wearable data"""
    # This would aggregate wearable data
    return {
        "date": date,
        "steps": 0,
        "calories": 0,
        "heart_rate": {"min": 0, "max": 0, "avg": 0}
    }