"""One anomaly engine: rolling statistics over the CAgg ladder.

Detection is SQL-side over ``telemetry_hourly`` — never a Python scan of raw
rows. For every ``(tenant, patient, device, slug)`` series the latest complete
bucket is compared with the trailing baseline (up to 24 preceding buckets); a
|z-score| above the threshold is a ``spike`` or ``drop`` event. Events persist
to ``telemetry_anomalies`` and deduplicate on
``(tenant, patient, device, slug, window_start, kind)``, so re-running detection
updates one row instead of appending duplicates. A later bucket resolves an
older active event, which is how recovery is recorded.

The engine is the only anomaly implementation in the app: the old Python
raw-rows detector was deleted in the same change.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.biomarker_model import BiomarkerDefinition
from app.models.enums import TelemetryAnomalyKind
from app.models.telemetry_model import TelemetryAnomalyModel

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_MIN_SAMPLES = 5
DEFAULT_THRESHOLD = 2.5
MAX_TRAILING_BUCKETS = 24
_DETECTION_SQL = """
    WITH series AS (
        SELECT tenant_id, patient_id, device_id, slug, bucket,
               avg_val, sample_count
        FROM telemetry_hourly
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND bucket >= CAST(:since AS timestamptz)
          {patient_scope}
    ),
    windowed AS (
        SELECT *,
               AVG(avg_val) OVER w AS baseline_mean,
               STDDEV_SAMP(avg_val) OVER w AS baseline_std,
               COUNT(*) OVER w AS baseline_count,
               ROW_NUMBER() OVER (
                   PARTITION BY tenant_id, patient_id, device_id, slug
                   ORDER BY bucket DESC
               ) AS rn
        FROM series
        WINDOW w AS (
            PARTITION BY tenant_id, patient_id, device_id, slug
            ORDER BY bucket
            ROWS BETWEEN {trailing} PRECEDING AND 1 PRECEDING
        )
    )
    SELECT tenant_id, patient_id, device_id, slug, bucket AS window_start,
           bucket + INTERVAL '1 hour' AS window_end,
           avg_val, sample_count, baseline_mean, baseline_std, baseline_count,
           (avg_val - baseline_mean) / baseline_std AS z_score,
           (avg_val - baseline_mean) AS delta
    FROM windowed
    WHERE rn = 1
      AND baseline_count >= :min_samples
      AND baseline_std > 0
      AND ABS((avg_val - baseline_mean) / baseline_std) > :threshold
      {patient_filter}
