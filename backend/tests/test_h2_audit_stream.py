"""Plan 16 H2 — the ``audit_events`` stream (identity-auth §17).

§17: ``audit_events`` (actor, action, resource, tenant, outcome,
timestamp) must record **record reads, record writes, and every admin
action**. H2 renames ``audit_logs`` → ``audit_events``, adds the
normative ``outcome`` column, widens the read API (SYSTEM_ADMIN
cross-tenant viewer), and wires the missing surfaces.

Pinned here:

1. Migration ``a1u2d3i4t5e6`` round-trips (rename + ``outcome``).
2. Denied reads (403 ownership, 404 cross-tenant) land with
   ``outcome="denied"``; granted reads with ``outcome="ok"``.
3. Auth events: login ok / login denied / refresh reuse.
4. Admin actions: user create / role-change / delete.
5. Clinical writes: patient create/delete (observations were already
   covered by the B12 tests).
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Optional

import pytest
from sqlalchemy import create_engine, select, text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.audit_model import AuditEvent
from app.models.enums import Role
from app.models.fhir.patient import Observation, Patient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _audit_rows(
    *,
    action: Optional[str] = None,
    resource_id=None,
    user_id=None,
    tenant_id=None,
    outcome: Optional[str] = None,
) -> list[AuditEvent]:
    """Read the audit stream for the given filters (real test DB)."""
    from app.services.audit_service import _coerce_uuid

    stmt = select(AuditEvent)
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    if resource_id is not None:
        stmt = stmt.where(AuditEvent.resource_id == _coerce_uuid(resource_id))
    if user_id is not None:
        stmt = stmt.where(AuditEvent.user_id == _coerce_uuid(user_id))
    if tenant_id is not None:
        stmt = stmt.where(AuditEvent.tenant_id == _coerce_uuid(tenant_id))
    if outcome is not None:
        stmt = stmt.where(AuditEvent.outcome == outcome)
    stmt = stmt.order_by(AuditEvent.created_at.desc())
    async with AsyncSessionLocal() as db:
        return list((await db.execute(stmt)).scalars().all())


async def _make_patient(tenant_id, user_id=None) -> Patient:
    """Insert a patient row directly (per-tenant isolation by caller)."""
    pid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=pid,
                tenant_id=tenant_id,
                user_id=user_id,
                name={"family": "H2", "given": ["Test"]},
                gender="UNKNOWN",
            )
        )
        await db.commit()
    return Patient(id=pid)


async def _make_observation(tenant_id, patient_id) -> uuid.UUID:
    oid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Observation(
                id=oid,
                tenant_id=tenant_id,
                patient_id=patient_id,
                code={"coding": [{"system": "http://loinc.org", "code": "8867-4"}], "text": "Heart Rate"},
                subject={"reference": f"Patient/{patient_id}"},
                value_quantity={"value": 72.0, "unit": "bpm"},
                effective_datetime=_dt.datetime.now(_dt.timezone.utc),
                status="final",
                biomarker_id=None,
            )
        )
        await db.commit()
    return oid


def _table_names() -> set[str]:
    """Sync introspection of the public schema (psycopg2)."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            ).all()
            return {r[0] for r in rows}
    finally:
        engine.dispose()


