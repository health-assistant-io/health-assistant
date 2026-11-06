"""Telemetry ingest + anomaly engine (plan 3 W2/W4).

DB-backed tests for the three non-vacuous promises:

* the slug registry rejects an unknown/non-telemetry slug at ingest;
* canonical units aggregate a mixed-unit series correctly while provenance is
  preserved;
* the CAgg anomaly engine detects an injected spike exactly once (dedup key),
  resolves it on recovery, and keeps same-slug events for two patients/devices
  distinct.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, func, select, text

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.fhir.patient import Patient
from app.models.telemetry_model import (
    TelemetryAnomalyModel,
    TelemetryDataModel,
    TelemetryDeviceModel,
)
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.biomarkers.anomaly_engine import (
    detect_telemetry_anomalies,
    list_telemetry_anomalies,
)
from app.services.telemetry.telemetry_ingest import (
    UnknownTelemetrySlugError,
    resolve_telemetry_device,
)
from app.services.telemetry.telemetry_service import upload_telemetry_data

TENANT = uuid.uuid4()
PATIENT = uuid.uuid4()
PATIENT_B = uuid.uuid4()
DEVICE_POINT = "ring-1"


class _Point:
    def __init__(self, ts, slug, value, unit, patient_id=PATIENT):
        self.timestamp = ts
        self.slug = slug
        self.value = value
        self.unit = unit
        self.patient_id = patient_id


async def _seed_tenant_and_patients() -> None:
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=TENANT, name="Telemetry Test", slug=f"tlm-{TENANT.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=uuid.uuid4(),
                email=f"tlm-{uuid.uuid4().hex[:6]}@test.local",
                tenant_id=TENANT,
                role="ADMIN",
            )
        )
        for patient_id in (PATIENT, PATIENT_B):
            db.add(
                Patient(
                    id=patient_id,
                    tenant_id=TENANT,
                    name={"family": "T", "given": ["P"]},
                    gender="UNKNOWN",
                )
            )
        await db.commit()


async def _purge() -> None:
    async with AsyncSessionLocal() as db:
        for model in (
            TelemetryAnomalyModel,
            TelemetryDataModel,
            TelemetryDeviceModel,
            BiomarkerDefinition,
            Patient,
            UserModel,
        ):
            await db.execute(delete(model).where(model.tenant_id == TENANT))
        await db.execute(delete(TenantModel).where(TenantModel.id == TENANT))
        await db.commit()


async def _seed_definition(
    slug: str, *, is_telemetry: bool = True, preferred_unit: str | None = None
) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        unit_id = None
        if preferred_unit:
            unit = Unit(
                symbol=preferred_unit,
                name=preferred_unit,
                quantity_type="TEMPERATURE" if preferred_unit == "°C" else "OTHER",
            )
            db.add(unit)
            await db.flush()
            unit_id = unit.id
        definition = BiomarkerDefinition(
            id=uuid.uuid4(),
            tenant_id=TENANT,
            slug=slug,
            name=slug.replace("-", " ").title(),
            coding_system="loinc",
            code=None,
            is_telemetry=is_telemetry,
            preferred_unit_id=unit_id,
        )
        db.add(definition)
        await db.commit()
        return definition.id


async def _refresh_hourly() -> None:
    from sqlalchemy import create_engine

    from app.core.config import settings

    engine = create_engine(
        settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("CALL refresh_continuous_aggregate('telemetry_hourly', NULL, NULL)"))
    finally:
        engine.dispose()


def _hour(offset_hours: int = 0) -> dt.datetime:
    base = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    return base - dt.timedelta(hours=offset_hours)


@pytest.fixture
async def telemetry_env():
    if not TENANT:
        pytest.skip("tenant fixture missing")
    await _purge()
    await _seed_tenant_and_patients()
    try:
        yield
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_unknown_slug_is_rejected_at_ingest(telemetry_env):
    async with AsyncSessionLocal() as db:
        with pytest.raises(UnknownTelemetrySlugError):
            await upload_telemetry_data(
                db,
                DEVICE_POINT,
                [_Point(_hour(1), "not-a-real-biomarker", 12.0, "count")],
                TENANT,
            )


@pytest.mark.asyncio
async def test_non_telemetry_slug_is_rejected_at_ingest(telemetry_env):
    await _seed_definition("lab-only-metric", is_telemetry=False)
    async with AsyncSessionLocal() as db:
        with pytest.raises(UnknownTelemetrySlugError):
            await upload_telemetry_data(
                db,
                DEVICE_POINT,
                [_Point(_hour(1), "lab-only-metric", 12.0, "mg/dL")],
                TENANT,
            )


@pytest.mark.asyncio
async def test_ingest_registers_device_and_canonicalizes_mixed_units(telemetry_env):
    await _seed_definition("body-temperature", preferred_unit="°C")
    points = [
        _Point(_hour(2), "body-temperature", 98.6, "°F"),
        _Point(_hour(1), "body-temperature", 37.0, "°C"),
    ]
    async with AsyncSessionLocal() as db:
        uploaded = await upload_telemetry_data(db, DEVICE_POINT, points, TENANT)
    assert uploaded == 2

    async with AsyncSessionLocal() as db:
        devices = (
            (
                await db.execute(
                    select(TelemetryDeviceModel).where(TelemetryDeviceModel.tenant_id == TENANT)
                )
            )
            .scalars()
            .all()
        )
        assert len(devices) == 1
        assert devices[0].external_ref == DEVICE_POINT
        assert devices[0].patient_id == PATIENT

        rows = (
            (
                await db.execute(
                    select(TelemetryDataModel)
                    .where(TelemetryDataModel.tenant_id == TENANT)
                    .order_by(TelemetryDataModel.timestamp)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert all(row.device_id == devices[0].id for row in rows)
        assert all(row.unit == "°C" for row in rows)
        assert [round(row.value, 4) for row in rows] == [37.0, 37.0]
        assert [row.raw_unit for row in rows] == ["°F", "°C"]
        assert rows[0].raw_value == 98.6

    await _refresh_hourly()
    async with AsyncSessionLocal() as db:
        aggregated = (
            await db.execute(
                text(
                    "SELECT AVG(avg_val) FROM telemetry_hourly "
                    "WHERE tenant_id = :tenant AND slug = 'body-temperature'"
                ),
                {"tenant": str(TENANT)},
            )
        ).scalar()
    assert round(float(aggregated), 4) == 37.0


async def _insert_series(
    *, patient_id: uuid.UUID, device_id: uuid.UUID, slug: str, spike: bool
) -> None:
    timestamps = [_hour(offset) for offset in range(9, -1, -1)]
    values = [60.0, 61.0, 60.0, 59.0, 60.0, 61.0, 60.0, 59.0, 60.0, 200.0 if spike else 60.0]
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                TelemetryDataModel(
                    tenant_id=TENANT,
                    patient_id=patient_id,
                    device_id=device_id,
                    timestamp=ts,
                    slug=slug,
                    value=value,
                    raw_value=value,
                    unit="bpm",
                    raw_unit="bpm",
                )
                for ts, value in zip(timestamps, values, strict=True)
            ]
        )
        await db.commit()


@pytest.mark.asyncio
async def test_anomaly_engine_detects_once_and_resolves_on_recovery(telemetry_env):
    await _seed_definition("heart-rate")
    async with AsyncSessionLocal() as db:
        device_id = await resolve_telemetry_device(
            db,
            tenant_id=TENANT,
            patient_id=PATIENT,
            source="test",
            external_ref=DEVICE_POINT,
        )
        await db.commit()

    await _insert_series(patient_id=PATIENT, device_id=device_id, slug="heart-rate", spike=True)
    await _refresh_hourly()

    async with AsyncSessionLocal() as db:
        detected = await detect_telemetry_anomalies(
            db, tenant_id=TENANT, lookback_days=2, threshold=2.5
        )
    assert len(detected) == 1
    assert detected[0]["kind"].value == "spike"

    # Re-running the same detection must update, not duplicate.
    async with AsyncSessionLocal() as db:
        await detect_telemetry_anomalies(db, tenant_id=TENANT, lookback_days=2, threshold=2.5)
        count = (
            await db.execute(
                select(func.count(TelemetryAnomalyModel.id)).where(
                    TelemetryAnomalyModel.tenant_id == TENANT
                )
            )
        ).scalar()
    assert count == 1

    # Recovery: a newer normal bucket resolves the event without a replacement.
    async with AsyncSessionLocal() as db:
        db.add(
            TelemetryDataModel(
                tenant_id=TENANT,
                patient_id=PATIENT,
                device_id=device_id,
                timestamp=_hour(0) + dt.timedelta(hours=1),
                slug="heart-rate",
                value=60.0,
                raw_value=60.0,
                unit="bpm",
                raw_unit="bpm",
            )
        )
        await db.commit()
    await _refresh_hourly()

    async with AsyncSessionLocal() as db:
        await detect_telemetry_anomalies(db, tenant_id=TENANT, lookback_days=2, threshold=2.5)
        rows = (
            (
                await db.execute(
                    select(TelemetryAnomalyModel).where(TelemetryAnomalyModel.tenant_id == TENANT)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].resolved_at is not None


@pytest.mark.asyncio
async def test_dedup_key_keeps_patients_and_devices_apart(telemetry_env):
    await _seed_definition("heart-rate")
    async with AsyncSessionLocal() as db:
        device_a = await resolve_telemetry_device(
            db, tenant_id=TENANT, patient_id=PATIENT, source="test", external_ref="dev-a"
        )
        device_b = await resolve_telemetry_device(
            db, tenant_id=TENANT, patient_id=PATIENT, source="test", external_ref="dev-b"
        )
        device_c = await resolve_telemetry_device(
            db, tenant_id=TENANT, patient_id=PATIENT_B, source="test", external_ref="dev-a"
        )
        await db.commit()

    await _insert_series(patient_id=PATIENT, device_id=device_a, slug="heart-rate", spike=True)
    await _insert_series(patient_id=PATIENT, device_id=device_b, slug="heart-rate", spike=True)
    await _insert_series(patient_id=PATIENT_B, device_id=device_c, slug="heart-rate", spike=True)
    await _refresh_hourly()

    async with AsyncSessionLocal() as db:
        await detect_telemetry_anomalies(db, tenant_id=TENANT, lookback_days=2, threshold=2.5)
        rows = (
            (
                await db.execute(
                    select(TelemetryAnomalyModel).where(TelemetryAnomalyModel.tenant_id == TENANT)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 3, "two devices + two patients sharing a slug must not collide"
    assert len({(row.patient_id, row.device_id) for row in rows}) == 3

    async with AsyncSessionLocal() as db:
        listed = await list_telemetry_anomalies(db, tenant_id=TENANT)
    assert len(listed) == 3
