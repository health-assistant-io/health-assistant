"""Round-trip for 0019_session_metadata (program 12C1).

Runs the real chain against a scratch database:

1. upgrade an **empty** database straight to head — the three device columns
   appear and the two notification vocabularies carry their new labels;
2. downgrade to ``0018`` — the columns are gone (the enum labels stay:
   Postgres cannot remove one, and the upgrade is written ``IF NOT EXISTS``
   so a re-upgrade is clean);
3. upgrade back to ``0019`` — the columns return.

Row-level security is untouched by this revision: ``auth_sessions`` keeps
the ``users``-anchored child policy of ``0016`` (asserted here so a future
column addition cannot silently drop the policy).
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
BEFORE = "0018_enterprise_identity"
HEAD = "0019_session_metadata"


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


def _connect(name: str):
    return psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=name,
    )


def _session_columns(cur) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'auth_sessions'"
    )
    return {row[0] for row in cur.fetchall()}


def _enum_labels(cur, type_name: str) -> set[str]:
    cur.execute(
        "SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
        "WHERE t.typname = %s",
        (type_name,),
    )
    return {row[0] for row in cur.fetchall()}


@pytest.fixture(scope="module")
def scratch_db():
    name = f"neuronection_health_sessmeta_{uuid.uuid4().hex[:8]}"
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


def test_empty_database_upgrades_straight_to_head(scratch_db):
    _alembic("upgrade", "head", _db_url(scratch_db))
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            assert {"ip", "user_agent", "last_seen_at"} <= _session_columns(cur)
            assert "SECURITY_EVENT" in _enum_labels(cur, "notificationtype")
            assert "security" in _enum_labels(cur, "notificationcategory")
            cur.execute("SELECT polname FROM pg_policy WHERE polrelid = 'auth_sessions'::regclass")
            assert {row[0] for row in cur.fetchall()}, "auth_sessions lost its RLS policy"
    finally:
        conn.close()


def test_downgrade_drops_the_columns_and_reupgrade_restores_them(scratch_db):
    _alembic("downgrade", BEFORE, _db_url(scratch_db))
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            columns = _session_columns(cur)
            assert not {"ip", "user_agent", "last_seen_at"} & columns
            # Postgres cannot remove an enum label; the labels survive on
            # purpose (harmless for the older code, clean for the re-upgrade).
            assert "SECURITY_EVENT" in _enum_labels(cur, "notificationtype")
    finally:
        conn.close()

    _alembic("upgrade", HEAD, _db_url(scratch_db))
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            assert {"ip", "user_agent", "last_seen_at"} <= _session_columns(cur)
    finally:
        conn.close()
