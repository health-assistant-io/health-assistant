"""Round-trip for 0006_connector_framework (credentials + sync state).

Runs the real chain against a scratch database:

1. upgrade to ``0005_telemetry_unified`` — no connector-framework tables, no
   ``integration_sync_logs.error_class``;
2. upgrade to head: both tables exist with their typed columns, the closed
   vocabularies are mirrored in CHECK constraints, and the credential slot is
   unique per ``(integration, kind)``;
3. downgrade to ``0005``: the two tables and the log column are gone (the old
   ``user_config`` JSONB cursor bag is deliberately not resurrected).

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
TARGET = "0005_telemetry_unified"


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


def _table_exists(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
        return bool(cur.fetchone()[0])


def _columns(conn, table: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        return dict(cur.fetchall())


def _constraint_defs(conn, table: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = %s::regclass",
            (f"public.{table}",),
        )
        return dict(cur.fetchall())


def test_0006_adds_connector_tables_and_downgrade_removes_them(scratch_db, scratch_url):
    _alembic("upgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        assert not _table_exists(conn, "integration_credentials")
        assert not _table_exists(conn, "integration_sync_state")
        assert "error_class" not in _columns(conn, "integration_sync_logs")
    finally:
        conn.close()

    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        credentials = _columns(conn, "integration_credentials")
        for column in (
            "tenant_id",
            "user_integration_id",
            "kind",
            "sealed_blob",
            "kid",
            "scopes",
            "access_expires_at",
            "refresh_expires_at",
            "rotated_at",
            "revoked_at",
        ):
            assert column in credentials, f"integration_credentials.{column} missing"
        assert credentials["sealed_blob"] == "text"
        assert credentials["scopes"] == "ARRAY"

        state = _columns(conn, "integration_sync_state")
        for column in (
            "tenant_id",
            "user_integration_id",
            "stream",
            "cursor_kind",
            "cursor_value",
            "last_success_at",
            "last_error_at",
            "last_error_class",
            "consecutive_failures",
            "next_retry_at",
        ):
            assert column in state, f"integration_sync_state.{column} missing"
        assert state["consecutive_failures"] == "integer"

        assert "error_class" in _columns(conn, "integration_sync_logs")

        credential_checks = " ".join(_constraint_defs(conn, "integration_credentials").values())
        assert "oauth2" in credential_checks and "api_key" in credential_checks
        state_checks = " ".join(_constraint_defs(conn, "integration_sync_state").values())
        for value in ("observations", "documents", "appointments", "labs", "telemetry"):
            assert value in state_checks, f"stream vocabulary missing {value}"
        for value in ("timestamp", "page_token", "etag", "commit"):
            assert value in state_checks, f"cursor kind vocabulary missing {value}"
        assert "mapping_gap" in state_checks
        assert "credential" in state_checks

        credential_constraints = _constraint_defs(conn, "integration_credentials")
        assert any(
            "UNIQUE" in definition and "user_integration_id" in definition
            for definition in credential_constraints.values()
        ), "credential slots must be unique per (integration, kind)"
    finally:
        conn.close()

    _alembic("downgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        assert not _table_exists(conn, "integration_credentials")
        assert not _table_exists(conn, "integration_sync_state")
        assert "error_class" not in _columns(conn, "integration_sync_logs")
        # The previous shape (0005) is intact.
        assert _table_exists(conn, "telemetry_devices")
    finally:
        conn.close()
