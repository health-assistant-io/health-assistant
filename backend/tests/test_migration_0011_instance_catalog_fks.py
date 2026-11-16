"""Round-trip + backfill for 0011_instance_catalog_fks (ADR 0055 D6).

Runs the real chain against a scratch database:

1. upgrade ``0010 → head``: the nullable ``medication_catalog_id`` /
   ``allergy_catalog_id`` columns, FKs and indexes exist;
2. legacy-data backfill (rows seeded at ``0010``): a live ``catalog_id``
   snapshot is linked; a dangling and a malformed id stay NULL; a row without
   ``catalog_id`` stays NULL; every JSONB snapshot byte survives;
3. ``ON DELETE SET NULL``: deleting the catalog row nulls the link, the JSONB
   snapshot is untouched;
4. downgrade to ``0010``: columns/FKs/indexes are gone.
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

from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[1]
TARGET = "0010_catalog_identity"


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
    """The scratch database at 0010 with legacy-shape instance rows."""
    _alembic("upgrade", TARGET, scratch_url)
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (name, slug) VALUES (%s, %s) RETURNING id",
                ("seed-t", "seed-t"),
            )
            tenant_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO fhir_patients (tenant_id, name, gender) "
                "VALUES (%s, %s::jsonb, 'MALE') RETURNING id",
                (tenant_id, json.dumps([{"given": ["Backfill"], "family": "Case"}])),
            )
            patient_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO medication_catalog (name, scope) VALUES ('Live Med', 'system') "
                "RETURNING id"
            )
            med_catalog_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO allergy_catalog (name, category, scope) "
                "VALUES ('Live Allergen', 'FOOD', 'system') RETURNING id"
            )
            allergy_catalog_id = cur.fetchone()[0]
            dangling = str(uuid.uuid4())
            for text, catalog_id in (
                ("Live Link", med_catalog_id),
                ("Dangling", dangling),
                ("Malformed", "not-a-uuid"),
                ("No id", None),
            ):
                code = {"text": text}
                if catalog_id is not None:
                    code["catalog_id"] = str(catalog_id)
                cur.execute(
                    "INSERT INTO fhir_medications (tenant_id, patient_id, code, status, intent) "
                    "VALUES (%s, %s, %s::jsonb, 'ACTIVE', 'statement') RETURNING id",
                    (tenant_id, patient_id, json.dumps(code)),
                )
            for text, catalog_id in (
                ("Live Link", allergy_catalog_id),
                ("Dangling", dangling),
                ("Malformed", "not-a-uuid"),
            ):
                code = {"text": text}
                if catalog_id is not None:
                    code["catalog_id"] = str(catalog_id)
                cur.execute(
                    "INSERT INTO fhir_allergy_intolerances (tenant_id, patient_id, code, "
                    "clinical_status) VALUES (%s, %s, %s::jsonb, 'ACTIVE') RETURNING id",
                    (tenant_id, patient_id, json.dumps(code)),
                )
        conn.commit()
    finally:
        conn.close()
    return {
        "db": scratch_db,
        "tenant_id": tenant_id,
        "patient_id": patient_id,
        "med_catalog_id": med_catalog_id,
        "allergy_catalog_id": allergy_catalog_id,
    }


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


def _indexes(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s",
            (table,),
        )
        return {row[0] for row in cur.fetchall()}


def _fks(conn, table: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND contype = 'f'",
            (f"public.{table}",),
        )
        return dict(cur.fetchall())


def _snapshot_by_text(conn, table: str) -> dict[str, str | None]:
    """code->>'text' → medication_catalog_id/allergy_catalog_id per instance."""
    column = "medication_catalog_id" if table == "fhir_medications" else "allergy_catalog_id"
    with conn.cursor() as cur:
        cur.execute(f"SELECT code->>'text', {column}::text, code::text FROM {table}")
        return {row[0]: row for row in cur.fetchall()}


def test_0011_backfills_links_and_downgrade_removes_them(seeded):
    url = _db_url(seeded["db"])

    _alembic("upgrade", "head", url)

    conn = _connect(seeded["db"])
    try:
        assert "medication_catalog_id" in _columns(conn, "fhir_medications")
        assert "allergy_catalog_id" in _columns(conn, "fhir_allergy_intolerances")
        assert "ix_fhir_medications_medication_catalog_id" in _indexes(conn, "fhir_medications")
        assert "ix_fhir_allergy_intolerances_allergy_catalog_id" in _indexes(
            conn, "fhir_allergy_intolerances"
        )
        med_fk = _fks(conn, "fhir_medications")["fk_fhir_medications_medication_catalog_id"]
        allergy_fk = _fks(conn, "fhir_allergy_intolerances")[
            "fk_fhir_allergy_intolerances_allergy_catalog_id"
        ]
        assert "medication_catalog" in med_fk and "SET NULL" in med_fk
        assert "allergy_catalog" in allergy_fk and "SET NULL" in allergy_fk

        meds = _snapshot_by_text(conn, "fhir_medications")
        assert meds["Live Link"][1] == str(seeded["med_catalog_id"])
        assert meds["Dangling"][1] is None, "dangling id must stay NULL"
        assert meds["Malformed"][1] is None, "malformed id must not abort the backfill"
        assert meds["No id"][1] is None
        allergies = _snapshot_by_text(conn, "fhir_allergy_intolerances")
        assert allergies["Live Link"][1] == str(seeded["allergy_catalog_id"])
        assert allergies["Dangling"][1] is None
        assert allergies["Malformed"][1] is None

        for text, row in meds.items():
            snapshot = json.loads(row[2])
            assert snapshot["text"] == text, "JSONB snapshot must survive"
            if "catalog_id" in snapshot:
                assert isinstance(snapshot["catalog_id"], str), (
                    "the JSONB snapshot is never modified"
                )
    finally:
        conn.close()

    _alembic("downgrade", TARGET, url)

    conn = _connect(seeded["db"])
    try:
        assert "medication_catalog_id" not in _columns(conn, "fhir_medications")
        assert "allergy_catalog_id" not in _columns(conn, "fhir_allergy_intolerances")
        assert "ix_fhir_medications_medication_catalog_id" not in _indexes(conn, "fhir_medications")
    finally:
        conn.close()

    _alembic("upgrade", "head", url)


def test_fk_set_null_keeps_the_jsonb_snapshot(seeded):
    conn = _connect(seeded["db"])
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM medication_catalog WHERE id = %s", (seeded["med_catalog_id"],))
            cur.execute("SELECT code::text FROM fhir_medications WHERE code->>'text' = 'Live Link'")
            snapshot = cur.fetchone()[0]
            cur.execute(
                "SELECT medication_catalog_id::text FROM fhir_medications "
                "WHERE code->>'text' = 'Live Link'"
            )
            link = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    assert link is None, "deleting the catalog row must SET NULL the FK"
    assert json.loads(snapshot)["catalog_id"] == str(seeded["med_catalog_id"]), (
        "the JSONB snapshot must keep the historical reference"
    )