def _columns(table: str) -> dict[str, Optional[str]]:
    """{column: column_default} for a table (sync introspection)."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            ).all()
            return {r[0]: r[1] for r in rows}
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. Table shape + migration round-trip
# ---------------------------------------------------------------------------


def test_h2_model_shape():
    """The model maps the §17-named table with the outcome column."""
    assert AuditEvent.__tablename__ == "audit_events"
    assert "outcome" in AuditEvent.__table__.columns
    assert AuditEvent.__table__.columns["outcome"].nullable is False


def test_h2_migration_applied_shape():
    """Head schema: audit_events (not audit_logs) with outcome defaulting
    to 'ok'."""
    tables = _table_names()
    assert "audit_events" in tables
    assert "audit_logs" not in tables
    cols = _columns("audit_events")
    assert "outcome" in cols
    assert "'ok'" in (cols["outcome"] or "")


def test_h2_migration_round_trip():
    """a1u2d3i4t5e6 downgrades to h1c2o3n4t5r6 (audit_logs, no outcome)
    and upgrades back — the rename must survive a round-trip."""
    from alembic import command
    from alembic.config import Config
    import logging

    logging.getLogger("alembic").setLevel(logging.WARNING)
    cfg = Config("alembic.ini")

    try:
        command.downgrade(cfg, "h1c2o3n4t5r6")
        tables = _table_names()
        assert "audit_logs" in tables, "downgrade must restore audit_logs"
        assert "audit_events" not in tables
        assert "outcome" not in _columns("audit_logs")
    finally:
        command.upgrade(cfg, "head")

    tables = _table_names()
    assert "audit_events" in tables
    assert "audit_logs" not in tables
    assert "outcome" in _columns("audit_events")


# ---------------------------------------------------------------------------
# 2. Record reads — ok + denied (§17)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h2_read_audited_ok(async_client):
    """A granted sensitive read lands in the stream with outcome=ok."""
    from tests._auth_helpers import auth_headers, create_user

    admin = await create_user(role=Role.ADMIN)
    headers = await auth_headers(admin)

    resp = await async_client.post(
        "/api/v1/patients",
        headers=headers,
        json={"name": {"family": "Readable", "given": ["Ada"]}, "gender": "female"},
    )
    assert resp.status_code == 200, resp.text
    patient_id = resp.json()["id"]

    got = await async_client.get(f"/api/v1/patients/{patient_id}", headers=headers)
    assert got.status_code == 200, got.text

    rows = await _audit_rows(action="read_patient", resource_id=patient_id)
    assert rows, "granted patient read must be audited"
    assert rows[0].outcome == "ok"
    assert str(rows[0].user_id) == str(admin.id)


@pytest.mark.asyncio
@pytest.mark.contract  # §18.8 — denied read (403) on a foreign record
async def test_h2_denied_read_403_audited(async_client):
    """USER reading another user's observation → 403 + outcome=denied."""
    from tests._auth_helpers import auth_headers, create_tenant, create_user

    tenant = await create_tenant()
    # The patient belongs to a different user in the same tenant.
    owner = await create_user(role=Role.USER, tenant_id=tenant)
    reader = await create_user(role=Role.USER, tenant_id=tenant)

    patient = await _make_patient(tenant, user_id=owner.id)
    obs_id = await _make_observation(tenant, patient.id)

    headers = await auth_headers(reader)
    resp = await async_client.get(f"/api/v1/observations/{obs_id}", headers=headers)
    assert resp.status_code == 403, resp.text

    rows = await _audit_rows(action="read_observation", resource_id=obs_id)
    assert rows, "denied observation read must be audited"
    assert rows[0].outcome == "denied"
    assert rows[0].user_id == reader.id
    assert (rows[0].new_value or {}).get("http_status") == 403


@pytest.mark.asyncio
@pytest.mark.contract  # §18.8 — hidden-404 for cross-tenant records
async def test_h2_denied_read_404_cross_tenant_audited(async_client):
    """Cross-tenant patient read → hidden-404 + outcome=denied."""
    from tests._auth_helpers import auth_headers, create_tenant, create_user

    tenant_a = await create_tenant()
    tenant_b = await create_tenant()
    outsider = await create_user(role=Role.USER, tenant_id=tenant_b)

    patient = await _make_patient(tenant_a)

    headers = await auth_headers(outsider)
    resp = await async_client.get(f"/api/v1/patients/{patient.id}", headers=headers)
    assert resp.status_code == 404, resp.text

    rows = await _audit_rows(action="read_patient", resource_id=patient.id)
    assert rows, "cross-tenant (hidden-404) read must be audited"
    assert rows[0].outcome == "denied"
    assert rows[0].user_id == outsider.id
    assert (rows[0].new_value or {}).get("http_status") == 404


# ---------------------------------------------------------------------------
# 3. Auth events — login ok/denied, refresh reuse (§17)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h2_login_ok_and_denied_audited(async_client):
    """login ok → outcome=ok; wrong password → outcome=denied; unknown
    email → denied with the anonymous (NULL) actor."""
    from tests._auth_helpers import create_user

    password = "correct-horse-battery"
    user = await create_user(
        role=Role.USER, password=password, email="h2-login@test.local", unique=False
    )

    ok = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "h2-login@test.local", "password": password},
    )
    assert ok.status_code == 200, ok.text
    rows = await _audit_rows(action="auth.login", user_id=user.id, outcome="ok")
    assert rows, "successful login must be audited"
    assert str(rows[0].tenant_id) == str(user.tenant_id)
    assert rows[0].resource_type == "auth"

    denied = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "h2-login@test.local", "password": "wrong-password-xx"},
    )
    assert denied.status_code == 401
    rows = await _audit_rows(action="auth.login", user_id=user.id, outcome="denied")
    assert rows, "failed login must be audited"
    assert rows[0].new_value == {"reason": "invalid_credentials"}

    anon = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "ghost-h2@test.local", "password": "wrong-password-xx"},
    )
    assert anon.status_code == 401
    # Anonymous denial: NULL actor + a tenant-less row is the marker.
    rows = await _audit_rows(action="auth.login", outcome="denied")
    anon_rows = [r for r in rows if r.new_value == {"reason": "invalid_credentials"} and r.user_id is None]
    assert anon_rows, "unknown-email login denial must be audited (NULL actor)"


