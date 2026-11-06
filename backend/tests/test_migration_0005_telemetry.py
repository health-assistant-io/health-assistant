"""Round-trip for 0005_telemetry_unified (device registry + canonical units).

Runs the real chain against a scratch database:

1. upgrade to ``0004_proposals_unified`` (the pre-plan-3 telemetry shape:
   free-string ``device_id``, no provenance columns, ladder already present);
2. seed a tenant/patient and one telemetry row in that old shape;
3. upgrade to head: ``telemetry_devices``/``telemetry_anomalies`` exist,
   ``device_id`` is UUID, ``raw_value``/``raw_unit`` exist, and the pre-UUID
   rows are deliberately discarded (documented in the revision);
4. downgrade to ``0004``: the free-string column and the old indexes/ladder are
   back, the new tables are gone.

A scratch database keeps the shared test DB untouched (family-dev §4).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import psycopg2
import pytest

from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[1]
TARGET = "0004_proposals_unified"


def _admin_dsn() -> dict:
    return {
        "host": settings.POSTGRES_HOST,
        "port": settings.POSTGRES_PORT,
        "user": settings.POSTGRES_USER,
        "password": settings.POSTGRES_PASSWORD,
        "dbname": "postgres",
    }


def _db_url(name: str) -> str:
    user = quote(settings.POSTGRES_USER, safe="")
    password = quote(settings.POSTGRES_PASSWORD or "", safe="")
    return (
        f"postgresql+asyncpg://{user}:{password}@"
        f"{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{name}"
    )


def _alembic(action: str, revision: str, url: str) -> None:
    env = {
        **os.environ,
        "HA_DATABASE_URL": url,
        "HA_APP_ENV": "test",
        "PYTHONPATH": str(BACKEND),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", action, revision],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {action} {revision} failed:\n{result.stderr}"


@pytest.fixture(scope="module")
def scratch_db():
    name = f"neuronection_health_migtest_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(**_admin_dsn())
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cur.fetchone()[0]:
                pytest.skip("the database role cannot CREATE DATABASE")
            cur.execute(f'CREATE DATABASE "{name}"')
        try:
            yield name
        finally:
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        admin.close()


@pytest.fixture(scope="module")
def scratch_url(scratch_db):
    return _db_url(scratch_db)


def _connect(name: str):
    return psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=name,
    )


def _column_types(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'telemetry_data'"
        )
        return dict(cur.fetchall())


def _table_exists(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass(%s) IS NOT NULL",
            (f"public.{name}",),
        )
        return bool(cur.fetchone()[0])


def test_0005_rewrites_the_hypertable_and_downgrade_restores_the_old_shape(scratch_db, scratch_url):
    _alembic("upgrade", TARGET, scratch_url)

    tenant_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (id, name, slug, is_active) VALUES (%s, %s, %s, true)",
                (tenant_id, "Migration T", f"mig-{tenant_id.hex[:8]}"),
            )
            cur.execute(
                "INSERT INTO fhir_patients (id, tenant_id, name, gender) "
                "VALUES (%s, %s, %s::jsonb, 'UNKNOWN')",
                (patient_id, tenant_id, '{"family": "T", "given": ["P"]}'),
            )
            cur.execute(
                "INSERT INTO telemetry_data "
                "(id, tenant_id, timestamp, device_id, slug, value, unit, patient_id) "
                "VALUES (%s, %s, now(), 'watch-abc', 'heart-rate', 72, 'bpm', %s)",
                (uuid.uuid4(), tenant_id, patient_id),
            )
        conn.commit()
        before = _column_types(conn)
        assert before["device_id"] == "character varying"
    finally:
        conn.close()

    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        types = _column_types(conn)
        assert types["device_id"] == "uuid"
        assert types["raw_value"] == "double precision"
        assert types["raw_unit"] == "character varying"
        assert _table_exists(conn, "telemetry_devices")
        assert _table_exists(conn, "telemetry_anomalies")
        assert _table_exists(conn, "telemetry_hourly")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM telemetry_data")
            assert cur.fetchone()[0] == 0, "pre-UUID rows are deliberately discarded"
            cur.execute("SELECT count(*) FROM telemetry_devices")
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()

    _alembic("downgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        types = _column_types(conn)
        assert types["device_id"] == "character varying"
        assert "raw_value" not in types
        assert "raw_unit" not in types
        assert not _table_exists(conn, "telemetry_devices")
        assert not _table_exists(conn, "telemetry_anomalies")
        assert _table_exists(conn, "telemetry_hourly")
    finally:
        conn.close()
