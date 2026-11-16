"""Round-trip + backfill for 0014_audit_chain (ADR 0054 D3, Phase 4A).

Runs the real chain against a scratch database:

1. upgrade to ``0013_patient_access_grants`` — the chain columns and the
   head table do not exist;
2. seed legacy ``audit_events`` rows (two tenants + the system chain) in
   the 0013 shape, upgrade to head: every row carries ``chain_key`` /
   ``seq`` / ``prev_hash`` / ``entry_hash``, backfilled with the same
   canonical function the appender uses, and one head row per non-empty
   chain;
3. the unique ``(chain_key, seq)`` constraint rejects a duplicate
   sequence;
4. downgrade to ``0013``: the columns and the head table are gone, the
   rows survive.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import psycopg2
import pytest

from app.core.config import settings
from app.services.audit.audit_chain import GENESIS_HASH, compute_entry_hash

BACKEND = Path(__file__).resolve().parents[1]
TARGET = "0013_patient_access_grants"
TABLE = "audit_events"
HEAD_TABLE = "audit_chain_heads"


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
        f"postgresql+psycopg2://{user}:{password}@"
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


def _seed_legacy_rows(conn) -> str:
    """Two tenants + legacy rows; returns the first tenant's id.

    Three rows for tenant A (the chain walked in assertions), one row for
    tenant B (per-tenant separation) and two system rows (NULL tenant).
    """
    cur = conn.cursor()
    tenant_ids = []
    for name in ("Chain A", "Chain B"):
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s) RETURNING id",
            (str(uuid.uuid4()), name, f"chain-{uuid.uuid4().hex[:8]}"),
        )
        tenant_ids.append(str(cur.fetchone()[0]))
    for i in range(3):
        rid, created = str(uuid.uuid4()), datetime(2026, 1, 1, 12, 0, i, tzinfo=UTC)
        cur.execute(
            "INSERT INTO audit_events (id, tenant_id, action, resource_type, "
            "resource_id, outcome, created_at) VALUES (%s, %s, %s, 'Patient', %s, 'ok', %s)",
            (str(uuid.uuid4()), tenant_ids[0], f"legacy_{i}", rid, created),
        )
    cur.execute(
        "INSERT INTO audit_events (id, tenant_id, action, resource_type, "
        "outcome, created_at) VALUES (%s, %s, 'tenant_b_event', 'Patient', 'ok', %s)",
        (str(uuid.uuid4()), tenant_ids[1], datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)),
    )
    for i in range(2):
        created = datetime(2026, 1, 2, 8, 0, i, tzinfo=UTC)
        cur.execute(
            "INSERT INTO audit_events (id, tenant_id, action, resource_type, "
            "outcome, created_at) VALUES (%s, NULL, %s, 'System', 'ok', %s)",
            (str(uuid.uuid4()), f"sys_{i}", created),
        )
    conn.commit()
    return tenant_ids[0]


def test_0014_backfills_chain_and_downgrade_removes_it(scratch_db, scratch_url):
    _alembic("upgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'audit_events' AND column_name IN "
                "('chain_key', 'seq', 'prev_hash', 'entry_hash')"
            )
            assert cur.fetchone()[0] == 0, "chain columns exist before 0014"
            cur.execute("SELECT to_regclass('public.audit_chain_heads') IS NOT NULL")
            assert not cur.fetchone()[0], "head table exists before 0014"
        _seed_legacy_rows(conn)
    finally:
        conn.close()

    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        assert cur.fetchone()[0] == "0025_document_object_key"

        cur.execute(
            "SELECT chain_key, seq, prev_hash, entry_hash, action, resource_id, "
            "outcome, created_at FROM audit_events WHERE tenant_id IS NOT NULL "
            "ORDER BY chain_key, seq"
        )
        tenant_rows = cur.fetchall()
        assert len(tenant_rows) == 4
        prev = GENESIS_HASH
        chain = None
        for ck, seq, ph, eh, action, rid, outcome, created in tenant_rows:
            if ck != chain:
                chain = ck
                prev = GENESIS_HASH
                expected_seq = 1
            assert seq == expected_seq, f"chain {ck}: seq {seq} != {expected_seq}"
            assert ph == prev, f"row {seq} does not link to its predecessor"
            assert eh == compute_entry_hash(
                chain_key=ck,
                seq=seq,
                prev_hash=ph,
                action=action,
                resource_type="Patient",
                resource_id=rid,
                outcome=outcome,
                created_at=created,
            )
            prev = eh
            expected_seq += 1

        cur.execute(
            "SELECT chain_key, seq, prev_hash, entry_hash FROM audit_events "
            "WHERE tenant_id IS NULL ORDER BY seq"
        )
        system_rows = cur.fetchall()
        assert [row[1] for row in system_rows] == [1, 2]
        assert system_rows[0][2] == GENESIS_HASH
        assert system_rows[1][2] == system_rows[0][3]
        assert all(row[0] == "system" for row in system_rows)

        cur.execute("SELECT chain_key, seq, tenant_id FROM audit_chain_heads")
        heads = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
        tenant_a_key = next(row[0] for row in tenant_rows if row[4] == "legacy_0")
        tenant_b_key = next(row[0] for row in tenant_rows if row[4] == "tenant_b_event")
        assert tenant_a_key != tenant_b_key, "each tenant must own its chain"
        assert heads[tenant_a_key][0] == 3 and heads[tenant_b_key][0] == 1
        assert heads[tenant_a_key][1] is not None and heads[tenant_b_key][1] is not None
        assert heads["system"][0] == 2 and heads["system"][1] is None

        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO audit_events (id, tenant_id, action, resource_type, "
                "outcome, created_at, chain_key, seq, prev_hash, entry_hash) "
                "VALUES (%s, %s, 'dup', 'Patient', 'ok', now(), %s, 1, %s, %s)",
                (
                    str(uuid.uuid4()),
                    heads[tenant_a_key][1],
                    tenant_a_key,
                    GENESIS_HASH,
                    "0" * 64,
                ),
            )
        conn.rollback()
    finally:
        conn.close()

    _alembic("downgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'audit_events' AND column_name IN "
                "('chain_key', 'seq', 'prev_hash', 'entry_hash')"
            )
            assert cur.fetchone()[0] == 0, "chain columns survived the downgrade"
            cur.execute("SELECT to_regclass('public.audit_chain_heads') IS NOT NULL")
            assert not cur.fetchone()[0], "head table survived the downgrade"
            cur.execute("SELECT count(*) FROM audit_events")
            assert cur.fetchone()[0] == 6, "the rows themselves must survive"
    finally:
        conn.close()
