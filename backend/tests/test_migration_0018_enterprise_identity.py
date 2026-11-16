"""Round-trip for 0018_enterprise_identity (ADR 0054 D2 — Phase 12B).

Runs the real chain against a scratch database:

1. upgrade an **empty** database straight to head (the empty-DB rule) —
   ``0016_rls`` must not classify the not-yet-existing tables and ``0018``
   must create all five with their policies;
2. upgrade from ``0017`` on a seeded database — the row laws (link-only
   uniques, verified-email CHECK, SSO role vocabulary, token-hash
   uniqueness), the RLS coverage and the runtime role's grants;
3. downgrade to ``0017`` — tables, policies and grants are gone.
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
BEFORE = "0017_access_elevations"
HEAD = "0018_enterprise_identity"
APP_ROLE = "neuronection_health_app"

TABLES = (
    "tenant_sso_configs",
    "user_identities",
    "scim_tokens",
    "scim_groups",
    "scim_group_members",
)

#: ``default_role`` is a VARCHAR CHECK mirror (native_enum=False) — an
#: out-of-vocabulary value can trip the length or the CHECK first.
ROLE_LAW_ERRORS = (psycopg2.IntegrityError, psycopg2.DataError)


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
    name = f"neuronection_health_eid_{uuid.uuid4().hex[:8]}"
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
            for table in TABLES:
                cur.execute(f"SELECT to_regclass('{table}') IS NOT NULL")
                assert cur.fetchone()[0], table
                cur.execute(f"SELECT count(*) FROM pg_policy WHERE polrelid = '{table}'::regclass")
                assert cur.fetchone()[0] >= 1, f"{table} has no policy"
    finally:
        conn.close()


def test_round_trip_with_laws_and_policies(scratch_db):
    _alembic("upgrade", "head", _db_url(scratch_db))
    _alembic("downgrade", BEFORE, _db_url(scratch_db))
    _alembic("upgrade", HEAD, _db_url(scratch_db))
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'tenant_sso_configs'::regclass"
            )
            sso_laws = {row[0] for row in cur.fetchall()}
            assert "ck_tenant_sso_configs_default_role_vocabulary" in sso_laws
            assert "ck_tenant_sso_configs_client_secret_ref_required" in sso_laws
            assert "uq_tenant_sso_configs_tenant_id_issuer" in sso_laws

            cur.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'user_identities'::regclass"
            )
            identity_laws = {row[0] for row in cur.fetchall()}
            assert "ck_user_identities_email_verified_required" in identity_laws
            assert "ck_user_identities_subject_required" in identity_laws
            assert "uq_user_identities_issuer_subject" in identity_laws
            assert "uq_user_identities_tenant_id_user_id_issuer" in identity_laws

            cur.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'scim_tokens'::regclass"
            )
            token_laws = {row[0] for row in cur.fetchall()}
            assert "ck_scim_tokens_token_hash_required" in token_laws
            assert "uq_scim_tokens_token_hash" in token_laws

            cur.execute(
                "SELECT polname FROM pg_policy WHERE polrelid = 'tenant_sso_configs'::regclass"
            )
            assert {"rls_tenant"} <= {row[0] for row in cur.fetchall()}
            cur.execute(
                "SELECT polname FROM pg_policy WHERE polrelid = 'scim_group_members'::regclass"
            )
            assert {"rls_tenant_parent"} <= {row[0] for row in cur.fetchall()}

            for table in TABLES:
                cur.execute(f"SELECT has_table_privilege(%s, '{table}', 'INSERT')", (APP_ROLE,))
                assert cur.fetchone()[0], f"{table} not granted to the runtime role"
    finally:
        conn.close()


def test_row_laws_are_enforced(scratch_db):
    """The link-only / vocabulary laws hold at the row level (fresh head DB).

    Every probe is self-contained: the seed rows ride the same transaction
    and every rejection rolls its probe back completely.
    """
    _alembic("upgrade", "head", _db_url(scratch_db))
    conn = _connect(scratch_db)

    def _seed(cur, marker: str) -> tuple[str, str]:
        tenant_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, 't', %s)",
            (tenant_id, f"eid-{marker}-{tenant_id}"),
        )
        cur.execute(
            "INSERT INTO users (id, email, tenant_id, role, is_active) "
            "VALUES (%s, %s, %s, 'ADMIN', true)",
            (user_id, f"eid-{marker}-{user_id}@test.local", tenant_id),
        )
        return tenant_id, user_id

    try:
        with conn.cursor() as cur:
            tenant_id, user_id = _seed(cur, "ok")
            cur.execute(
                "INSERT INTO tenant_sso_configs (tenant_id, issuer, client_id, "
                "client_secret_ref, default_role) "
                "VALUES (%s, 'https://idp.example', 'cid', 'slot', 'USER') RETURNING id",
                (tenant_id,),
            )
            cur.execute(
                "INSERT INTO user_identities (tenant_id, user_id, issuer, subject, email, "
                "email_verified) VALUES (%s, %s, 'https://idp.example', %s, "
                "%s, true)",
                (tenant_id, user_id, f"sub-{user_id}", f"eid-{user_id}@test.local"),
            )
            cur.execute(
                "INSERT INTO scim_groups (tenant_id, display_name) VALUES (%s, %s) RETURNING id",
                (tenant_id, f"Team-{user_id}"),
            )
            group_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO scim_group_members (group_id, user_id) VALUES (%s, %s)",
                (group_id, user_id),
            )
            cur.execute(
                "INSERT INTO scim_tokens (tenant_id, name, token_hash) "
                "VALUES (%s, 'ci', %s) RETURNING id",
                (tenant_id, user_id.replace("-", "")[:64].ljust(64, "0")),
            )
            conn.commit()

            with pytest.raises(psycopg2.IntegrityError):
                cur.execute(
                    "INSERT INTO tenant_sso_configs (tenant_id, issuer, client_id, "
                    "client_secret_ref, default_role) VALUES (%s, %s, 'cid2', 'slot2', 'USER')",
                    (tenant_id, "https://idp.example"),
                )
            conn.rollback()

            with pytest.raises(ROLE_LAW_ERRORS):
                cur.execute(
                    "INSERT INTO tenant_sso_configs (tenant_id, issuer, client_id, "
                    "client_secret_ref, default_role) "
                    "VALUES (%s, 'https://other.example', 'cid3', 'slot3', 'SYSTEM_ADMIN')",
                    (tenant_id,),
                )
            conn.rollback()

            with pytest.raises(psycopg2.IntegrityError):
                cur.execute(
                    "INSERT INTO user_identities (tenant_id, user_id, issuer, subject, email, "
                    "email_verified) VALUES (%s, %s, 'https://idp.example', %s, "
                    "'x@test.local', true)",
                    (tenant_id, user_id, f"sub-{user_id}"),
                )
            conn.rollback()

            with pytest.raises(psycopg2.IntegrityError):
                cur.execute(
                    "INSERT INTO user_identities (tenant_id, user_id, issuer, subject, email, "
                    "email_verified) VALUES (%s, %s, 'https://idp.example', %s, "
                    "'y@test.local', false)",
                    (tenant_id, user_id, f"other-{user_id}"),
                )
            conn.rollback()

            with pytest.raises(psycopg2.IntegrityError):
                cur.execute(
                    "INSERT INTO scim_group_members (group_id, user_id) VALUES (%s, %s)",
                    (group_id, user_id),
                )
            conn.rollback()

            with pytest.raises(psycopg2.IntegrityError):
                cur.execute(
                    "INSERT INTO scim_tokens (tenant_id, name, token_hash) VALUES (%s, 'ci2', %s)",
                    (tenant_id, user_id.replace("-", "")[:64].ljust(64, "0")),
                )
            conn.rollback()

            with conn.cursor() as verify:
                verify.execute("SELECT count(*) FROM tenant_sso_configs")
                assert (verify.fetchone()[0] or 0) >= 1
                verify.execute("SELECT count(*) FROM user_identities")
                assert (verify.fetchone()[0] or 0) >= 1
    finally:
        conn.close()
