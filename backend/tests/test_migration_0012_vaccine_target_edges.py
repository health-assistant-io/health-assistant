"""Round-trip + backfill for 0012_vaccine_target_edges (ADR 0055 D8).

Runs the real chain against a scratch database:

1. seed the legacy shape at ``0011``: vaccine rows carry ``target_diseases``
   slug lists; the disease concepts exist as system concepts; one slug is
   deliberately unresolvable; one PREVENTS edge already exists (the exact
   tuple the migration would insert);
2. upgrade ``0011 → head``: every resolvable slug became an approved seed
   ``PREVENTS`` edge (no duplicate for the pre-existing tuple), the
   unresolvable slug was skipped without aborting, the JSONB column is gone;
3. downgrade to ``0011``: the column is back, backfilled from the approved
   PREVENTS edges (sorted slug lists), including the pre-existing edge;
4. upgrade again: the same edge set (idempotent re-materialization).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import psycopg2
import pytest

from app.core.catalog_identity import SEED_KEY_NAMESPACE
from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[1]
TARGET = "0011_instance_catalog_fks"


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


@pytest.fixture(scope="module")
def seeded(scratch_db, scratch_url):
    """The scratch database at 0011 with the legacy vaccine shape."""
    _alembic("upgrade", TARGET, scratch_url)
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            disease_slugs = ("measles", "mumps", "rubella", "influenza")
            for slug in disease_slugs:
                cur.execute(
                    "INSERT INTO concepts (id, slug, name, primary_kind, tenant_id, "
                    "scope, status, deleted_at, aliases, display_order) "
                    "VALUES (%s, %s, %s, 'disease', NULL, 'system', 'active', NULL, "
                    "'[]'::jsonb, 0)",
                    (uuid.uuid4(), slug, slug.replace("-", " ").title()),
                )
            vaccines = {
                # mmr carries the curated seed_key → its materialized edges
                # get the seeder-canonical uuid5 ids; the others (outside the
                # curated identity) keep the row-based ids.
                "mmr": (["measles", "mumps", "rubella"], "vaccine:mmr"),
                "flu": (["influenza", "polio"], None),  # polio resolves to nothing
                "empty": ([], None),
            }
            vaccine_ids: dict[str, str] = {}
            for slug, (targets, seed_key) in vaccines.items():
                cur.execute(
                    "INSERT INTO vaccine_catalog (id, slug, name, target_diseases, "
                    "coding_system, scope, seed_key) VALUES (%s, %s, %s, %s::jsonb, "
                    "'cvx', 'system', %s) RETURNING id",
                    (uuid.uuid4(), slug, slug.upper(), json.dumps(targets), seed_key),
                )
                vaccine_ids[slug] = cur.fetchone()[0]
            tenant_id = uuid.uuid4()
            cur.execute(
                "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
                (tenant_id, "vt", "vt-0012"),
            )
            cur.execute(
                "INSERT INTO vaccine_catalog (id, slug, name, target_diseases, "
                "coding_system, scope, tenant_id) VALUES (%s, %s, %s, %s::jsonb, "
                "'cvx', 'tenant', %s) RETURNING id",
                (uuid.uuid4(), "ovl-flu", "OVL FLU", json.dumps(["influenza"]), tenant_id),
            )
            # A pre-existing identical PREVENTS edge (e.g. from an earlier
            # concept_edges seeding): the migration must not duplicate it.
            cur.execute(
                "INSERT INTO concept_edges (id, src_type, src_id, dst_type, dst_id, "
                "relation, tenant_id, source, status) "
                "SELECT %s, 'immunization', v.id, 'concept', c.id, 'PREVENTS', NULL, "
                "'seed', 'approved' FROM vaccine_catalog v "
                "JOIN concepts c ON c.slug = 'measles' WHERE v.slug = 'mmr'",
                (uuid.uuid4(),),
            )
        conn.commit()
    finally:
        conn.close()
    return {"db": scratch_db, "vaccine_ids": vaccine_ids}


def _connect(name: str):
    return psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=name,
    )


def _columns(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        return {row[0] for row in cur.fetchall()}


def _prevents(conn) -> dict[str, list[str]]:
    """vaccine slug → sorted list of prevented disease slugs (approved edges)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT v.slug, c.slug FROM concept_edges e "
            "JOIN vaccine_catalog v ON v.id = e.src_id "
            "JOIN concepts c ON c.id = e.dst_id "
            "WHERE e.src_type = 'immunization' AND e.dst_type = 'concept' "
            "AND e.relation = 'PREVENTS' AND e.status = 'approved' "
            "AND e.source = 'seed' ORDER BY v.slug, c.slug"
        )
        out: dict[str, list[str]] = {}
        for vaccine_slug, disease_slug in cur.fetchall():
            out.setdefault(vaccine_slug, []).append(disease_slug)
        return out


