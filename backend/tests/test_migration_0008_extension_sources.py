"""Round-trip for 0008_extension_sources (the imported-extension lane).

Runs the real chain against a scratch database:

1. upgrade to ``0007_data_lifecycle`` — none of the four tables exists;
2. upgrade to head: all four exist with their typed columns, the closed
   vocabularies mirrored in CHECK constraints, the cascade FKs and the
   uniqueness rules (one row per ``(source_id, digest)``, one consent
   decision per ``(user, version, capability)``, one posture per
   ``(version, host_profile)``);
3. downgrade to ``0007``: all four are gone and the previous shape is
   intact;
4. a fresh database walks ``base → head → base`` (the empty-DB rule).
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
TARGET = "0007_data_lifecycle"

_VERSION_TABLES = (
    "extension_sources",
    "extension_source_versions",
    "extension_consents",
    "extension_isolation_posture",
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


def test_0008_adds_source_lane_tables_and_downgrade_removes_them(scratch_db, scratch_url):
    _alembic("upgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        for table in _VERSION_TABLES:
            assert not _table_exists(conn, table), f"{table} exists before 0008"
    finally:
        conn.close()

    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        sources = _columns(conn, "extension_sources")
        for column in (
            "tenant_id",
            "kind",
            "uri",
            "ref_spec",
            "auth_secret_ref",
            "trust_tier",
            "enabled",
            "last_check_at",
            "last_error",
        ):
            assert column in sources, f"extension_sources.{column} missing"

        versions = _columns(conn, "extension_source_versions")
        for column in (
            "source_id",
            "commit_sha",
            "digest",
            "manifest_digest",
            "envelope",
            "status",
            "reason",
            "first_seen_at",
            "approved_by",
            "contract_version",
        ):
            assert column in versions, f"extension_source_versions.{column} missing"
        assert versions["envelope"] == "jsonb"

        consents = _columns(conn, "extension_consents")
        for column in (
            "tenant_id",
            "user_id",
            "version_id",
            "capability",
            "decision",
            "scope_json",
            "decided_at",
            "decided_by",
        ):
            assert column in consents, f"extension_consents.{column} missing"
        assert consents["scope_json"] == "jsonb"

        posture = _columns(conn, "extension_isolation_posture")
        for column in (
            "version_id",
            "host_profile",
            "requested_tier",
            "derived_tier",
            "posture",
            "probed_at",
        ):
            assert column in posture, f"extension_isolation_posture.{column} missing"
        assert posture["posture"] == "jsonb"

        source_checks = " ".join(_constraint_defs(conn, "extension_sources").values())
        for value in ("git", "file", "registry", "T2", "T3"):
            assert value in source_checks, f"source vocabulary missing {value}"

        version_checks = " ".join(_constraint_defs(conn, "extension_source_versions").values())
        for value in ("pending", "approved", "rejected", "revoked"):
            assert value in version_checks, f"version vocabulary missing {value}"
        assert any(
            "UNIQUE" in definition and "digest" in definition
            for definition in _constraint_defs(conn, "extension_source_versions").values()
        ), "versions must be unique per (source_id, digest)"

        consent_checks = " ".join(_constraint_defs(conn, "extension_consents").values())
        for value in ("granted", "denied"):
            assert value in consent_checks, f"consent vocabulary missing {value}"
        assert any(
            "UNIQUE" in definition and "capability" in definition
            for definition in _constraint_defs(conn, "extension_consents").values()
        ), "consents must be unique per (user, version, capability)"

        posture_checks = " ".join(_constraint_defs(conn, "extension_isolation_posture").values())
        for value in ("T0", "T1", "T2", "T3"):
            assert value in posture_checks, f"tier ladder missing {value}"

        cascades = " ".join(_constraint_defs(conn, "extension_source_versions").values())
        assert "ON DELETE CASCADE" in cascades, "versions must cascade with their source"
    finally:
        conn.close()

    _alembic("downgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        for table in _VERSION_TABLES:
            assert not _table_exists(conn, table), f"{table} survives the downgrade"
        assert _table_exists(conn, "erasure_requests")
        assert _table_exists(conn, "fhir_patients")
    finally:
        conn.close()


def test_fresh_database_walks_base_head_base(scratch_db, scratch_url):
    _alembic("downgrade", "base", scratch_url)

    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            )
            assert cur.fetchall() == [], "downgrade base must empty a fresh database"
    finally:
        conn.close()

    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        for table in _VERSION_TABLES:
            assert _table_exists(conn, table), f"{table} missing after fresh upgrade head"
    finally:
        conn.close()
