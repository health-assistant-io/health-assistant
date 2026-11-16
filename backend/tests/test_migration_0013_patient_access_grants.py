"""Round-trip for 0013_patient_access_grants (the care-team grant lane).

Runs the real chain against a scratch database:

1. upgrade to ``0012_vaccine_target_edges`` — the table does not exist;
2. upgrade to head: the table exists with its typed columns, the closed
   vocabularies mirrored in CHECK constraints, the exactly-one-grantee
   CHECK, the cascade FKs, the plain indexes and the two partial unique
   indexes (one ACTIVE grant per grantee identity per patient);
3. constraint behavior: a second active grant for the same identity
   violates the partial unique index; a revoked first grant frees the slot;
   a grant with both grantee columns set violates the exactly-one CHECK;
4. downgrade to ``0012``: the table is gone and the previous shape is
   intact.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import psycopg2
import pytest

from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[1]
TARGET = "0012_vaccine_target_edges"

TABLE = "patient_access_grants"


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


def _indexes(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", (table,))
        return {row[0] for row in cur.fetchall()}


def _seed_identity(conn, db: str) -> tuple[str, str, str]:
    """One tenant + user + patient to hang grants on; returns their ids."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s) RETURNING id",
            (str(uuid.uuid4()), "Grant Mig Tenant", f"grant-mig-{uuid.uuid4().hex[:8]}"),
        )
        tenant_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO users (id, email, full_name, role, tenant_id, is_active) "
            "VALUES (%s, %s, %s, 'USER', %s, true) RETURNING id",
            (str(uuid.uuid4()), f"owner-{uuid.uuid4().hex[:8]}@mig.test", "Owner", tenant_id),
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO fhir_patients (id, tenant_id, user_id, name, gender) "
            "VALUES (%s, %s, %s, %s, 'UNKNOWN') RETURNING id",
            (str(uuid.uuid4()), tenant_id, user_id, '{"family": "Mig"}'),
        )
        patient_id = cur.fetchone()[0]
    conn.commit()
    return tenant_id, user_id, patient_id


def _insert_grant(conn, tenant_id, patient_id, user_id, status="active", permission="read"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO patient_access_grants "
            "(id, tenant_id, patient_id, grantee_user_id, permission, source, status) "
            "VALUES (%s, %s, %s, %s, %s, 'admin', %s) RETURNING id",
            (
                str(uuid.uuid4()),
                tenant_id,
                patient_id,
                user_id,
                permission,
                status,
            ),
        )
        grant_id = cur.fetchone()[0]
    conn.commit()
    return grant_id


