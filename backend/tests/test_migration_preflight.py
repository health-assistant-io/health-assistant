"""Upgrade-path preflight (v0.9.0 baseline squash).

The published v0.8.0 chain was replaced by ``0001_baseline``; a database
from that release records a revision that no longer exists. Alembic's own
error ("Can't locate revision identified by ...") gives an operator nothing
to act on, so ``alembic/env.py`` refuses with the supported routes before any
DDL runs. These tests pin the three states:

* an unknown recorded revision is refused, naming the runbook;
* a fresh database is not refused (the normal chain is untouched);
* a database already at head is not refused (re-running an update is safe).
"""

from __future__ import annotations

import ast
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
V080_HEAD = "b1y2o3k4s5e6"
CURRENT_HEAD = "0009_calendar_bindings"


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


def _alembic(action: str, revision: str, url: str) -> subprocess.CompletedProcess:
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


@pytest.fixture
def scratch_db():
    name = f"neuronection_health_preflight_{uuid.uuid4().hex[:8]}"
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


@pytest.fixture
def scratch_url(scratch_db):
    return _db_url(scratch_db)


def _record_revision(name: str, revision: str) -> None:
    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=name,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE alembic_version (version_num varchar(32) primary key)")
            cur.execute("INSERT INTO alembic_version (version_num) VALUES (%s)", (revision,))
        conn.commit()
    finally:
        conn.close()


def _current_revision(name: str) -> str:
    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=name,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def test_unknown_recorded_revision_is_refused_with_the_runbook(scratch_db, scratch_url):
    _record_revision(scratch_db, V080_HEAD)

    result = _alembic("upgrade", "head", scratch_url)

    assert result.returncode != 0, result.stdout
    output = result.stdout + result.stderr
    assert V080_HEAD in output, "the refusal must name the recorded revision"
    assert "v0.8.0" in output
    assert "0001_baseline" in output
    assert "not supported" in output
    assert "docs/INSTALL.md" in output, "the refusal must point at the runbook"
    assert "install.sh --reset --yes" in output, "the empty-install route is named"


def test_fresh_database_is_not_refused(scratch_db, scratch_url):
    result = _alembic("upgrade", "head", scratch_url)

    assert result.returncode == 0, result.stderr
    assert _current_revision(scratch_db) == CURRENT_HEAD


def test_database_at_head_is_not_refused(scratch_db, scratch_url):
    result = _alembic("upgrade", "head", scratch_url)

    assert result.returncode == 0, result.stderr


def test_recorded_revision_matches_the_chain_head():
    """The test's head constant follows the migration lane automatically."""
    revisions = {}
    for path in (BACKEND / "alembic" / "versions").glob("0*.py"):
        tree = ast.parse(path.read_text())
        revision = down_revision = None
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            value = node.value
            if "revision" in names and isinstance(value, ast.Constant):
                revision = value.value
            if "down_revision" in names and isinstance(value, ast.Constant):
                down_revision = value.value
        revisions[revision] = down_revision

    heads = set(revisions) - {down for down in revisions.values() if down}
    assert heads == {CURRENT_HEAD}
