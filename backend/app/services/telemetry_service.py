"""Telemetry service.

Long-format hypertable CRUD over the TimescaleDB ``telemetry_data`` table.
All read paths take ``tenant_id`` explicitly so a caller cannot read another
tenant's data even if they guess a ``device_id`` (audit items B3, F8).

The hypertable stores one row per ``(timestamp, device, slug)`` triple — no
metric-specific columns, no JSONB catch-all. Every consumer (this service,
``analytics_service``, the continuous aggregates) treats ``slug`` as the
uniform metric discriminator.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telemetry_model import TelemetryDataModel
from app.services.anomaly_detector import AnomalyDetector

logger = logging.getLogger(__name__)

# Core bulk-insert chunk size. Balances insert throughput against single-
# statement memory / WAL pressure.
_UPLOAD_CHUNK_SIZE = 5000


def _coerce_uuid(value: str | UUID | None) -> Optional[UUID]:
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


def _parse_slug_list(metrics: Optional[str]) -> Optional[set]:
    """Turn a comma-separated ``metrics`` query param into a slug set.

    Returns ``None`` when there's nothing to filter on (caller omits the
    predicate entirely).
    """
    if not metrics:
        return None
    slugs = {m.strip() for m in metrics.split(",") if m.strip()}
    return slugs or None


async def upload_telemetry_data(
    db: AsyncSession,
    device_id: str,
    data_points: Sequence[Any],
    tenant_id: str | UUID,
) -> int:
    """Bulk-insert long-format telemetry rows via SQLAlchemy Core.

    Chunks at ``_UPLOAD_CHUNK_SIZE`` rows and uses
    ``ON CONFLICT (id, timestamp) DO NOTHING`` so a retried chunk (e.g. after
    a client-side timeout on a partially-committed batch) doesn't abort the
    whole upload. Returns the number of rows inserted.
    """
    tenant_uuid = _coerce_uuid(tenant_id)
    if tenant_uuid is None:
        raise ValueError(f"Invalid tenant_id: {tenant_id!r}")

    rows: List[Dict[str, Any]] = []
    for point in data_points:
        patient_uuid = _coerce_uuid(getattr(point, "patient_id", None))
        rows.append(
            {
                "tenant_id": tenant_uuid,
                "device_id": device_id,
                "timestamp": point.timestamp,
                "slug": point.slug,
                "value": point.value,
                "unit": getattr(point, "unit", None),
                "patient_id": patient_uuid,
            }
        )

    if not rows:
        return 0

    table = TelemetryDataModel.__table__
    total = 0
    try:
        for i in range(0, len(rows), _UPLOAD_CHUNK_SIZE):
            chunk = rows[i : i + _UPLOAD_CHUNK_SIZE]
            stmt = (
                pg_insert(table)
                .values(chunk)
                .on_conflict_do_nothing(index_elements=["id", "timestamp"])
            )
            result = await db.execute(stmt)
            if result.rowcount:
                total += result.rowcount
        await db.commit()
        return total
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

    ``metrics`` is an optional comma-separated list of biomarker slugs; when
    present, only rows matching one of those slugs are returned.
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

    slugs = _parse_slug_list(metrics)
    if slugs:
        query = query.where(TelemetryDataModel.slug.in_(slugs))

    result = await db.execute(query)
    return [row.to_dict() for row in result.scalars().all()]


async def get_telemetry_summary(
    db: AsyncSession,
    tenant_id: str | UUID,
    target_date: str,
    device_id: Optional[str] = None,
    metrics: Optional[str] = None,
) -> Dict[str, Any]:
    """Generic per-slug daily aggregates: ``{slug: {min,max,avg,sum,count}}``.

    Groups by ``slug`` so every telemetry biomarker is covered without
    service-layer branching. ``metrics`` optionally filters the slugs
    included in the response.
    """
    empty = {"date": target_date, "device_id": device_id, "metrics": {}}
    tenant_uuid = _coerce_uuid(tenant_id)
    if tenant_uuid is None:
        return empty

    day = _parse_iso_date(target_date)
    if day is None:
        return empty
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    query = (
        select(
            TelemetryDataModel.slug.label("slug"),
            func.min(TelemetryDataModel.value).label("mn"),
            func.max(TelemetryDataModel.value).label("mx"),
            func.avg(TelemetryDataModel.value).label("avg"),
            func.sum(TelemetryDataModel.value).label("sm"),
            func.count(TelemetryDataModel.value).label("cnt"),
        )
        .where(
            TelemetryDataModel.tenant_id == tenant_uuid,
            TelemetryDataModel.timestamp >= day_start,
            TelemetryDataModel.timestamp < day_end,
        )
        .group_by(TelemetryDataModel.slug)
    )
    if device_id:
        query = query.where(TelemetryDataModel.device_id == device_id)

    slugs = _parse_slug_list(metrics)
    if slugs:
        query = query.where(TelemetryDataModel.slug.in_(slugs))

    result = await db.execute(query)

    def _f(v):
        return float(v) if v is not None else None

    out_metrics: Dict[str, Dict[str, Any]] = {}
    for row in result.all():
        out_metrics[row.slug] = {
            "min": _f(row.mn),
            "max": _f(row.mx),
            "avg": _f(row.avg),
            "sum": _f(row.sm),
            "count": int(row.cnt) if row.cnt is not None else 0,
        }

    return {"date": target_date, "device_id": device_id, "metrics": out_metrics}


async def get_telemetry_anomalies(
    db: AsyncSession,
    tenant_id: str | UUID,
    device_id: str,
    metric: str,
    period_days: int = 30,
) -> List[Dict[str, Any]]:
    """Detect anomalies in a device's telemetry stream for one slug.

    ``metric`` is interpreted directly as a biomarker slug (no alias map).
    Pulls the historical series from the tenant-scoped hypertable, then runs
    the (synchronous) ``AnomalyDetector`` against the most recent value as
    ``new_value`` and everything earlier as ``historical_values``.
    """
    tenant_uuid = _coerce_uuid(tenant_id)
    if tenant_uuid is None:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
    query = (
        select(TelemetryDataModel.timestamp, TelemetryDataModel.value)
        .where(
            TelemetryDataModel.tenant_id == tenant_uuid,
            TelemetryDataModel.device_id == device_id,
            TelemetryDataModel.slug == metric,
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
