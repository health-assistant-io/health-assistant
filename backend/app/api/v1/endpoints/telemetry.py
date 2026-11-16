"""Telemetry ingest endpoint.

The analytical reads live under ``/api/v1/analytics`` (the one web-facing
surface); the bridge reads through its schema-pinned sub-API. This router is
the single ingest seam: long-format device points in, validated/canonicalized
rows out.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.patient_access_grant import GrantPermission
from app.schemas.telemetry import TelemetrySyncPayload
from app.schemas.user import TokenData
from app.services.auth.access import check_patient_access
from app.services.telemetry.telemetry_service import upload_telemetry_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/data")
async def upload_telemetry_data_endpoint(
    payload: TelemetrySyncPayload,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sync time-series health data from a mobile device (Health Connect / HealthKit)."""
    for patient_id in {point.patient_id for point in payload.points}:
        await check_patient_access(patient_id, current_user, db, required=GrantPermission.WRITE)

    try:
        uploaded = await upload_telemetry_data(
            db,
            payload.device_id,
            payload.points,
            current_user.tenant_id,
            source=payload.source,
            device_label=payload.device_label,
        )
        return {
            "uploaded": uploaded,
            "device_id": payload.device_id,
            "message": "Sync successful",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception:
        logger.exception("Telemetry upload failed")
        raise HTTPException(status_code=500, detail="Telemetry upload failed") from None
