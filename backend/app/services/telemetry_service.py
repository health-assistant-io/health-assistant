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
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telemetry_model import TelemetryDataModel
from app.services.anomaly_detector import AnomalyDetector

logger = logging.getLogger(__name__)

# Core bulk-insert chunk size. Balances insert throughput against single-
# statement memory / WAL pressure.
_UPLOAD_CHUNK_SIZE = 5000


def _coerce_uuid(value: str | UUID | None) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_iso_date(value: str) -> datetime | None:
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


def _parse_slug_list(metrics: str | None) -> set | None:
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

    rows: list[dict[str, Any]] = []
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
    metrics: str | None = None,
) -> list[dict[str, Any]]:
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
    device_id: str | None = None,
    metrics: str | None = None,
) -> dict[str, Any]:
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

    out_metrics: dict[str, dict[str, Any]] = {}
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
) -> list[dict[str, Any]]:
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


# --------------------------------------------------------------------------- #
#  Patient-scoped readers (bridge read path)                                  #
# --------------------------------------------------------------------------- #


def _bound_dt(value: Any) -> datetime | None:
    """Coerce a date/datetime string or object into a tz-aware datetime.

    Accepts ISO-8601 strings (via ``_parse_iso_date``) and datetime objects
    (naive assumed UTC). Returns None on parse failure.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return _parse_iso_date(value)


async def _patient_scope_predicate(
    db: AsyncSession, tenant_uuid: UUID, patient_uuid: UUID
):
    """Telemetry patient-scoping predicate, including the legacy fallback.

    Fresh rows always carry ``patient_id`` (``apply_telemetry_split`` persists
    it from the observation). Pre-migration rows may be NULL; when the tenant
    has exactly one patient, NULL-patient rows scoped to that tenant are also
    matched (mirrors ``migrate_biomarker_data``'s single-patient-tenant
    default). Multi-patient tenants never fall back, so cross-patient
    isolation is strict.
    """
    from app.models.fhir.patient import Patient

    count = await db.execute(
        select(func.count(Patient.id)).where(Patient.tenant_id == tenant_uuid)
    )
    if (count.scalar() or 0) == 1:
        return or_(
            TelemetryDataModel.patient_id == patient_uuid,
            TelemetryDataModel.patient_id.is_(None),
        )
    return TelemetryDataModel.patient_id == patient_uuid


async def get_patient_telemetry_series(
    db: AsyncSession,
    tenant_id: str | UUID,
    patient_id: str | UUID,
    slug: str,
    start_date: Any = None,
    end_date: Any = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Patient-scoped telemetry series for one biomarker slug.

    Bridge-facing counterpart to ``get_telemetry_data`` (which is device-
    scoped): filters by the persisted ``patient_id`` instead of a device, so
    a bound patient can read back every wearable stream regardless of which
    device produced it. Rows are returned newest-first (the bridge emits them
    newest-first too). Legacy NULL-patient rows are matched only in
    single-patient tenants (see ``_patient_scope_predicate``).
    """
    tenant_uuid = _coerce_uuid(tenant_id)
    patient_uuid = _coerce_uuid(patient_id)
    if tenant_uuid is None or patient_uuid is None:
        return []

    try:
        cap = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        cap = 500

    scope = await _patient_scope_predicate(db, tenant_uuid, patient_uuid)
    query = (
        select(TelemetryDataModel)
        .where(
            TelemetryDataModel.tenant_id == tenant_uuid,
            TelemetryDataModel.slug == slug,
            scope,
        )
        .order_by(TelemetryDataModel.timestamp.desc())
        .limit(cap)
    )

    start_dt = _bound_dt(start_date)
    if start_dt is not None:
        query = query.where(TelemetryDataModel.timestamp >= start_dt)
    end_dt = _bound_dt(end_date)
    if end_dt is not None:
        query = query.where(TelemetryDataModel.timestamp <= end_dt)

    result = await db.execute(query)
    return [row.to_dict() for row in result.scalars().all()]


async def get_patient_telemetry_latest(
    db: AsyncSession,
    tenant_id: str | UUID,
    patient_id: str | UUID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Latest telemetry row per slug for one patient.

    ``DISTINCT ON (slug)`` scoped to tenant + patient — the telemetry side of
    the bridge's latest-per-biomarker read. Returns the same ``to_dict()``
    shape as ``get_patient_telemetry_series``.
    """
    tenant_uuid = _coerce_uuid(tenant_id)
    patient_uuid = _coerce_uuid(patient_id)
    if tenant_uuid is None or patient_uuid is None:
        return []

    try:
        cap = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        cap = 50

    scope = await _patient_scope_predicate(db, tenant_uuid, patient_uuid)
    query = (
        select(TelemetryDataModel)
        .where(
            TelemetryDataModel.tenant_id == tenant_uuid,
            scope,
        )
        .distinct(TelemetryDataModel.slug)
        .order_by(TelemetryDataModel.slug, TelemetryDataModel.timestamp.desc())
        .limit(cap)
    )

    result = await db.execute(query)
    return [row.to_dict() for row in result.scalars().all()]
