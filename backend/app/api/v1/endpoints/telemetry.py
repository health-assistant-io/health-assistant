from fastapi import APIRouter, Depends, Query, Body, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime, timezone
from app.core.security import get_current_user
from app.core.database import get_db
from app.schemas.telemetry import TelemetrySyncPayload, TelemetryDataPoint
from app.services.telemetry_service import (
    upload_telemetry_data,
    get_telemetry_data,
    get_telemetry_summary,
)
from app.services.anomaly_detector import AnomalyDetector

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/data")
async def upload_telemetry_data_endpoint(
    payload: TelemetrySyncPayload,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Sync time-series health data from a mobile device (Health Connect / HealthKit).
    """
    try:
        uploaded = await upload_telemetry_data(db, payload.device_id, payload.points, current_user.tenant_id)
        return {"uploaded": uploaded, "device_id": payload.device_id, "message": "Sync successful"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data")
async def get_telemetry_data_endpoint(
    device_id: str,
    start_date: str,
    end_date: str,
    metrics: str = Query(None),
    current_user=Depends(get_current_user),
):
    """Get telemetry data for a device"""
    data = await get_telemetry_data(device_id, start_date, end_date, metrics)
    return {"device_id": device_id, "data": data}


@router.get("/data/summary")
async def get_telemetry_summary_endpoint(
    date: str, device_id: str = None, current_user=Depends(get_current_user)
):
    """Get daily summary for telemetry data"""
    summary = await get_telemetry_summary(date, device_id)
    return summary


@router.get("/anomalies")
async def get_telemetry_anomalies_endpoint(
    device_id: str,
    metric: str,
    period: str = Query("last-30-days"),
    current_user=Depends(get_current_user),
):
    """Detect anomalies in telemetry data"""
    detector = AnomalyDetector()
    anomalies = await detector.detect_biomarker_anomalies(device_id, metric, period)
    return {"device_id": device_id, "metric": metric, "anomalies": anomalies}
