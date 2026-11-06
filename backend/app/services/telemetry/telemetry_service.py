"""Telemetry service.

Long-format hypertable writes over the TimescaleDB ``telemetry_data`` table,
plus the patient-scoped read path the bridge sub-API serves. All read paths
take ``tenant_id`` explicitly so a caller cannot read another tenant's data.

The hypertable stores one row per ``(timestamp, device, slug)`` triple — no
metric-specific columns, no JSONB catch-all. Every consumer treats ``slug`` as
the uniform metric discriminator. Ingest goes through
:mod:`app.services.telemetry.telemetry_ingest`, which enforces the slug registry, device
identity and canonical units in one seam.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telemetry_model import TelemetryDataModel
from app.services.telemetry.telemetry_ingest import (
    build_telemetry_row,
    resolve_telemetry_definition_map,
    resolve_telemetry_device,
)

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
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


async def upload_telemetry_data(
    db: AsyncSession,
    device_id: str,
    data_points: Sequence[Any],
    tenant_id: str | UUID,
    source: str = "bridge",
    device_label: str | None = None,
) -> int:
    """Validate, canonicalize and bulk-insert long-format telemetry rows.

    Every slug must resolve to a visible ``is_telemetry`` BiomarkerDefinition
    and every point must carry a ``patient_id``; both failures are typed
    ``ValueError``s (the endpoint maps them to 400). The client's opaque
    ``device_id`` resolves to a ``telemetry_devices`` UUID scoped by tenant and
    patient, and the canonical ``value``/``unit`` pair is stored beside the
    source ``raw_value``/``raw_unit``.

    Chunks at ``_UPLOAD_CHUNK_SIZE`` rows and uses
    ``ON CONFLICT (id, timestamp) DO NOTHING`` so a retried chunk (e.g. after
    a client-side timeout on a partially-committed batch) doesn't abort the
    whole upload. Returns the number of rows inserted.
    """
    tenant_uuid = _coerce_uuid(tenant_id)
    if tenant_uuid is None:
        raise ValueError(f"Invalid tenant_id: {tenant_id!r}")

    if not data_points:
        return 0

    patients: dict[UUID, list[Any]] = {}
    for point in data_points:
        patient_uuid = _coerce_uuid(getattr(point, "patient_id", None))
        if patient_uuid is None:
            raise ValueError(
                f"Telemetry point for slug {getattr(point, 'slug', None)!r} has no "
                "patient_id — patient attribution is required"
            )
        patients.setdefault(patient_uuid, []).append(point)

    slugs = {getattr(point, "slug", None) for point in data_points}
    if None in slugs:
        raise ValueError("Telemetry point is missing its slug")
    definitions = await resolve_telemetry_definition_map(db, tenant_uuid, slugs)

    rows: list[dict[str, Any]] = []
    device_ids: dict[UUID, UUID] = {}
    for patient_uuid, points in patients.items():
        device_uuid = await resolve_telemetry_device(
            db,
            tenant_id=tenant_uuid,
            patient_id=patient_uuid,
            source=source,
            external_ref=device_id,
            label=device_label,
        )
        device_ids[patient_uuid] = device_uuid
        for point in points:
            definition = definitions[point.slug]
            rows.append(
                build_telemetry_row(
                    tenant_id=tenant_uuid,
                    patient_id=patient_uuid,
                    device_id=device_uuid,
                    timestamp=point.timestamp,
                    definition=definition,
                    value=point.value,
                    unit=getattr(point, "unit", None),
                )
            )

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


# --------------------------------------------------------------------------- #
#  Patient-scoped readers (bridge read path)                                  #
# --------------------------------------------------------------------------- #


def _bound_dt(value: Any) -> datetime | None:
    """Coerce a date/datetime string or object into a tz-aware datetime.

    Accepts ISO-8601 strings (via ``_parse_iso_date``) and datetime objects
    (naive assumed UTC). Returns None on parse failure.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return _parse_iso_date(value)


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

    Bridge-facing counterpart to the analytics readers: filters by the
    persisted ``patient_id`` instead of a device, so a bound patient can read
    back every wearable stream regardless of which device produced it. Rows are
    returned newest-first (the bridge emits them newest-first too).
    """
    tenant_uuid = _coerce_uuid(tenant_id)
    patient_uuid = _coerce_uuid(patient_id)
    if tenant_uuid is None or patient_uuid is None:
        return []

    try:
        cap = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        cap = 500

    query = (
        select(TelemetryDataModel)
        .where(
            TelemetryDataModel.tenant_id == tenant_uuid,
            TelemetryDataModel.patient_id == patient_uuid,
            TelemetryDataModel.slug == slug,
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

    # Patient attribution is NOT NULL since the baseline squash.
    query = (
        select(TelemetryDataModel)
        .where(
            TelemetryDataModel.tenant_id == tenant_uuid,
            TelemetryDataModel.patient_id == patient_uuid,
        )
        .distinct(TelemetryDataModel.slug)
        .order_by(TelemetryDataModel.slug, TelemetryDataModel.timestamp.desc())
        .limit(cap)
    )

    result = await db.execute(query)
    return [row.to_dict() for row in result.scalars().all()]