def test_0013_adds_grant_table_and_downgrade_removes_it(scratch_db, scratch_url):
    _alembic("upgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        assert not _table_exists(conn, TABLE), f"{TABLE} exists before 0013"
    finally:
        conn.close()

    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        columns = _columns(conn, TABLE)
        for column, dtype in (
            ("id", "uuid"),
            ("tenant_id", "uuid"),
            ("patient_id", "uuid"),
            ("grantee_user_id", "uuid"),
            ("grantee_doctor_id", "uuid"),
            ("permission", "character varying"),
            ("source", "character varying"),
            ("status", "character varying"),
            ("granted_by", "uuid"),
            ("granted_at", "timestamp with time zone"),
            ("accepted_at", "timestamp with time zone"),
            ("expires_at", "timestamp with time zone"),
            ("reason", "text"),
        ):
            assert columns.get(column) == dtype, f"{column}: {columns.get(column)} != {dtype}"

        checks = _constraint_defs(conn, TABLE)
        assert any(
            "grantee_user_id IS NULL) <> (grantee_doctor_id IS NULL" in body
            for body in checks.values()
        ), f"exactly-one-grantee CHECK missing: {list(checks.values())}"
        permission_checks = [b for name, b in checks.items() if "permission" in name]
        assert permission_checks, "permission vocabulary CHECK missing"
        assert "'read'" in permission_checks[0] and "'write'" in permission_checks[0]
        source_checks = [b for name, b in checks.items() if "source" in name]
        assert source_checks, "source vocabulary CHECK missing"
        assert "break_glass" not in source_checks[0], "break_glass must not be a grant source"
        assert "'guardian'" in source_checks[0]
        status_checks = [b for name, b in checks.items() if "status" in name]
        assert status_checks, "status vocabulary CHECK missing"
        assert "'declined'" in status_checks[0]

        fks = {name: body for name, body in checks.items() if body.startswith("FOREIGN KEY")}
        assert any("fhir_patients" in body and "CASCADE" in body for body in fks.values())
        assert any("users" in body and "grantee_user_id" in body for body in fks.values())
        assert any("doctors" in body for body in fks.values())

        indexes = _indexes(conn, TABLE)
        for expected in (
            "uq_patient_access_grants_active_user",
            "uq_patient_access_grants_active_doctor",
            "ix_patient_access_grants_tenant_patient",
            "ix_patient_access_grants_tenant_grantee_user",
            "ix_patient_access_grants_tenant_grantee_doctor",
            "ix_patient_access_grants_expires_at",
        ):
            assert expected in indexes, f"index {expected} missing: {sorted(indexes)}"
    finally:
        conn.close()

    _alembic("downgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        assert not _table_exists(conn, TABLE), f"{TABLE} survived the downgrade"
    finally:
        conn.close()


def test_0013_partial_unique_and_grantee_exclusive_enforced(scratch_db, scratch_url):
    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        tenant_id, owner_id, patient_id = _seed_identity(conn, scratch_db)

        cur = conn.cursor()

        def _count_other_grantees() -> int:
            cur.execute(
                "SELECT count(*) FROM users WHERE tenant_id = %s AND id <> %s",
                (tenant_id, owner_id),
            )
            return cur.fetchone()[0]

        if _count_other_grantees() == 0:
            cur.execute(
                "INSERT INTO users (id, email, full_name, role, tenant_id, is_active) "
                "VALUES (%s, %s, %s, 'USER', %s, true)",
                (
                    str(uuid.uuid4()),
                    f"second-{uuid.uuid4().hex[:8]}@mig.test",
                    "Second",
                    tenant_id,
                ),
            )
            conn.commit()

        cur.execute(
            "SELECT id FROM users WHERE tenant_id = %s AND id <> %s LIMIT 1",
            (tenant_id, owner_id),
        )
        second_user_id = cur.fetchone()[0]

        grant_id = _insert_grant(conn, tenant_id, patient_id, owner_id, status="active")

        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO patient_access_grants "
                "(id, tenant_id, patient_id, grantee_user_id, permission, source, status) "
                "VALUES (%s, %s, %s, %s, 'read', 'admin', 'active')",
                (str(uuid.uuid4()), tenant_id, patient_id, owner_id),
            )
        conn.rollback()

        cur.execute(
            "UPDATE patient_access_grants SET status = 'revoked' WHERE id = %s",
            (grant_id,),
        )
        conn.commit()
        revoked = _insert_grant(conn, tenant_id, patient_id, owner_id, status="active")
        assert revoked, "a revoked prior grant must free the active slot"

        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "INSERT INTO patient_access_grants "
                "(id, tenant_id, patient_id, grantee_user_id, grantee_doctor_id, "
                "permission, source, status) "
                "VALUES (%s, %s, %s, %s, %s, 'read', 'admin', 'active')",
                (str(uuid.uuid4()), tenant_id, patient_id, second_user_id, str(uuid.uuid4())),
            )
        conn.rollback()

        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "INSERT INTO patient_access_grants "
                "(id, tenant_id, patient_id, permission, source, status) "
                "VALUES (%s, %s, %s, 'read', 'admin', 'active')",
                (str(uuid.uuid4()), tenant_id, patient_id),
            )
        conn.rollback()

        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "INSERT INTO patient_access_grants "
                "(id, tenant_id, patient_id, grantee_user_id, permission, source, status) "
                "VALUES (%s, %s, %s, %s, 'eleva', 'admin', 'active')",
                (str(uuid.uuid4()), tenant_id, patient_id, second_user_id),
            )
        conn.rollback()

        future = datetime.now(UTC) + timedelta(days=7)
        cur.execute(
            "SELECT count(*) FROM patient_access_grants WHERE tenant_id = %s",
            (tenant_id,),
        )
        before = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO patient_access_grants "
            "(id, tenant_id, patient_id, grantee_user_id, permission, source, status, "
            "expires_at) VALUES (%s, %s, %s, %s, 'write', 'care_team', 'active', %s)",
            (str(uuid.uuid4()), tenant_id, patient_id, second_user_id, future),
        )
        conn.commit()
        cur.execute(
            "SELECT count(*) FROM patient_access_grants WHERE tenant_id = %s",
            (tenant_id,),
        )
        assert cur.fetchone()[0] == before + 1
    finally:
        conn.close()
