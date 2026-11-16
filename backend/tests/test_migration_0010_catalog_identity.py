"""Round-trip + constraint laws for 0010_catalog_identity (ADR 0055).

Runs the real chain against a scratch database:

1. upgrade ``0009 → head``: the identity columns, the per-tenant-scope unique
   indexes, the vaccine slug index and the scope CHECKs exist;
2. the laws raise: duplicate ``seed_key`` / code / slug / approved edge within
   one tenant scope; scope CHECK violations (system with a tenant, tenant or
   user scope without a tenant, user scope without a creator);
3. the overlay cases stay legal: the same ``seed_key`` / code / slug in the
   system scope and a tenant scope (and across two tenants), NULL
   ``seed_key`` rows, non-approved duplicate edges;
4. downgrade to ``0009``: columns, CHECKs and new indexes are gone and the
   blanket edge uniqueness is restored; a fresh database walks the chain;
5. a pre-0010 database holding a scope-violating row aborts the upgrade
   loudly at the ``VALIDATE CONSTRAINT`` step (fail-loud, no users).
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
from psycopg2 import errors as pg_errors

from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[1]
TARGET = "0009_calendar_bindings"


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


def _alembic_run(action: str, revision: str, url: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HA_DATABASE_URL": url,
        "HA_APP_ENV": "test",
        "PYTHONPATH": str(BACKEND),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", action, revision],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )


def _alembic(action: str, revision: str, url: str) -> None:
    result = _alembic_run(action, revision, url)
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


@pytest.fixture(scope="module")
def head_db(scratch_db, scratch_url):
    """The scratch database upgraded to head (after the round-trip test)."""
    _alembic("upgrade", "head", scratch_url)
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (name, slug) VALUES (%s, %s) RETURNING id", ("t0", "t0")
            )
            tenant_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO tenants (name, slug) VALUES (%s, %s) RETURNING id", ("t1", "t1")
            )
            other_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return {"db": scratch_db, "tenant_id": tenant_id, "other_tenant_id": other_id}


def _connect(name: str):
    return psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=name,
    )


def _columns(conn, table: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        return dict(cur.fetchall())


def _indexes(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s",
            (table,),
        )
        return {row[0] for row in cur.fetchall()}


def _checks(conn, table: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND contype = 'c'",
            (f"public.{table}",),
        )
        return dict(cur.fetchall())


def _insert(conn, sql: str, params: tuple) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()


def _assert_raises(conn, sql: str, params: tuple, match: str) -> None:
    with conn.cursor() as cur, pytest.raises(pg_errors.IntegrityError, match=match):
        cur.execute(sql, params)
    conn.rollback()


MED_INSERT = (
    "INSERT INTO medication_catalog (name, scope, tenant_id, seed_key, coding_system, code) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)


def test_upgrade_adds_identity_shape_and_downgrade_restores(scratch_db, scratch_url):
    _alembic("upgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        for table in ("medication_catalog", "allergy_catalog", "vaccine_catalog"):
            assert "seed_key" not in _columns(conn, table), f"{table}.seed_key exists before 0010"
        assert "ix_concept_edges_unique" in _indexes(conn, "concept_edges")
        assert "ix_vaccine_catalog_slug_tenant" not in _indexes(conn, "vaccine_catalog")
    finally:
        conn.close()

    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        for table in (
            "medication_catalog",
            "allergy_catalog",
            "vaccine_catalog",
            "biomarker_definitions",
            "concepts",
        ):
            columns = _columns(conn, table)
            assert columns.get("seed_key") == "character varying", f"{table}.seed_key missing"
            assert f"ix_{table}_seed_key_tenant" in _indexes(conn, table)
        for table in ("medication_catalog", "allergy_catalog"):
            columns = _columns(conn, table)
            assert columns.get("coding_system") == "character varying"
            assert columns.get("code") == "character varying"
            assert f"ix_{table}_code_tenant" in _indexes(conn, table)
        assert "ix_vaccine_catalog_slug_tenant" in _indexes(conn, "vaccine_catalog")

        edge_indexes = _indexes(conn, "concept_edges")
        assert "ix_concept_edges_approved_unique" in edge_indexes
        assert "ix_concept_edges_unique" not in edge_indexes

        checks = _checks(conn, "medication_catalog")
        for name in (
            "ck_medication_catalog_scope_system_no_tenant",
            "ck_medication_catalog_scope_scoped_has_tenant",
            "ck_medication_catalog_scope_user_has_creator",
        ):
            assert name in checks, f"{name} missing: {sorted(checks)}"
        assert "ck_concepts_scope_system_no_tenant" in _checks(conn, "concepts")
        assert "ck_biomarker_definitions_scope_user_has_creator" in _checks(
            conn, "biomarker_definitions"
        )
    finally:
        conn.close()

    _alembic("downgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        for table in (
            "medication_catalog",
            "allergy_catalog",
            "vaccine_catalog",
            "biomarker_definitions",
            "concepts",
        ):
            assert "seed_key" not in _columns(conn, table), f"{table}.seed_key survives downgrade"
            assert f"ix_{table}_seed_key_tenant" not in _indexes(conn, table)
        assert "ck_medication_catalog_scope_system_no_tenant" not in _checks(
            conn, "medication_catalog"
        )
        assert "ix_vaccine_catalog_slug_tenant" not in _indexes(conn, "vaccine_catalog")
        edge_indexes = _indexes(conn, "concept_edges")
        assert "ix_concept_edges_unique" in edge_indexes
        assert "ix_concept_edges_approved_unique" not in edge_indexes
    finally:
        conn.close()

    _alembic("upgrade", "head", scratch_url)


def test_fresh_database_walks_base_head_base(scratch_db, scratch_url):
    _alembic("downgrade", "base", scratch_url)
    _alembic("upgrade", "head", scratch_url)

    conn = _connect(scratch_db)
    try:
        assert "seed_key" in _columns(conn, "concepts")
        assert "ix_concept_edges_approved_unique" in _indexes(conn, "concept_edges")
    finally:
        conn.close()


def test_duplicate_seed_key_in_same_scope_raises(head_db):
    conn = _connect(head_db["db"])
    try:
        _insert(
            conn,
            MED_INSERT,
            ("Aspirin A", "tenant", head_db["tenant_id"], "med:aspirin", "rxnorm", "1191"),
        )
        _assert_raises(
            conn,
            MED_INSERT,
            ("Aspirin dup", "tenant", head_db["tenant_id"], "med:aspirin", "rxnorm", "9999"),
            "ix_medication_catalog_seed_key_tenant",
        )
        _insert(conn, MED_INSERT, ("Aspirin sys", "system", None, "med:aspirin", None, None))
        _assert_raises(
            conn,
            MED_INSERT,
            ("Aspirin sys dup", "system", None, "med:aspirin", None, None),
            "ix_medication_catalog_seed_key_tenant",
        )
    finally:
        conn.close()


def test_seed_key_overlay_and_null_rows_are_unconstrained(head_db):
    """System + tenant may share a key (overlay precedence, ADR-0056); NULL
    seed_key rows (user-created) stay outside the curated identity."""
    conn = _connect(head_db["db"])
    try:
        _insert(conn, MED_INSERT, ("Overlay med", "system", None, "med:overlay", None, None))
        _insert(
            conn,
            MED_INSERT,
            ("Overlay med t", "tenant", head_db["tenant_id"], "med:overlay", None, None),
        )
        _insert(conn, MED_INSERT, ("User med 1", "tenant", head_db["tenant_id"], None, None, None))
        _insert(conn, MED_INSERT, ("User med 2", "tenant", head_db["tenant_id"], None, None, None))
    finally:
        conn.close()


def test_seed_key_shared_across_tenants_and_system(head_db):
    """Uniqueness is per tenant scope: tenant A, tenant B and the system row
    may each hold the same ``seed_key`` (overlay law, ADR-0056) while two rows
    in the same tenant scope still collide."""
    conn = _connect(head_db["db"])
    try:
        _insert(
            conn,
            MED_INSERT,
            ("Shared tA", "tenant", head_db["tenant_id"], "med:shared-across-scopes", None, None),
        )
        _insert(
            conn,
            MED_INSERT,
            (
                "Shared tB",
                "tenant",
                head_db["other_tenant_id"],
                "med:shared-across-scopes",
                None,
                None,
            ),
        )
        _insert(
            conn, MED_INSERT, ("Shared sys", "system", None, "med:shared-across-scopes", None, None)
        )
        _assert_raises(
            conn,
            MED_INSERT,
            (
                "Shared tA dup",
                "tenant",
                head_db["tenant_id"],
                "med:shared-across-scopes",
                None,
                None,
            ),
            "ix_medication_catalog_seed_key_tenant",
        )
        _assert_raises(
            conn,
            MED_INSERT,
            (
                "Shared tB dup",
                "tenant",
                head_db["other_tenant_id"],
                "med:shared-across-scopes",
                None,
                None,
            ),
            "ix_medication_catalog_seed_key_tenant",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM medication_catalog WHERE seed_key = %s",
                ("med:shared-across-scopes",),
            )
            assert cur.fetchone()[0] == 3
    finally:
        conn.close()


def test_duplicate_code_in_same_scope_raises_and_overlay_allowed(head_db):
    conn = _connect(head_db["db"])
    try:
        _insert(conn, MED_INSERT, ("Code A", "tenant", head_db["tenant_id"], None, "rxnorm", "31"))
        _assert_raises(
            conn,
            MED_INSERT,
            ("Code dup", "tenant", head_db["tenant_id"], None, "rxnorm", "31"),
            "ix_medication_catalog_code_tenant",
        )
        _insert(conn, MED_INSERT, ("Code overlay", "system", None, None, "rxnorm", "31"))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM medication_catalog WHERE coding_system = 'rxnorm' "
                "AND code = '31'"
            )
            assert cur.fetchone()[0] == 2
        conn.rollback()
    finally:
        conn.close()


def test_vaccine_slug_duplicate_in_same_scope_raises_and_overlay_allowed(head_db):
    conn = _connect(head_db["db"])
    insert = (
        "INSERT INTO vaccine_catalog (slug, name, scope, tenant_id, seed_key) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    try:
        _insert(conn, insert, ("mmr", "MMR t", "tenant", head_db["tenant_id"], None))
        _assert_raises(
            conn,
            insert,
            ("mmr", "MMR dup", "tenant", head_db["tenant_id"], None),
            "ix_vaccine_catalog_slug_tenant",
        )
        _insert(conn, insert, ("mmr", "MMR sys", "system", None, "vaccine:mmr"))
        _assert_raises(
            conn,
            insert,
            ("mmr-sys", "MMR sys dup", "system", None, "vaccine:mmr"),
            "ix_vaccine_catalog_seed_key_tenant",
        )
    finally:
        conn.close()


def test_seed_key_unique_on_concepts(head_db):
    conn = _connect(head_db["db"])
    insert = (
        "INSERT INTO concepts (slug, name, scope, tenant_id, seed_key, aliases, status, "
        "display_order) VALUES (%s, %s, %s, %s, %s, '[]'::jsonb, 'active', 0)"
    )
    try:
        _insert(conn, insert, ("concept-a", "Concept A", "system", None, "concept:concept-a"))
        _assert_raises(
            conn,
            insert,
            ("concept-a-dup", "Concept dup", "system", None, "concept:concept-a"),
            "ix_concepts_seed_key_tenant",
        )
    finally:
        conn.close()


def test_scope_check_violations_raise(head_db):
    conn = _connect(head_db["db"])
    try:
        _assert_raises(
            conn,
            MED_INSERT,
            ("System with tenant", "system", head_db["tenant_id"], None, None, None),
            "scope_system_no_tenant",
        )
        _assert_raises(
            conn,
            MED_INSERT,
            ("Tenant without tenant", "tenant", None, None, None, None),
            "scope_scoped_has_tenant",
        )
        _assert_raises(
            conn,
            MED_INSERT,
            ("User without creator", "user", head_db["tenant_id"], None, None, None),
            "scope_user_has_creator",
        )
        _insert(
            conn,
            "INSERT INTO medication_catalog (name, scope, tenant_id, created_by) "
            "VALUES (%s, %s, %s, %s)",
            ("User with creator", "user", head_db["tenant_id"], str(uuid.uuid4())),
        )
    finally:
        conn.close()


def test_scope_checks_on_concepts_and_biomarkers(head_db):
    conn = _connect(head_db["db"])
    try:
        _assert_raises(
            conn,
            "INSERT INTO concepts (slug, name, scope, tenant_id, aliases, status, display_order) "
            "VALUES (%s, %s, %s, %s, '[]'::jsonb, 'active', 0)",
            ("scoped-no-tenant", "x", "tenant", None),
            "ck_concepts_scope_scoped_has_tenant",
        )
        _assert_raises(
            conn,
            "INSERT INTO biomarker_definitions (slug, name, coding_system, scope, tenant_id, "
            "aliases, is_telemetry) VALUES (%s, %s, %s, %s, %s, '[]'::jsonb, FALSE)",
            ("biomarker-x", "x", "LOINC", "system", head_db["tenant_id"]),
            "ck_biomarker_definitions_scope_system_no_tenant",
        )
    finally:
        conn.close()


def test_approved_edge_dup_raises_and_non_approved_allowed(head_db):
    conn = _connect(head_db["db"])
    src = str(uuid.uuid4())
    dst = str(uuid.uuid4())
    insert = (
        "INSERT INTO concept_edges (src_type, src_id, dst_type, dst_id, relation, status, "
        "tenant_id, source) VALUES ('concept', %s, 'concept', %s, 'TREATS', %s, %s, 'manual')"
    )
    try:
        _insert(conn, insert, (src, dst, "approved", None))
        _assert_raises(
            conn,
            insert,
            (src, dst, "approved", None),
            "ix_concept_edges_approved_unique",
        )
        _insert(conn, insert, (src, dst, "proposed", None))
        _insert(conn, insert, (src, dst, "proposed", None))
        _insert(conn, insert, (src, dst, "approved", head_db["tenant_id"]))
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM concept_edges WHERE src_id = %s", (src,))
            assert cur.fetchone()[0] == 4
        conn.rollback()
    finally:
        conn.close()


def test_upgrade_dedupes_approved_duplicates_keeps_earliest(head_db):
    """A database whose blanket edge index predates 0010 (or is gone) can hold
    approved duplicates — the upgrade dedupes them (earliest row kept) before
    creating ``ix_concept_edges_approved_unique``."""
    src = str(uuid.uuid4())
    dst = str(uuid.uuid4())
    url = _db_url(head_db["db"])

    _alembic("downgrade", TARGET, url)
    conn = _connect(head_db["db"])
    try:
        with conn.cursor() as cur:
            cur.execute("DROP INDEX ix_concept_edges_unique")
            cur.execute(
                "INSERT INTO concept_edges (src_type, src_id, dst_type, dst_id, relation, "
                "status, tenant_id, source, created_at) "
                "VALUES ('concept', %s, 'concept', %s, 'TREATS', 'approved', NULL, 'manual', "
                "now() - interval '2 days')",
                (src, dst),
            )
            cur.execute(
                "INSERT INTO concept_edges (src_type, src_id, dst_type, dst_id, relation, "
                "status, tenant_id, source, created_at) "
                "VALUES ('concept', %s, 'concept', %s, 'TREATS', 'approved', NULL, 'manual', "
                "now())",
                (src, dst),
            )
        conn.commit()
    finally:
        conn.close()

    _alembic("upgrade", "head", url)

    conn = _connect(head_db["db"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT created_at FROM concept_edges WHERE src_id = %s AND status = 'approved'",
                (src,),
            )
            kept = cur.fetchall()
    finally:
        conn.close()
    assert len(kept) == 1, "approved duplicates must be deduped to one row"
    a_day_ago = datetime.now(UTC) - timedelta(days=1)
    assert kept[0][0] < a_day_ago, "the earliest (older) row must be kept"


def test_downgrade_dedupes_non_approved_duplicates(head_db):
    """Head allows repeated proposed/rejected rows; the restored blanket index
    does not — the downgrade dedupes them (earliest kept) instead of failing."""
    src = str(uuid.uuid4())
    dst = str(uuid.uuid4())
    url = _db_url(head_db["db"])

    conn = _connect(head_db["db"])
    try:
        with conn.cursor() as cur:
            for _ in range(2):
                cur.execute(
                    "INSERT INTO concept_edges (src_type, src_id, dst_type, dst_id, relation, "
                    "status, tenant_id, source, created_at) "
                    "VALUES ('concept', %s, 'concept', %s, 'TREATS', 'proposed', NULL, 'manual', "
                    "now())",
                    (src, dst),
                )
        conn.commit()
    finally:
        conn.close()

    _alembic("downgrade", TARGET, url)
    _alembic("upgrade", "head", url)

    conn = _connect(head_db["db"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM concept_edges WHERE src_id = %s", (src,))
            remaining = cur.fetchone()[0]
    finally:
        conn.close()
    assert remaining == 1, "non-approved duplicates must be deduped on the downgrade"


def test_upgrade_fails_loudly_on_pre_existing_scope_violation(head_db):
    """A pre-0010 row violating the scope laws aborts the upgrade at the
    ``VALIDATE CONSTRAINT`` step (the fail-loud claim in the revision
    header): the whole revision rolls back and 0009 stays in place."""
    url = _db_url(head_db["db"])
    _alembic("downgrade", TARGET, url)

    conn = _connect(head_db["db"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO medication_catalog (name, scope, tenant_id) "
                "VALUES (%s, %s, %s) RETURNING id",
                ("Scope violator", "system", str(head_db["tenant_id"])),
            )
            violator_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    result = _alembic_run("upgrade", "0010_catalog_identity", url)
    assert result.returncode != 0, "a scope-violating row must abort the upgrade"
    assert "ck_medication_catalog_scope_system_no_tenant" in result.stderr, result.stderr

    conn = _connect(head_db["db"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM medication_catalog WHERE id = %s", (str(violator_id),)
            )
            assert cur.fetchone()[0] == 1, "the rolled-back upgrade must leave the row intact"
            cur.execute("DELETE FROM medication_catalog WHERE id = %s", (str(violator_id),))
        conn.commit()
    finally:
        conn.close()

    _alembic("upgrade", "head", url)