"""


def _severity(z_score: float) -> str:
    return "critical" if abs(z_score) >= 3.0 else "warning"


def _detection_sql(patient_scoped: bool) -> Any:
    scope = "AND patient_id = CAST(:patient_id AS uuid)" if patient_scoped else ""
    return text(
        _DETECTION_SQL.format(
            patient_scope=scope,
            trailing=MAX_TRAILING_BUCKETS,
            patient_filter=scope,
        )
    )


async def _definition_unit_map(
    db: AsyncSession, tenant_id: UUID, slugs: set[str]
) -> dict[str, tuple[str | None, str | None]]:
    """Map slug → (name, preferred unit symbol), tenant rows shadowing global."""
    if not slugs:
        return {}
    result = await db.execute(
        select(BiomarkerDefinition).where(
            BiomarkerDefinition.slug.in_(slugs),
            or_(
                BiomarkerDefinition.tenant_id == tenant_id,
                BiomarkerDefinition.tenant_id.is_(None),
            ),
        )
    )
    mapping: dict[str, tuple[str | None, str | None]] = {}
    for definition in result.scalars().all():
        if definition.tenant_id is None and definition.slug in mapping:
            continue
        unit = getattr(getattr(definition, "preferred_unit", None), "symbol", None)
        mapping[definition.slug] = (definition.name, unit)
    return mapping


async def detect_telemetry_anomalies(
    db: AsyncSession,
    *,
    tenant_id: UUID | str,
    patient_id: UUID | str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict[str, Any]]:
    """Detect, persist and deduplicate telemetry anomalies for one tenant.

    Returns the detected (currently anomalous) rows. Idempotent: running twice
    over the same buckets leaves exactly one row per event.
    """
    tenant_uuid = UUID(str(tenant_id))
    patient_uuid = UUID(str(patient_id)) if patient_id else None
    since = datetime.now(UTC) - timedelta(days=lookback_days)

    exists = await db.execute(text("SELECT to_regclass('public.telemetry_hourly')"))
    if exists.scalar() is None:
        return []

    params: dict[str, Any] = {
        "tenant_id": str(tenant_uuid),
        "since": since,
        "min_samples": min_samples,
        "threshold": threshold,
    }
    if patient_uuid is not None:
        params["patient_id"] = str(patient_uuid)

    result = await db.execute(_detection_sql(patient_uuid is not None), params)
    detected = result.mappings().all()
    if not detected:
        await _resolve_recovered(db, tenant_uuid, patient_uuid, since)
        await db.commit()
        return []

    definitions = await _definition_unit_map(db, tenant_uuid, {row["slug"] for row in detected})
    rows: list[dict[str, Any]] = []
    for row in detected:
        z_score = float(row["z_score"])
        name, unit = definitions.get(row["slug"], (None, None))
        rows.append(
            {
                "tenant_id": tenant_uuid,
                "patient_id": row["patient_id"],
                "device_id": row["device_id"],
                "slug": row["slug"],
                "window_start": row["window_start"],
                "window_end": row["window_end"],
                "kind": (TelemetryAnomalyKind.SPIKE if z_score > 0 else TelemetryAnomalyKind.DROP),
                "z_score": z_score,
                "delta": float(row["delta"]),
                "payload": {
                    "value": float(row["avg_val"]),
                    "unit": unit,
                    "biomarker_name": name,
                    "baseline_mean": float(row["baseline_mean"]),
                    "baseline_std": float(row["baseline_std"]),
                    "baseline_count": int(row["baseline_count"]),
                    "sample_count": int(row["sample_count"] or 0),
                    "severity": _severity(z_score),
                },
                "payload_schema_version": 1,
            }
        )

    table = TelemetryAnomalyModel.__table__
    stmt = pg_insert(table).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            "tenant_id",
            "patient_id",
            "device_id",
            "slug",
            "window_start",
            "kind",
        ],
        set_={
            "window_end": stmt.excluded.window_end,
            "z_score": stmt.excluded.z_score,
            "delta": stmt.excluded.delta,
            "payload": stmt.excluded.payload,
            "payload_schema_version": stmt.excluded.payload_schema_version,
            "updated_at": text("now()"),
        },
    )
    await db.execute(stmt)
    await _resolve_recovered(db, tenant_uuid, patient_uuid, since)
    await db.commit()
    return rows


async def _resolve_recovered(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: UUID | None,
    since: datetime,
) -> None:
    """Resolve active events once a newer bucket exists for their series.

    A newer bucket means the series has moved past the event window: either it
    recovered (no new detection) or a new event superseded it. Either way the
    old row is closed; the next detection inserts/updates the new window.
    """
    scope = ""
    params: dict[str, Any] = {"tenant_id": str(tenant_id), "since": since}
    if patient_id is not None:
        scope = "AND patient_id = CAST(:patient_id AS uuid)"
        params["patient_id"] = str(patient_id)
    await db.execute(
        text(
            f"""
            UPDATE telemetry_anomalies a
            SET resolved_at = now()
            FROM (
                SELECT tenant_id, patient_id, device_id, slug, MAX(bucket) AS latest_bucket
                FROM telemetry_hourly
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND bucket >= CAST(:since AS timestamptz)
                  {scope}
                GROUP BY tenant_id, patient_id, device_id, slug
            ) latest
            WHERE a.tenant_id = latest.tenant_id
              AND a.patient_id = latest.patient_id
              AND a.device_id = latest.device_id
              AND a.slug = latest.slug
              AND a.resolved_at IS NULL
              AND a.window_start < latest.latest_bucket
            """
        ),
        params,
    )


async def list_telemetry_anomalies(
    db: AsyncSession,
    *,
    tenant_id: UUID | str,
    patient_id: UUID | str | None = None,
    slugs: list[str] | None = None,
    lookback_days: int = 30,
) -> list[dict[str, Any]]:
    """Read persisted anomalies for one tenant, newest first."""
    tenant_uuid = UUID(str(tenant_id))
    query = select(TelemetryAnomalyModel).where(
        TelemetryAnomalyModel.tenant_id == tenant_uuid,
        TelemetryAnomalyModel.window_start >= datetime.now(UTC) - timedelta(days=lookback_days),
    )
    if patient_id:
        query = query.where(TelemetryAnomalyModel.patient_id == UUID(str(patient_id)))
    if slugs:
        query = query.where(TelemetryAnomalyModel.slug.in_(slugs))
    query = query.order_by(TelemetryAnomalyModel.detected_at.desc())
    result = await db.execute(query)
    anomalies = list(result.scalars().all())

    definitions = await _definition_unit_map(db, tenant_uuid, {a.slug for a in anomalies})
    out: list[dict[str, Any]] = []
    for anomaly in anomalies:
        name, unit = definitions.get(anomaly.slug, (None, None))
        payload = anomaly.payload or {}
        severity = payload.get("severity") or _severity(anomaly.z_score)
        direction = "above" if anomaly.kind == TelemetryAnomalyKind.SPIKE else "below"
        out.append(
            {
                "id": str(anomaly.id),
                "type": "statistical_anomaly",
                "kind": anomaly.kind.value,
                "biomarker": name or anomaly.slug,
                "biomarker_slug": anomaly.slug,
                "biomarker_id": str(payload.get("biomarker_id"))
                if payload.get("biomarker_id")
                else None,
                "value": payload.get("value"),
                "unit": unit or payload.get("unit"),
                "message": (
                    f"{name or anomaly.slug} is {abs(anomaly.z_score):.2f} standard "
                    f"deviations {direction} its trailing baseline"
                ),
                "severity": severity,
                "z_score": anomaly.z_score,
                "delta": anomaly.delta,
                "window_start": anomaly.window_start.isoformat() if anomaly.window_start else None,
                "window_end": anomaly.window_end.isoformat() if anomaly.window_end else None,
                "detected_at": anomaly.detected_at.isoformat() if anomaly.detected_at else None,
                "resolved_at": anomaly.resolved_at.isoformat() if anomaly.resolved_at else None,
                "payload_schema_version": anomaly.payload_schema_version,
            }
        )
    return out
