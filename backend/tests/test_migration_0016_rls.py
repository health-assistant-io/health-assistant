"""Round-trip for 0016_rls (ADR 0054 D4/D6 — the second wall).

Runs the real chain against a scratch database:

1. upgrade to ``0015_seed_packs`` — no policies exist;
2. seed two tenants with one document each;
3. upgrade to head — every tenant-scoped table carries RLS policies, the
   runtime role can only read its own tenant's row through the context
   GUCs, and the cagg surface is views-only;
4. downgrade to ``0015`` — policies and scoped views are gone, RLS flags
   cleared, and the rows survive.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[1]
BEFORE = "0015_seed_packs"
HEAD = "0016_rls"
APP_ROLE = "neuronection_health_app"


def _admin_dsn() -> dict:
    return {
        "host": settings.POSTGRES_HOST,
        "port": settings.POSTGRES_PORT,
        "user": settings.POSTGRES_USER,
        "password": settings.POSTGRES_PASSWORD,
        "dbname": "postgres",
    }


def _db_url(name: str, driver: str = "psycopg2") -> str:
    from tests._db_roles import db_url

    return db_url(name, driver=driver)


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
    name = f"neuronection_health_rls_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(**_admin_dsn())
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cur.fetchone()[0]:
                pytest.skip("the database role cannot CREATE DATABASE")
            cur.execute(f'CREATE DATABASE "{name}"')
        yield name
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _connect(name: str, *, role: str | None = None):
    from tests._db_roles import APP_ROLE, app_role_password

    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=role or settings.POSTGRES_USER,
        password=app_role_password() if role == APP_ROLE else settings.POSTGRES_PASSWORD,
        dbname=name,
    )
    return conn


def _policy_count(name: str) -> int:
    conn = _connect(name)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_policy")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _has_timescaledb(name: str) -> bool:
    conn = _connect(name)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
            return cur.fetchone() is not None
    finally:
        conn.close()


def _seed_documents(name: str) -> tuple[str, str, str]:
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    conn = _connect(name)
    try:
        with conn.cursor() as cur:
            for tenant in (tenant_a, tenant_b):
                cur.execute(
                    "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
                    (tenant, f"probe-{tenant}", f"probe-{tenant}"),
                )
            user_a = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO users (id, email, tenant_id, role, is_active) "
                "VALUES (%s, %s, %s, 'USER', true)",
                (user_a, f"probe-{uuid.uuid4()}@test.local", tenant_a),
            )
            cur.execute(
                "INSERT INTO documents (id, tenant_id, owner_id, filename, file_path, "
                "status, include_in_extraction, is_edited) VALUES (%s, %s, %s, 'a', "
                "'/tmp/a', 'uploaded', false, false)",
                (str(uuid.uuid4()), tenant_a, user_a),
            )
        conn.commit()
    finally:
        conn.close()
    return tenant_a, tenant_b, "present"


def _seed_telemetry_row(name: str, tenant_id: str) -> None:
    """One telemetry row in ``tenant_id`` for the live scoped-view probe."""
    conn = _connect(name)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO telemetry_data (id, timestamp, tenant_id, device_id, "
                "patient_id, slug, value, raw_value, raw_unit, unit) "
                "VALUES (%s, now(), %s, %s, %s, 'hr', 1.0, 1.0, 'bpm', 'bpm')",
                (str(uuid.uuid4()), tenant_id, str(uuid.uuid4()), str(uuid.uuid4())),
            )
        conn.commit()
    finally:
        conn.close()


def test_rls_round_trip(scratch_db):
    owner_url = _db_url(scratch_db)
    _alembic("upgrade", BEFORE, owner_url)
    assert _policy_count(scratch_db) == 0, "policies existed before 0016"

    tenant_a, _tenant_b, _ = _seed_documents(scratch_db)

    _alembic("upgrade", "head", owner_url)

    timescaled = _has_timescaledb(scratch_db)

    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_policy")
            assert cur.fetchone()[0] > 0
            cur.execute(
                "SELECT relrowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname='public' AND c.relname='documents'"
            )
            assert cur.fetchone()[0] is True
            cur.execute(
                "SELECT has_table_privilege(%s, 'alembic_version', 'SELECT')",
                (APP_ROLE,),
            )
            assert cur.fetchone()[0] is False
            if timescaled:
                # The live hypertable's GUC-filtered scoped view exists (the
                # trends live-aggregate surface — S2 of the phase 11 revision).
                cur.execute(
                    "SELECT has_table_privilege(%s, 'telemetry_live_scoped', 'SELECT')",
                    (APP_ROLE,),
                )
                assert cur.fetchone()[0] is True
    finally:
        conn.close()

    if timescaled:
        _seed_telemetry_row(scratch_db, tenant_a)

    app = _connect(scratch_db, role=APP_ROLE)
    try:
        with app.cursor() as cur:
            cur.execute("SELECT count(*) FROM documents")
            assert cur.fetchone()[0] == 0, "the app role read rows without a context"
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_a,))
            cur.execute("SELECT count(*) FROM documents")
            assert cur.fetchone()[0] == 1, "the tenant context lost its own row"
            if timescaled:
                cur.execute("SELECT count(*) FROM telemetry_live_scoped")
                assert cur.fetchone()[0] == 1, "the live scoped view lost its tenant row"
                cur.execute("SELECT set_config('app.tenant_id', '', true)")
                cur.execute("SELECT count(*) FROM telemetry_live_scoped")
                assert cur.fetchone()[0] == 0, "the live scoped view answered without a GUC"
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_a,))
            cur.execute("SELECT set_config('app.system', 'on', true)")
            cur.execute("SELECT count(*) FROM documents")
            assert cur.fetchone()[0] == 1
    finally:
        app.close()

    _alembic("downgrade", BEFORE, owner_url)

    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_policy")
            assert cur.fetchone()[0] == 0, "the downgrade left policies behind"
            cur.execute(
                "SELECT relrowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname='public' AND c.relname='documents'"
            )
            assert cur.fetchone()[0] is False, "the downgrade left RLS enabled"
            cur.execute("SELECT count(*) FROM documents")
            assert cur.fetchone()[0] == 1, "the downgrade lost data"
            cur.execute("SELECT count(*) FROM pg_views WHERE viewname LIKE 'telemetry%%_scoped'")
            assert cur.fetchone()[0] == 0, "the downgrade left scoped views"
            cur.execute("SELECT has_table_privilege(%s, 'documents', 'SELECT')", (APP_ROLE,))
            assert cur.fetchone()[0] is True, "the downgrade dropped the DML grants"
    finally:
        conn.close()
