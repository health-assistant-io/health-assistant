"""Telemetry service.

Provides tenant-scoped CRUD over the TimescaleDB ``telemetry_data`` hypertable.
All read paths take ``tenant_id`` explicitly so a caller cannot read another
tenant's data even if they guess a ``device_id`` (audit items B3, F8).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telemetry_model import TelemetryDataModel
from app.services.anomaly_detector import AnomalyDetector

logger = logging.getLogger(__name__)


# Mapping from request ``metric`` name to the TelemetryDataModel column used
# for that metric. Used by get_telemetry_data / get_telemetry_anomalies.
_METRIC_COLUMNS = {
    "heart_rate": "heart_rate",
    "heart-rate": "heart_rate",
    "hr": "heart_rate",
    "steps": "steps",
    "calories": "calories",
    "active_calories": "calories",
    "active-calories": "calories",
}


def _column_for(metric: str) -> Optional[str]:
    """Resolve a request metric alias to the ORM column name."""
    if not metric:
        return None
    return _METRIC_COLUMNS.get(metric.strip().lower())


def _coerce_uuid(value: str | UUID) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_iso_date(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 string into a timezone-aware datetime.

    Accepts both full datetime (``2026-06-21T10:00:00Z``) and date-only
    (``2026-06-21``) inputs. Returns None on parse failure.
    """
    if not value:
        return None
    try:
        # Try full datetime first
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


async def upload_telemetry_data(
    db: AsyncSession,
    device_id: str,
    data_points: Sequence[Any],
    tenant_id: str | UUID,
) -> int:
    """Save time-series telemetry device data to the database."""
    tenant_uuid = _coerce_uuid(tenant_id)
    if tenant_uuid is None:
        raise ValueError(f"Invalid tenant_id: {tenant_id!r}")

    try:
        records = [
            TelemetryDataModel(
                tenant_id=tenant_uuid,
                device_id=device_id,
                timestamp=point.timestamp,
                heart_rate=getattr(point, "heart_rate", None),
                steps=getattr(point, "steps", None),
                calories=getattr(point, "calories", None),
                data=getattr(point, "data", None),
            )
            for point in data_points
        ]
        if records:
            db.add_all(records)
            await db.commit()
        return len(records)
    except Exception as e:
        logger.error("Failed to save telemetry data: %s", e)
        await db.rollback()
        raise


async def get_telemetry_data(
    db: AsyncSession,
    tenant_id: str | UUID,
    device_id: str,
    start_date: str,
    end_date: str,
    metrics: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read raw telemetry rows for a device, scoped to the caller's tenant.

    Replaces the previous stub (audit F8) and adds tenant scoping (audit B3).
    """
    tenant_uuid = _coerce_uuid(tenant_id)
    if tenant_uuid is None:
        return []

    start_dt = _parse_iso_date(start_date)
    end_dt = _parse_iso_date(end_date)
    if start_dt is None or end_dt is None:
        return []

    query = (
        select(TelemetryDataModel)
        .where(
            TelemetryDataModel.tenant_id == tenant_uuid,
            TelemetryDataModel.device_id == device_id,
            TelemetryDataModel.timestamp >= start_dt,
            TelemetryDataModel.timestamp <= end_dt,
        )
        .order_by(TelemetryDataModel.timestamp.asc())
    )
    result = await db.execute(query)
    rows = result.scalars().all()

    # If the caller asked for specific metrics, filter the JSONB ``data``
    # payload to just those keys (the dedicated columns are always returned).
    metric_filter = {m.strip() for m in metrics.split(",")} if metrics else None

    out: List[Dict[str, Any]] = []
    for row in rows:
        d = row.to_dict()
        if metric_filter and isinstance(d.get("data"), dict):
            d["data"] = {k: v for k, v in d["data"].items() if k in metric_filter}
        out.append(d)
    return out


async def get_telemetry_summary(
    db: AsyncSession,
    tenant_id: str | UUID,
    target_date: str,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate daily stats (min/max/avg/sum) for a tenant's telemetry.

    Replaces the previous zero-stub (audit F8) and adds tenant scoping (B3).
    """
    tenant_uuid = _coerce_uuid(tenant_id)
    if tenant_uuid is None:
        return {
            "date": target_date,
            "device_id": device_id,
            "steps": 0,
            "calories": 0,
            "heart_rate": {"min": None, "max": None, "avg": None},
        }

    day = _parse_iso_date(target_date)
    if day is None:
        return {
            "date": target_date,
            "device_id": device_id,
            "steps": 0,
            "calories": 0,
            "heart_rate": {"min": None, "max": None, "avg": None},
        }
    next_day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta

    next_day = next_day + timedelta(days=1)

    query = (
        select(
            func.min(TelemetryDataModel.heart_rate).label("hr_min"),
            func.max(TelemetryDataModel.heart_rate).label("hr_max"),
            func.avg(TelemetryDataModel.heart_rate).label("hr_avg"),
            func.sum(TelemetryDataModel.steps).label("steps_sum"),
            func.sum(TelemetryDataModel.calories).label("cal_sum"),
        )
        .where(
            TelemetryDataModel.tenant_id == tenant_uuid,
            TelemetryDataModel.timestamp >= day,
            TelemetryDataModel.timestamp < next_day,
        )
    )
    if device_id:
        query = query.where(TelemetryDataModel.device_id == device_id)

    result = await db.execute(query)
    row = result.one()

    def _f(v):
        return float(v) if v is not None else None

    return {
        "date": target_date,
        "device_id": device_id,
        "steps": int(row.steps_sum or 0),
        "calories": _f(row.cal_sum),
        "heart_rate": {
            "min": _f(row.hr_min),
            "max": _f(row.hr_max),
            "avg": _f(row.hr_avg),
        },
    }


async def get_telemetry_anomalies(
    db: AsyncSession,
    tenant_id: str | UUID,
    device_id: str,
    metric: str,
    period_days: int = 30,
) -> List[Dict[str, Any]]:
    """Detect anomalies in a device's telemetry stream.

    Pulls historical values for the requested metric from the tenant-scoped
    hypertable, then runs the (synchronous) ``AnomalyDetector`` against the
    most recent value as the ``new_value`` and everything earlier as
    ``historical_values``.

    Replaces the broken endpoint at ``telemetry.py:65`` that called
    ``await detector.detect_biomarker_anomalies(device_id, metric, period)``
    with the wrong arity and on a synchronous function (audit A6). Tenant
    scoping (audit B3) is enforced via the query.
    """
    tenant_uuid = _coerce_uuid(tenant_id)
    if tenant_uuid is None:
        return []

    column_name = _column_for(metric)
    if column_name is None:
        return []

    column = getattr(TelemetryDataModel, column_name)

    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
    query = (
        select(TelemetryDataModel.timestamp, column)
        .where(
            TelemetryDataModel.tenant_id == tenant_uuid,
            TelemetryDataModel.device_id == device_id,
            column.is_not(None),
            TelemetryDataModel.timestamp >= cutoff,
        )
        .order_by(TelemetryDataModel.timestamp.asc())
    )
    result = await db.execute(query)
    rows = result.all()

    if len(rows) < 2:
        return []

    historical = [{"value": float(value)} for _, value in rows[:-1]]
    new_value = {"value": float(rows[-1][1])}

    detector = AnomalyDetector()
    return detector.detect_biomarker_anomalies(historical, new_value)
