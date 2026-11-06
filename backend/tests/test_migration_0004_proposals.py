"""Data migration for 0004_proposals_unified (legacy-shape test).

Runs the real migration chain against a scratch database:

1. upgrade to ``0003_ai_task_registry`` (the pre-plan-5 shape);
2. seed legacy rows — an ``integration_proposals`` row and a chat message whose
   ``tasks`` JSONB carries two known cards plus one unregistered type;
3. upgrade to head and assert the rows moved into the one ``proposals`` table
   with the right source/kind/status/payload mapping, and that
   ``chat_messages.tasks`` is gone;
4. downgrade to ``0003`` and assert the previous shape (and its data) is back.

A scratch database (not the shared test DB) keeps the session's migrated DB
untouched — the same rule family-dev states for migration round-trip tests.
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
import psycopg2.extras
import pytest

from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[1]
TARGET = "0003_ai_task_registry"
HEAD = "0004_proposals_unified"


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
            allowed = cur.fetchone()[0]
            if not allowed:
                pytest.skip(
                    "the database role cannot CREATE DATABASE — the migration "
                    "round-trip needs an isolated scratch database"
                )
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
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def _seed_legacy(conn) -> dict:
    ids = {
        key: str(uuid.uuid4())
        for key in (
            "tenant",
            "user",
            "patient",
            "integration",
            "session",
            "message",
            "extension_proposal",
            "chat_card_event",
            "chat_card_ask",
            "chat_card_unknown",
        )
    }
    tasks = [
        {
            "schema_version": 1,
            "proposal_id": ids["chat_card_event"],
            "task_type": "create_clinical_event",
            "title": "Pregnancy",
            "status": "confirmed",
            "proposed_payload": {"title": "Pregnancy"},
            "context": {"patient_id": ids["patient"]},
            "resolved": {
                "confirmed_by": ids["user"],
                "final_payload": {"title": "Pregnancy"},
                "result": {"event_id": "evt-1"},
                "at": "2026-10-20T10:00:00+00:00",
            },
        },
        {
            "schema_version": 1,
            "proposal_id": ids["chat_card_ask"],
            "task_type": "ask_user",
            "title": "Pick one",
            "status": "proposed",
            "proposed_payload": {"questions": []},
            "context": {},
            "resolved": None,
        },
        {
            "schema_version": 1,
            "proposal_id": ids["chat_card_unknown"],
            "task_type": "not_a_kind",
            "title": "Dropped",
            "status": "proposed",
            "proposed_payload": {},
            "context": {},
            "resolved": None,
        },
    ]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, 'Legacy T', %s)",
            (ids["tenant"], f"legacy-{ids['tenant'][:8]}"),
        )
        cur.execute(
            "INSERT INTO users (id, email, password_hash, role, tenant_id) "
            "VALUES (%s, %s, 'x', 'ADMIN', %s)",
            (ids["user"], f"legacy-{ids['user'][:8]}@test.local", ids["tenant"]),
        )
        cur.execute(
            "INSERT INTO fhir_patients (id, tenant_id, name, gender) "
            "VALUES (%s, %s, %s, 'UNKNOWN')",
            (ids["patient"], ids["tenant"], json.dumps({"family": "Legacy"})),
        )
        cur.execute(
            "INSERT INTO user_integrations "
            "(id, tenant_id, user_id, patient_id, provider, status, user_config, "
            " is_debug_enabled) "
            "VALUES (%s, %s, %s, %s, 'test_provider', 'ACTIVE', '{}'::jsonb, false)",
            (ids["integration"], ids["tenant"], ids["user"], ids["patient"]),
        )
        cur.execute(
            "INSERT INTO integration_proposals "
            "(id, integration_id, patient_id, proposal_type, title, status, "
            " proposed_payload, context, resolved_payload, resolved_by, resolved_at, "
            " resolution_note, dedup_key, tenant_id, created_by) "
            "VALUES (%s, %s, NULL, 'create_biomarker_definition', 'Define: ApoB', "
            " 'confirmed', %s, %s, %s, %s, '2026-10-20T09:00:00+00:00', 'ok', %s, %s, %s)",
            (
                ids["extension_proposal"],
                ids["integration"],
                json.dumps({"name": "ApoB", "slug": "apob"}),
                json.dumps({"upstream": "lab"}),
                json.dumps({"name": "ApoB", "slug": "apob", "_applied_entity_id": None}),
                ids["user"],
                "a" * 64,
                ids["tenant"],
                ids["user"],
            ),
        )
        cur.execute(
            "INSERT INTO chat_sessions (id, tenant_id, user_id, title) "
            "VALUES (%s, %s, %s, 'Legacy chat')",
            (ids["session"], ids["tenant"], ids["user"]),
        )
        cur.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, tasks) "
            "VALUES (%s, %s, 'assistant', '{\"text\":\"hi\"}'::jsonb, %s::jsonb)",
            (ids["message"], ids["session"], json.dumps(tasks)),
        )
    conn.commit()
    return ids


def test_0004_moves_both_stores_and_downgrade_restores_the_old_shape(scratch_db, scratch_url):
    _alembic("upgrade", TARGET, scratch_url)

    conn = _connect(scratch_db)
    try:
        ids = _seed_legacy(conn)
    finally:
        conn.close()

    _alembic("upgrade", HEAD, scratch_url)

    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'chat_messages' AND column_name = 'tasks'"
            )
            assert cur.fetchone() is None, "chat_messages.tasks must be dropped"

            cur.execute("SELECT count(*) AS n FROM proposal_kinds")
            assert cur.fetchone()["n"] == 12

            cur.execute(
                "SELECT p.*, k.code AS kind_code FROM proposals p "
                "JOIN proposal_kinds k ON k.id = p.kind_id "
                "WHERE p.source = 'extension'"
            )
            extension = cur.fetchall()
            assert len(extension) == 1
            row = extension[0]
            assert str(row["id"]) == ids["extension_proposal"]
            assert str(row["source_ref"]) == ids["integration"]
            assert row["kind_code"] == "create_biomarker_definition"
            assert row["status"] == "applied"
            assert row["payload"]["title"] == "Define: ApoB"
            assert row["payload"]["proposed_payload"]["slug"] == "apob"
            assert row["final_payload"]["name"] == "ApoB"
            assert row["applied_at"] is not None
            assert row["resolution_trace"]["resolved_by"] == ids["user"]
            assert row["resolution_trace"]["note"] == "ok"

            cur.execute(
                "SELECT p.*, k.code AS kind_code FROM proposals p "
                "JOIN proposal_kinds k ON k.id = p.kind_id "
                "WHERE p.source = 'chat' ORDER BY p.ordinal"
            )
            chat = cur.fetchall()
            assert len(chat) == 2, "the unregistered task_type is dropped, the two known kinds move"
            assert [str(r["id"]) for r in chat] == [
                ids["chat_card_event"],
                ids["chat_card_ask"],
            ]
            assert [r["ordinal"] for r in chat] == [0, 1]
            assert str(chat[0]["source_ref"]) == ids["message"]
            assert str(chat[0]["patient_id"]) == ids["patient"]
            assert chat[0]["kind_code"] == "create_event"
            assert chat[0]["status"] == "applied"
            assert chat[0]["final_payload"] == {"title": "Pregnancy"}
            assert chat[1]["kind_code"] == "ask_user"
            assert chat[1]["status"] == "proposed"
    finally:
        conn.close()

    # ---- downgrade: the previous shape AND its data come back ----
    _alembic("downgrade", TARGET, scratch_url)
    conn = _connect(scratch_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'chat_messages' AND column_name = 'tasks'"
            )
            assert cur.fetchone() is not None, "downgrade re-adds chat_messages.tasks"

            cur.execute("SELECT tasks FROM chat_messages WHERE id = %s", (ids["message"],))
            tasks = cur.fetchone()["tasks"]
            assert [t["task_type"] for t in tasks] == ["create_clinical_event", "ask_user"]
            assert tasks[0]["status"] == "confirmed"
            assert tasks[0]["resolved"]["final_payload"] == {"title": "Pregnancy"}
            assert tasks[1]["status"] == "proposed"

            cur.execute(
                "SELECT * FROM integration_proposals WHERE id = %s",
                (ids["extension_proposal"],),
            )
            old = cur.fetchone()
            assert old["status"] == "confirmed"
            assert old["proposal_type"] == "create_biomarker_definition"
            assert old["proposed_payload"]["slug"] == "apob"
            assert old["resolved_payload"]["name"] == "ApoB"
            assert old["dedup_key"] == "a" * 64
    finally:
        conn.close()
