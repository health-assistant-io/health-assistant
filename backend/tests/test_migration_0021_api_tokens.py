"""Round-trip for 0021_api_tokens (program 12C2B).

Runs the real chain against a scratch database:

1. upgrade an empty database to head — ``api_tokens`` exists with its lookup
   keys (prefix/hash uniqueness), permission/patient-scope JSONB columns,
   cascade FKs, indexes and its NOT-NULL-tenant RLS policy;
2. downgrade to ``0020`` — the table is gone;
3. upgrade back to ``0021`` — it returns.

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
BEFORE = "0020_webauthn"
HEAD = "0021_api_tokens"


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
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'api_tokens'"
    )
    return {row[0] for row in cur.fetchall()}


def _table_exists(cur) -> bool:
    cur.execute("SELECT to_regclass('public.api_tokens')")
    return cur.fetchone()[0] is not None


@pytest.fixture(scope="module")
def scratch_db():
    name = f"neuronection_health_apitokens_{uuid.uuid4().hex[:8]}"
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
                "token_prefix",
                "token_hash",
                "permissions",
                "patient_scope",
                "patient_ids",
                "expires_at",
                "last_used_at",
                "revoked_at",
                "rotated_from",
                "created_by",
                "created_at",
                "updated_at",
            } <= _columns(cur)
            cur.execute(
                "SELECT count(*) FROM pg_constraint WHERE conname IN "
                "('uq_api_tokens_token_prefix', 'uq_api_tokens_token_hash')"
            )
            assert cur.fetchone()[0] == 2
            cur.execute(
                "SELECT count(*) FROM pg_indexes WHERE tablename = 'api_tokens' "
                "AND indexname = 'ix_api_tokens_tenant_owner'"
            )
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'api_tokens'::regclass")
            assert cur.fetchone()[0] is True
            cur.execute("SELECT polname FROM pg_policy WHERE polrelid = 'api_tokens'::regclass")
            assert {row[0] for row in cur.fetchall()} == {"rls_tenant"}
            cur.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'api_tokens'::regclass AND contype = 'c'"
            )
            definitions = dict(cur.fetchall())
            assert "patient_scope_vocabulary" in " ".join(definitions)
            assert "granted_scope_requires_ids" in " ".join(definitions)
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
