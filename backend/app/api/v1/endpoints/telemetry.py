import logging

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.core.database import get_db
from app.schemas.telemetry import TelemetrySyncPayload
from app.schemas.user import TokenData
from app.services.telemetry_service import (
    get_telemetry_anomalies,
    get_telemetry_data,
    get_telemetry_summary,
    upload_telemetry_data,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/data")
async def upload_telemetry_data_endpoint(
    payload: TelemetrySyncPayload,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sync time-series health data from a mobile device (Health Connect / HealthKit)."""
    try:
        uploaded = await upload_telemetry_data(
            db, payload.device_id, payload.points, current_user.tenant_id
        )
        return {
            "uploaded": uploaded,
            "device_id": payload.device_id,
            "message": "Sync successful",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("Telemetry upload failed")
        raise HTTPException(status_code=500, detail="Telemetry upload failed")


@router.get("/data")
async def get_telemetry_data_endpoint(
    device_id: str,
    start_date: str,
    end_date: str,
    metrics: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get telemetry data for a device (tenant-scoped).

    The caller can only ever read data belonging to their own tenant
    regardless of which device_id they pass (audit B3).
    """
    data = await get_telemetry_data(
        db,
        tenant_id=current_user.tenant_id,
        device_id=device_id,
        start_date=start_date,
        end_date=end_date,
        metrics=metrics,
    )
    return {"device_id": device_id, "data": data}


@router.get("/data/summary")
async def get_telemetry_summary_endpoint(
    date: str,
    device_id: str = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get daily summary for telemetry data (tenant-scoped, audit B3)."""
    summary = await get_telemetry_summary(
        db,
        tenant_id=current_user.tenant_id,
        target_date=date,
        device_id=device_id,
    )
    return summary


@router.get("/anomalies")
async def get_telemetry_anomalies_endpoint(
    device_id: str,
    metric: str,
    period_days: int = Query(30, ge=1, le=365),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detect anomalies in a device's telemetry stream (tenant-scoped).

    Replaces the broken implementation that called
    ``await AnomalyDetector().detect_biomarker_anomalies(device_id, metric,
    period)`` — wrong arity and on a synchronous function (audit A6).
    """
    anomalies = await get_telemetry_anomalies(
        db,
        tenant_id=current_user.tenant_id,
        device_id=device_id,
        metric=metric,
        period_days=period_days,
    )
    return {"device_id": device_id, "metric": metric, "anomalies": anomalies}
