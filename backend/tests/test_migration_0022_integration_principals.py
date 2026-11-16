"""Round-trip for 0022_integration_principals (ADR 0054 D8, Phase 13).

Runs the real chain against a scratch database:

1. upgrade an empty database to head — ``integration_principals`` exists with
   its lookup keys (secret prefix/hash uniqueness), the permission/patient
   scope JSONB columns, the partial unique active-integration index, cascade
   FKs and its NOT-NULL-tenant RLS policy;
2. downgrade to ``0021`` — the table is gone;
3. upgrade back to ``0022`` — it returns.

The RLS policy is owned by this revision (``0016`` excludes the table via
``POST_0016_TABLES``), so the policy presence is part of the contract.
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
BEFORE = "0021_api_tokens"
HEAD = "0022_integration_principals"


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


def _columns(cur) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'integration_principals'"
    )
    return {row[0] for row in cur.fetchall()}


def _table_exists(cur) -> bool:
    cur.execute("SELECT to_regclass('public.integration_principals')")
    return cur.fetchone()[0] is not None


@pytest.fixture(scope="module")
def scratch_db():
    name = f"neuronection_health_principal_{uuid.uuid4().hex[:8]}"
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
            assert _table_exists(cur)
            assert {
                "tenant_id",
                "owner_user_id",
                "name",
                "kind",
                "integration_id",
                "scopes",
                "patient_scope",
                "patient_ids",
                "status",
                "secret_prefix",
                "secret_hash",
                "last_used_at",
                "expires_at",
                "revoked_at",
                "rotated_from",
                "created_by",
                "created_at",
                "updated_at",
            } <= _columns(cur)
            cur.execute(
                "SELECT count(*) FROM pg_constraint WHERE conname IN "
                "('uq_integration_principals_secret_prefix', "
                "'uq_integration_principals_secret_hash')"
            )
            assert cur.fetchone()[0] == 2
            cur.execute(
                "SELECT count(*) FROM pg_indexes WHERE tablename = 'integration_principals' "
                "AND indexname = 'uq_integration_principals_active_integration'"
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                "SELECT relrowsecurity FROM pg_class WHERE oid = 'integration_principals'::regclass"
            )
            assert cur.fetchone()[0] is True
            cur.execute(
                "SELECT polname FROM pg_policy WHERE polrelid = 'integration_principals'::regclass"
            )
            assert {row[0] for row in cur.fetchall()} == {"rls_tenant"}
            cur.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'integration_principals'::regclass AND contype = 'c'"
            )
            names = {row[0] for row in cur.fetchall()}
            assert {
                "ck_integration_principals_patient_scope_vocabulary",
                "ck_integration_principals_granted_scope_requires_ids",
                "ck_integration_principals_integration_kind_requires_binding",
                "ck_integration_principals_secret_pair_together",
            } <= names
    finally:
        conn.close()


def test_downgrade_drops_the_table_and_reupgrade_restores_it(scratch_db):
    _alembic("downgrade", BEFORE, _db_url(scratch_db))
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            assert not _table_exists(cur)
    finally:
        conn.close()

    _alembic("upgrade", HEAD, _db_url(scratch_db))
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            assert _table_exists(cur)
    finally:
        conn.close()