@pytest.mark.asyncio
@pytest.mark.contract  # §18.3 — refresh reuse ⇒ revoked + denied audit row
async def test_h2_refresh_reuse_audited(async_client):
    """Replaying a rotated refresh token revokes the family and lands in
    the stream as auth.refresh_reuse / outcome=denied."""
    from tests._auth_helpers import auth_headers, create_user

    user = await create_user(
        role=Role.USER, password="rotate-me-please", email="h2-rotate@test.local", unique=False
    )
    headers = await auth_headers(user)  # signs in → refresh token issued

    # Sign in directly to hold a refresh token of our own.
    signed_in = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "h2-rotate@test.local", "password": "rotate-me-please"},
    )
    assert signed_in.status_code == 200, signed_in.text
    refresh_1 = signed_in.json()["refresh_token"]

    rotated = await async_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_1}
    )
    assert rotated.status_code == 200, rotated.text

    replay = await async_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_1}
    )
    assert replay.status_code == 423, replay.text

    rows = await _audit_rows(action="auth.refresh_reuse", user_id=user.id)
    assert rows, "refresh reuse detection must be audited"
    assert rows[0].outcome == "denied"


# ---------------------------------------------------------------------------
# 4. Admin actions — user create / role-change / delete (§17)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h2_admin_user_actions_audited(async_client, system_admin_headers):
    """POST /users, role-change PUT, and DELETE all leave audit rows."""
    email = f"h2-target-{uuid.uuid4().hex[:8]}@example.com"

    created = await async_client.post(
        "/api/v1/users",
        headers=system_admin_headers,
        json={"email": email, "password": "initial-password-1", "full_name": "H2 Target"},
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]
    rows = await _audit_rows(action="user.create", resource_id=user_id)
    assert rows and rows[0].outcome == "ok"
    assert rows[0].new_value["email"] == email

    promoted = await async_client.put(
        f"/api/v1/users/{user_id}",
        headers=system_admin_headers,
        params={"role": "MANAGER"},
    )
    assert promoted.status_code == 200, promoted.text
    rows = await _audit_rows(action="user.role_change", resource_id=user_id)
    assert rows, "role change must be audited"
    assert rows[0].old_value["role"] == "USER"
    assert rows[0].new_value["role"] == "MANAGER"

    deleted = await async_client.delete(
        f"/api/v1/users/{user_id}", headers=system_admin_headers
    )
    assert deleted.status_code == 200, deleted.text
    rows = await _audit_rows(action="user.delete", resource_id=user_id)
    assert rows and rows[0].old_value["email"] == email


# ---------------------------------------------------------------------------
# 5. Clinical writes + the widened read API (§17)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h2_patient_write_audited(async_client, system_admin_headers):
    """Patient create/delete (record writes) land in the stream."""
    resp = await async_client.post(
        "/api/v1/patients",
        headers=system_admin_headers,
        json={"name": {"family": "Writable", "given": ["Bob"]}, "gender": "male"},
    )
    assert resp.status_code == 200, resp.text
    patient_id = resp.json()["id"]

    rows = await _audit_rows(action="create_patient", resource_id=patient_id)
    assert rows and rows[0].outcome == "ok"

    deleted = await async_client.delete(
        f"/api/v1/patients/{patient_id}", headers=system_admin_headers
    )
    assert deleted.status_code == 200, deleted.text
    rows = await _audit_rows(action="delete_patient", resource_id=patient_id)
    assert rows and rows[0].outcome == "ok"


@pytest.mark.asyncio
async def test_h2_cross_tenant_audit_viewer(async_client, system_admin_headers):
    """GET /admin/audit returns rows across tenants (SYSTEM_ADMIN-only)."""
    resp = await async_client.get(
        "/api/v1/admin/audit", headers=system_admin_headers, params={"limit": 250}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    entry = body["items"][0]
    assert "outcome" in entry
    assert "tenant_id" in entry

    # Denial filter narrows to denied rows only.
    denied = await async_client.get(
        "/api/v1/admin/audit",
        headers=system_admin_headers,
        params={"outcome": "denied", "limit": 250},
    )
    assert denied.status_code == 200
    assert all(i["outcome"] == "denied" for i in denied.json()["items"])

    # Non-admins are kept out (403 from the RoleChecker).
    from tests._auth_helpers import auth_headers, create_user

    plain = await create_user(role=Role.USER)
    forbidden = await async_client.get(
        "/api/v1/admin/audit", headers=await auth_headers(plain)
    )
    assert forbidden.status_code == 403
