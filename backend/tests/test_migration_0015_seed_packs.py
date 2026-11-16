"""Round-trip + constraints for 0015_seed_packs (ADR 0056, program Phase 9A).

Runs the real chain against a scratch database:

1. upgrade to ``0014_audit_chain`` — none of the six tables exist;
2. upgrade to head: every table exists, the version is pinned, and the
   constraint mesh holds — ``UNIQUE(source_id, digest)`` on versions,
   ``(entity_type, entity_id)`` uniqueness on provenance, the mirrored
   vocabulary CHECKs (kind/scope/status/mode/action/state/deferred), the
   priority range and the scope⇔tenant coherence CHECK;
3. the delete rules behave: source → versions (CASCADE), version → runs and
   deferred refs (CASCADE) and provenance (``SET NULL``), version deletion
   nulls ``seed_sources.current_version_id`` (``SET NULL``);
4. downgrade to ``0014``: the tables are gone.
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
TARGET = "0014_audit_chain"
TABLES = (
    "seed_sources",
    "seed_source_versions",
    "seed_import_runs",
    "seed_import_run_items",
    "seed_item_provenance",
    "seed_deferred_refs",
)


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


def _must_violate(conn, sql: str, params, exc: type) -> None:
    """Run ``sql`` expecting the given psycopg2 error; roll back afterwards."""
    with pytest.raises(exc):
        conn.cursor().execute(sql, params)
    conn.rollback()


def test_0015_creates_lane_with_constraints_and_downgrade_removes_it(scratch_db, scratch_url):
    _alembic("upgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            for table in TABLES:
                cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
                assert not cur.fetchone()[0], f"{table} exists before 0015"
    finally:
        conn.close()

    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        assert cur.fetchone()[0] == "0025_document_object_key"
        for table in TABLES:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
            assert cur.fetchone()[0], f"{table} missing after 0015"

        source_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO seed_sources (id, kind, uri, priority, scope) "
            "VALUES (%s, 'bundled', 'builtin://data/seeds', 0, 'system')",
            (source_id,),
        )
        version_id = str(uuid.uuid4())
        digest = uuid.uuid4().hex
        cur.execute(
            "INSERT INTO seed_source_versions (id, source_id, digest, contract_version) "
            "VALUES (%s, %s, %s, '1.0')",
            (version_id, source_id, digest),
        )
        conn.commit()

        # UNIQUE(source_id, digest) — the digest-pinned approval anchor.
        _must_violate(
            conn,
            "INSERT INTO seed_source_versions (id, source_id, digest, contract_version) "
            "VALUES (%s, %s, %s, '1.0')",
            (str(uuid.uuid4()), source_id, digest),
            psycopg2.errors.UniqueViolation,
        )

        # Vocabulary + shape CHECKs (each on its own transaction).
        _must_violate(
            conn,
            "INSERT INTO seed_sources (id, kind, uri, priority, scope) "
            "VALUES (%s, 'npm', 'x://y', 0, 'system')",
            (str(uuid.uuid4()),),
            psycopg2.errors.CheckViolation,
        )
        _must_violate(
            conn,
            "INSERT INTO seed_sources (id, kind, uri, priority, scope) "
            "VALUES (%s, 'git', 'x://y', 0, 'tenant')",
            (str(uuid.uuid4()),),
            psycopg2.errors.CheckViolation,
        )
        _must_violate(
            conn,
            "INSERT INTO seed_sources (id, kind, uri, priority, scope) "
            "VALUES (%s, 'git', 'x://y', 1000, 'system')",
            (str(uuid.uuid4()),),
            psycopg2.errors.CheckViolation,
        )
        _must_violate(
            conn,
            "INSERT INTO seed_source_versions (id, source_id, digest, contract_version, status) "
            "VALUES (%s, %s, %s, '1.0', 'merged')",
            (str(uuid.uuid4()), source_id, uuid.uuid4().hex),
            psycopg2.errors.CheckViolation,
        )
        _must_violate(
            conn,
            "INSERT INTO seed_import_runs (id, source_version_id, mode) VALUES (%s, %s, 'dry-run')",
            (str(uuid.uuid4()), version_id),
            psycopg2.errors.CheckViolation,
        )
        _must_violate(
            conn,
            "INSERT INTO seed_import_runs (id, source_version_id, mode, status) "
            "VALUES (%s, %s, 'apply', 'aborted')",
            (str(uuid.uuid4()), version_id),
            psycopg2.errors.CheckViolation,
        )
        _must_violate(
            conn,
            "INSERT INTO seed_item_provenance (id, entity_type, entity_id, state) "
            "VALUES (%s, 'medication', %s, 'deleted')",
            (str(uuid.uuid4()), str(uuid.uuid4())),
            psycopg2.errors.CheckViolation,
        )
        _must_violate(
            conn,
            "INSERT INTO seed_deferred_refs (id, source_version_id, payload, status) "
            "VALUES (%s, %s, '{}', 'lost')",
            (str(uuid.uuid4()), version_id),
            psycopg2.errors.CheckViolation,
        )

        # A run + a run item + a deferred ref + provenance for the mesh below.
        run_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO seed_import_runs (id, source_version_id, mode) VALUES (%s, %s, 'apply')",
            (run_id, version_id),
        )
        conn.commit()
        _must_violate(
            conn,
            "INSERT INTO seed_import_run_items (id, run_id, entity_type, action) "
            "VALUES (%s, %s, 'medication', 'delete')",
            (str(uuid.uuid4()), run_id),
            psycopg2.errors.CheckViolation,
        )
        cur.execute(
            "INSERT INTO seed_import_run_items (id, run_id, entity_type, entity_id, seed_key, "
            "action, content_hash) VALUES (%s, %s, 'medication', %s, 'medication:aspirin', "
            "'create', %s)",
            (str(uuid.uuid4()), run_id, str(uuid.uuid4()), uuid.uuid4().hex),
        )
        cur.execute(
            "INSERT INTO seed_deferred_refs (id, source_version_id, payload) "
            'VALUES (%s, %s, \'{"ref": "concept:influenza"}\')',
            (str(uuid.uuid4()), version_id),
        )
        entity_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO seed_item_provenance (id, entity_type, entity_id, source_version_id, "
            "seed_key, content_hash) VALUES (%s, 'medication', %s, %s, 'medication:aspirin', %s)",
            (str(uuid.uuid4()), entity_id, version_id, uuid.uuid4().hex),
        )
        conn.commit()

        # (entity_type, entity_id) uniqueness on provenance.
        _must_violate(
            conn,
            "INSERT INTO seed_item_provenance (id, entity_type, entity_id) "
            "VALUES (%s, 'medication', %s)",
            (str(uuid.uuid4()), entity_id),
            psycopg2.errors.UniqueViolation,
        )

        # Delete rules: version → runs/deferred (CASCADE), provenance
        # (SET NULL, the row survives); the source's current_version_id
        # SET NULLs; source → versions (CASCADE).
        cur.execute(
            "UPDATE seed_sources SET current_version_id = %s WHERE id = %s",
            (
                version_id,
                source_id,
            ),
        )
        conn.commit()
        cur.execute("DELETE FROM seed_source_versions WHERE id = %s", (version_id,))
        conn.commit()
        cur.execute("SELECT current_version_id FROM seed_sources WHERE id = %s", (source_id,))
        assert cur.fetchone()[0] is None, "current_version_id must SET NULL"
        cur.execute("SELECT count(*) FROM seed_import_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == 0, "runs must cascade with their version"
        cur.execute(
            "SELECT count(*) FROM seed_deferred_refs WHERE source_version_id = %s", (version_id,)
        )
        assert cur.fetchone()[0] == 0, "deferred refs must cascade with their version"
        cur.execute(
            "SELECT source_version_id, seed_key FROM seed_item_provenance WHERE entity_id = %s",
            (entity_id,),
        )
        assert cur.fetchone() == (
            None,
            "medication:aspirin",
        ), "provenance must survive its version (SET NULL)"

        cur.execute("DELETE FROM seed_sources WHERE id = %s", (source_id,))
        conn.commit()
        cur.execute("SELECT count(*) FROM seed_source_versions WHERE source_id = %s", (source_id,))
        assert cur.fetchone()[0] == 0, "versions must cascade with their source"
    finally:
        conn.close()

    _alembic("downgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            for table in TABLES:
                cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
                assert not cur.fetchone()[0], f"{table} survived the downgrade"
    finally:
        conn.close()