def _target_diseases(conn) -> dict[str, list[str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT slug, target_diseases FROM vaccine_catalog "
            "WHERE target_diseases IS NOT NULL ORDER BY slug"
        )
        return {slug: list(targets) for slug, targets in cur.fetchall()}


def test_0012_materializes_edges_drops_column_and_round_trips(seeded):
    url = _db_url(seeded["db"])

    _alembic("upgrade", "head", url)

    conn = _connect(seeded["db"])
    try:
        assert "target_diseases" not in _columns(conn, "vaccine_catalog")
        prevented = _prevents(conn)
        assert prevented["mmr"] == ["measles", "mumps", "rubella"]
        assert prevented["flu"] == ["influenza"], "the unresolvable slug must be skipped"
        assert "empty" not in prevented, "no edges for an empty legacy list"
        assert prevented.get("ovl-flu") == ["influenza"]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT e.tenant_id FROM concept_edges e "
                "JOIN vaccine_catalog v ON v.id = e.src_id "
                "JOIN concepts c ON c.id = e.dst_id "
                "WHERE v.slug = 'ovl-flu' AND e.relation = 'PREVENTS'"
            )
            edge_tenant = cur.fetchone()[0]
        assert edge_tenant is not None, "a tenant overlay's edge stays tenant-scoped"
        # The curated row's edges carry the seeder-canonical id (a later
        # reseed converges to exactly this edge); rows outside the curated
        # identity keep the row-based id.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT v.slug, c.slug, e.id FROM concept_edges e "
                "JOIN vaccine_catalog v ON v.id = e.src_id "
                "JOIN concepts c ON c.id = e.dst_id "
                "WHERE e.relation = 'PREVENTS' AND e.status = 'approved'"
            )
            edge_ids = {(v, c): str(eid) for v, c, eid in cur.fetchall()}
            assert edge_ids[("mmr", "mumps")] == str(
                uuid.uuid5(SEED_KEY_NAMESPACE, "vaccine-prevents:mmr/mumps")
            ), "a seeded vaccine row must mint the seeder-canonical edge id"
            cur.execute("SELECT id FROM concepts WHERE slug = 'influenza'")
            influenza_id = cur.fetchone()[0]
            assert edge_ids[("flu", "influenza")] == str(
                uuid.uuid5(
                    SEED_KEY_NAMESPACE,
                    f"vaccine-prevents-row:{seeded['vaccine_ids']['flu']}/{influenza_id}",
                )
            ), "a row without seed_key keeps the row-based edge id"
            # No duplicates: exactly one edge per (mmr, measles).
            cur.execute(
                "SELECT count(*) FROM concept_edges e "
                "JOIN vaccine_catalog v ON v.id = e.src_id "
                "JOIN concepts c ON c.id = e.dst_id "
                "WHERE v.slug = 'mmr' AND c.slug = 'measles' "
                "AND e.relation = 'PREVENTS' AND e.status = 'approved'"
            )
            assert cur.fetchone()[0] == 1, "the pre-existing edge was duplicated"
    finally:
        conn.close()

    _alembic("downgrade", TARGET, url)

    conn = _connect(seeded["db"])
    try:
        assert "target_diseases" in _columns(conn, "vaccine_catalog")
        targets = _target_diseases(conn)
        assert targets["mmr"] == ["measles", "mumps", "rubella"]
        assert targets["flu"] == ["influenza"], "the unresolvable slug was dropped"
        assert targets["ovl-flu"] == ["influenza"]
        with conn.cursor() as cur:
            cur.execute("SELECT target_diseases FROM vaccine_catalog WHERE slug = 'empty'")
            assert cur.fetchone()[0] is None, "an empty legacy list stays NULL"
    finally:
        conn.close()

    _alembic("upgrade", "head", url)

    conn = _connect(seeded["db"])
    try:
        assert "target_diseases" not in _columns(conn, "vaccine_catalog")
        prevented = _prevents(conn)
        assert prevented["mmr"] == ["measles", "mumps", "rubella"]
        assert prevented["flu"] == ["influenza"]
    finally:
        conn.close()
