"""Round-trip for 0017_access_elevations (ADR 0054 D1/D6 — Phase 12A).

Runs the real chain against a scratch database:

1. upgrade an **empty** database straight to head (the empty-DB rule) —
   ``0016_rls`` must not classify the not-yet-existing table and ``0017``
   must create it with its policies;
2. upgrade from ``0016`` on a seeded database — the table carries its CHECK
   laws (two-person, scope pairing, duration bounds, live-requester
   uniqueness), its RLS policies (the hidden-read flavor), and the runtime
   role's grants;
3. downgrade to ``0016`` — table, policies and grants are gone.
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
BEFORE = "0016_rls"
HEAD = "0017_access_elevations"
APP_ROLE = "neuronection_health_app"


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
    name = f"neuronection_health_elev_{uuid.uuid4().hex[:8]}"
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


def _connect(name: str):
    return psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=name,
    )


def test_empty_database_upgrades_straight_to_head(scratch_db):
    """The empty-DB rule: a fresh install migrates in one shot."""
    _alembic("upgrade", "head", _db_url(scratch_db))
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('access_elevations') IS NOT NULL")
            assert cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM pg_policy WHERE polrelid = 'access_elevations'::regclass"
            )
            assert cur.fetchone()[0] >= 4
    finally:
        conn.close()


def test_round_trip_with_laws_and_policies(scratch_db):
    _alembic("downgrade", BEFORE, _db_url(scratch_db))
    _alembic("upgrade", HEAD, _db_url(scratch_db))
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'access_elevations'::regclass AND contype = 'c'"
            )
            checks = {row[0] for row in cur.fetchall()}
            assert "ck_access_elevations_requester_is_not_approver" in checks
            assert "ck_access_elevations_scope_tenant_pairing" in checks
            assert "ck_access_elevations_duration_bounds" in checks
            assert "ck_access_elevations_scope_vocabulary" in checks
            assert "ck_access_elevations_status_vocabulary" in checks

            cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'access_elevations'")
            indexes = {row[0] for row in cur.fetchall()}
            assert "ix_access_elevations_tenant_status_expires" in indexes
            assert "ix_access_elevations_requested_by" in indexes
            assert "ix_access_elevations_approved_by" in indexes
            assert "uq_access_elevations_live_requester" in indexes

            cur.execute(
                "SELECT polname FROM pg_policy WHERE polrelid = 'access_elevations'::regclass"
            )
            policies = {row[0] for row in cur.fetchall()}
            assert {
                "rls_tenant_read",
                "rls_tenant_write",
                "rls_tenant_write_upd",
                "rls_tenant_write_del",
            } <= policies

            cur.execute(
                "SELECT has_table_privilege(%s, 'access_elevations', 'INSERT')",
                (APP_ROLE,),
            )
            assert cur.fetchone()[0]
    finally:
        conn.close()


def test_row_laws_are_enforced(scratch_db):
    """The CHECK/partial-unique laws hold at the row level (fresh head DB)."""
    _alembic("downgrade", BEFORE, _db_url(scratch_db))
    _alembic("upgrade", HEAD, _db_url(scratch_db))
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            tenant_id = str(uuid.uuid4())
            requester = str(uuid.uuid4())
            approver = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO tenants (id, name, slug) VALUES (%s, 't', %s)",
                (tenant_id, f"t-{tenant_id}"),
            )
            for user_id in (requester, approver):
                cur.execute(
                    "INSERT INTO users (id, email, tenant_id, role, is_active) "
                    "VALUES (%s, %s, %s, 'ADMIN', true)",
                    (user_id, f"elev-{user_id}@test.local", tenant_id),
                )

            def _insert(**overrides):
                row = {
                    "tenant": tenant_id,
                    "requested_by": requester,
                    "approved_by": None,
                    "scope": "tenant",
                    "status": "pending",
                    "permissions": '["audit:read"]',
                    "reason": "law probe",
                    "duration": 30,
                }
                row.update(overrides)
                cur.execute(
                    "INSERT INTO access_elevations (tenant_id, requested_by, approved_by, "
                    "scope, status, permissions, reason, duration_minutes) "
                    "VALUES (%(tenant)s, %(requested_by)s, %(approved_by)s, %(scope)s, "
                    "%(status)s, %(permissions)s::jsonb, %(reason)s, %(duration)s) "
                    "RETURNING id",
                    row,
                )
                return cur.fetchone()[0]

            assert _insert()

            with pytest.raises(psycopg2.IntegrityError):
                _insert(approved_by=requester, status="active")
            conn.rollback()

            with pytest.raises(psycopg2.IntegrityError):
                _insert(tenant=None)
            conn.rollback()

            with pytest.raises(psycopg2.IntegrityError):
                _insert(duration=0)
            conn.rollback()

            with pytest.raises(psycopg2.IntegrityError):
                _insert(status="settled")
            conn.rollback()

            with pytest.raises(psycopg2.IntegrityError):
                _insert()
            conn.rollback()
    finally:
        conn.close()
