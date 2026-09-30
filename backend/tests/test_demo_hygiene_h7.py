"""Plan 16 H7 — demo hygiene: §13 verifier sweep + seeder refusals.

identity-auth §13: "every verifier rejects ``demo`` tokens while
``demo_mode=false`` (test §18.11)". H1 put the demo check in
``authenticate_session_token`` (so ``get_current_user`` / WS / the presign
Bearer path inherit it); H7 closes the remaining session-class surfaces:

* ``/auth/refresh`` — a demo **refresh family** must not keep rotating
  after the instance stops being a demo (the stamp travels with the
  family; the gate re-checks the live DB fact on every rotation);
* tenant-switch minting — the demo principal never mints cross-tenant
  scoped tokens (``switch_into`` / ``exit-switch`` refuse it);
* ``/auth/demo-login`` — 404 + a denied audit row on non-demo instances;
* ``scripts/seed_demo.py`` — the §13 refusal matrix (target name,
  instance flag, ``--init-demo`` on non-empty), fail-closed on unreadable
  facts (§4.1);
* instance state — env flips are init-only noise; unset facts read
  ``demo_mode=false``.

The seeder tests run against an isolated scratch ``*_demo`` database on
the test PostgreSQL server — never the shared ``*_test`` database.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient as FastTestClient
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import instance_state
from app.core.config import settings
from app.main import app
from app.models.audit_model import AuditEvent
from app.models.base import Base
from app.models.enums import Role
from app.models.instance_setting_model import InstanceSettingModel
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from tests._auth_helpers import create_tenant, create_user, sign_in

# Every test here implements identity-auth §18.11 (demo rules) + the
# §18.6 init-only/fail-closed instance-state halves — drift gate.
pytestmark = pytest.mark.contract

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# helpers — flip the live instance fact per test
# ---------------------------------------------------------------------------


def _state_with(demo_mode: bool):
    """Patch target for ``instance_state.get_state`` (DB fact stand-in)."""
    return AsyncMock(
        return_value=SimpleNamespace(
            auth_mode=instance_state.AUTH_MODE_AUTHENTICATED,
            demo_mode=demo_mode,
        )
    )


# ---------------------------------------------------------------------------
# HTTP: session-class verifiers reject demo tokens off-demo (§18.11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_token_401_on_http_off_demo(async_client):
    user = await create_user()
    issued = await sign_in(user, auth_mode="demo")
    with patch.object(instance_state, "get_state", new=_state_with(False)):
        r = await async_client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {issued.access_token}"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_demo_token_still_works_on_demo_instance(async_client):
    user = await create_user()
    issued = await sign_in(user, auth_mode="demo")
    with patch.object(instance_state, "get_state", new=_state_with(True)):
        r = await async_client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {issued.access_token}"},
        )
    assert r.status_code == 200
    assert r.json()["auth_mode"] == "demo"


@pytest.mark.asyncio
async def test_demo_token_rejected_on_presign_bearer_path():
    """The documents preview Bearer path calls ``authenticate_session_token``
    directly — the same §13 gate applies there (no separate rule)."""
    from app.core.security import authenticate_session_token

    user = await create_user()
    issued = await sign_in(user, auth_mode="demo")
    with patch.object(instance_state, "get_state", new=_state_with(False)):
        with pytest.raises(HTTPException) as exc:
            await authenticate_session_token(issued.access_token)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# WebSocket handshake
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_handshake_rejects_demo_token_off_demo():
    from app.core.database import engine as db_engine

    user = await create_user()
    issued = await sign_in(user, auth_mode="demo")
    # Fresh pool: the TestClient portal runs its own event loop, and the
    # shared asyncpg pool is loop-bound.
    await db_engine.dispose()
    client = FastTestClient(app)
    with patch.object(instance_state, "get_state", new=_state_with(False)):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/v1/ws/notifications",
                headers={"cookie": f"nx_access={issued.access_token}"},
            ):
                pass  # pragma: no cover — the handshake must not complete


@pytest.mark.asyncio
async def test_ws_handshake_accepts_demo_token_on_demo_instance():
    from app.core.database import engine as db_engine

    user = await create_user()
    issued = await sign_in(user, auth_mode="demo")
    await db_engine.dispose()
    client = FastTestClient(app)
    with patch.object(instance_state, "get_state", new=_state_with(True)):
        with client.websocket_connect(
            "/api/v1/ws/notifications",
            headers={"cookie": f"nx_access={issued.access_token}"},
        ) as ws:
            assert ws is not None


# ---------------------------------------------------------------------------
# Refresh: the demo family dies with demo_mode (S-7 residual)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_refresh_refused_off_demo(async_client):
    user = await create_user()
    issued = await sign_in(user, auth_mode="demo")
    with patch.object(instance_state, "get_state", new=_state_with(False)):
        r = await async_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": issued.refresh_token}
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_demo_refresh_refusal_writes_denied_audit_row(async_client):
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select

    user = await create_user()
    issued = await sign_in(user, auth_mode="demo")
    with patch.object(instance_state, "get_state", new=_state_with(False)):
        r = await async_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": issued.refresh_token}
        )
    assert r.status_code == 401
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "auth.refresh",
                    AuditEvent.user_id == user.id,
                    AuditEvent.outcome == "denied",
                )
                .order_by(AuditEvent.created_at.desc())
                .limit(1)
            )
        ).scalars().all()
    assert rows, "expected a denied auth.refresh audit row for the demo family"
    assert rows[0].new_value["reason"] == "demo_refresh_on_non_demo_instance"


@pytest.mark.asyncio
async def test_demo_refresh_rotates_on_demo_instance(async_client):
    user = await create_user()
    issued = await sign_in(user, auth_mode="demo")
    with patch.object(instance_state, "get_state", new=_state_with(True)):
        r = await async_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": issued.refresh_token}
        )
    assert r.status_code == 200
    assert r.json()["access_token"]
    # The rotated pair keeps the §13 stamp — verifiers keep rejecting it
    # off-demo.
    from app.core.security import decode_token

    payload = decode_token(r.json()["access_token"])
    assert payload["auth_mode"] == "demo"


@pytest.mark.asyncio
async def test_password_refresh_unaffected_by_demo_gate(async_client):
    user = await create_user(password="Passw0rd!")
    issued = await sign_in(user, auth_mode="password")
    with patch.object(instance_state, "get_state", new=_state_with(False)):
        r = await async_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": issued.refresh_token}
        )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Tenant switch: the demo principal never mints scoped tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_principal_cannot_switch_tenants(async_client):
    # SYSTEM_ADMIN so the role gate passes and only the §13 rule refuses.
    user = await create_user(role=Role.SYSTEM_ADMIN)
    target = await create_tenant(name="H7 Switch Target")
    issued = await sign_in(user, auth_mode="demo")
    with patch.object(instance_state, "get_state", new=_state_with(True)):
        r = await async_client.post(
            f"/api/v1/admin/tenants/{target}/switch",
            headers={"Authorization": f"Bearer {issued.access_token}"},
        )
        r2 = await async_client.post(
            "/api/v1/admin/tenants/exit-switch",
            headers={"Authorization": f"Bearer {issued.access_token}"},
        )
    assert r.status_code == 403
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_non_demo_admin_can_still_switch_tenants(async_client):
    user = await create_user(role=Role.SYSTEM_ADMIN)
    target = await create_tenant(name="H7 Switch Target OK")
    issued = await sign_in(user, auth_mode="password")
    with patch.object(instance_state, "get_state", new=_state_with(False)):
        r = await async_client.post(
            f"/api/v1/admin/tenants/{target}/switch",
            headers={"Authorization": f"Bearer {issued.access_token}"},
        )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# demo-login: fail-closed 404 + audit; happy path on a demo instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_login_404_and_audited_off_demo(async_client):
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select

    with patch.object(instance_state, "get_state", new=_state_with(False)):
        r = await async_client.post("/api/v1/auth/demo-login")
    assert r.status_code == 404
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "auth.demo_login",
                    AuditEvent.outcome == "denied",
                )
                .order_by(AuditEvent.created_at.desc())
                .limit(1)
            )
        ).scalars().all()
    assert rows
    assert rows[0].new_value["reason"] == "demo_mode_off"


@pytest.mark.asyncio
async def test_demo_login_mints_demo_stamped_tokens_on_demo_instance(async_client):
    await create_user(
        email=settings.HA_DEMO_EMAIL,
        password="Demo1234!",
        unique=False,
        full_name="Demo",
    )
    with patch.object(instance_state, "get_state", new=_state_with(True)):
        r = await async_client.post("/api/v1/auth/demo-login")
    assert r.status_code == 200
    from app.core.security import decode_token

    payload = decode_token(r.json()["access_token"])
    assert payload["auth_mode"] == "demo"


# ---------------------------------------------------------------------------
# Instance state: fail-closed facts + init-only env (§4.1/§13)
# ---------------------------------------------------------------------------


class _ScratchDB:
    """An isolated ``*_demo``-named scratch database on the test server."""

    def __init__(self, name: str):
        self.name = name
        import psycopg2

        self._admin = psycopg2.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            dbname="postgres",
        )
        self._admin.autocommit = True

    def create(self):
        with self._admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.name}"')
            cur.execute(f'CREATE DATABASE "{self.name}"')

    def sql(self, *statements: str):
        import psycopg2

        conn = psycopg2.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            dbname=self.name,
        )
        try:
            with conn.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def drop(self):
        with self._admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.name}"')
        self._admin.close()

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{settings.POSTGRES_USER}:"
            f"{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:"
            f"{settings.POSTGRES_PORT}/{self.name}"
        )


@pytest.fixture
async def istate_scratch():
    """Scratch DB with the tables ``instance_state`` touches (facts + the
    user-count probe) — isolated from the shared test DB."""
    db = _ScratchDB("health_h7_istate_demo")
    db.create()
    engine = create_async_engine(db.url)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    InstanceSettingModel.__table__,
                    TenantModel.__table__,
                    UserModel.__table__,
                ],
            )
        )
    await engine.dispose()
    yield db
    db.drop()


def _bind_instance_state_to(db):
    """Point ``instance_state``'s session factory at the scratch DB.

    Returns ``(patcher, engine)`` — dispose the engine before teardown so
    the scratch database can be dropped.
    """
    engine = create_async_engine(db.url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return patch.object(instance_state, "AsyncSessionLocal", factory), engine


@pytest.mark.asyncio
async def test_get_state_fails_closed_when_demo_fact_unset(istate_scratch):
    patched, engine = _bind_instance_state_to(istate_scratch)
    with patched:
        state = await instance_state.get_state()
    await engine.dispose()
    assert state.demo_mode is False
    assert state.auth_mode is None  # unknown ⇒ verifier treats as authenticated


def _capture_instance_state_logs() -> tuple[list, object]:
    """Capture ``instance_state`` warnings directly.

    The session ``run_migrations`` fixture triggers alembic's
    ``fileConfig(..., disable_existing_loggers=True)``, which silences
    loggers created at collection time — ``caplog`` therefore never sees
    ``instance_state`` records. A dedicated handler (with the ``disabled``
    flag cleared for the capture window) is immune to that.
    """
    import logging

    records: list = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    lg = logging.getLogger("app.core.instance_state")
    lg.addHandler(handler)
    was_disabled = lg.disabled
    lg.disabled = False

    class _Restore:
        def __enter__(self):
            return records

        def __exit__(self, *exc):
            lg.removeHandler(handler)
            lg.disabled = was_disabled

    return records, _Restore()


@pytest.mark.asyncio
async def test_env_flip_cannot_enable_demo_mode_post_init(istate_scratch):
    istate_scratch.sql(
        "INSERT INTO instance_settings (key, value) VALUES ('auth_mode', 'authenticated')",
        "INSERT INTO instance_settings (key, value) VALUES ('demo_mode', 'false')",
    )
    records, capture = _capture_instance_state_logs()
    patched, engine = _bind_instance_state_to(istate_scratch)
    with capture, patched, patch.object(settings, "HA_DEMO_MODE", True):
        await instance_state.initialize()
        state = await instance_state.get_state()
    await engine.dispose()
    assert state.demo_mode is False  # the env flip is ignored — DB wins
    assert any("HA_DEMO_MODE" in rec.getMessage() for rec in records)


@pytest.mark.asyncio
async def test_env_flip_cannot_disable_demo_mode_post_init(istate_scratch):
    istate_scratch.sql(
        "INSERT INTO instance_settings (key, value) VALUES ('auth_mode', 'authenticated')",
        "INSERT INTO instance_settings (key, value) VALUES ('demo_mode', 'true')",
    )
    records, capture = _capture_instance_state_logs()
    patched, engine = _bind_instance_state_to(istate_scratch)
    with capture, patched, patch.object(settings, "HA_DEMO_MODE", False):
        await instance_state.initialize()
        state = await instance_state.get_state()
    await engine.dispose()
    assert state.demo_mode is True  # still a demo — the env is init-only
    assert any("HA_DEMO_MODE" in rec.getMessage() for rec in records)


# ---------------------------------------------------------------------------
# Seeder §13 refusal matrix (identity-auth §13 / §18.11)
# ---------------------------------------------------------------------------


@pytest.fixture
def seeder_scratch():
    db = _ScratchDB("health_h7_seed_demo")
    db.create()
    yield db
    db.drop()


def _run_seeder(url: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/seed_demo.py", "--database-url", url, *extra],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_seeder_target_guard_unit_rules():
    from scripts.seed_demo import Refusal, ensure_demo_target

    with pytest.raises(Refusal):
        ensure_demo_target("sqlite:///tmp/not-a-demo.sqlite3")
    with pytest.raises(Refusal):
        ensure_demo_target("postgresql+asyncpg://u:p@h:5432/health_assistant")
    with pytest.raises(Refusal):
        ensure_demo_target("postgresql+psycopg://u:p@h:5432/neuronection_health")
    # The family demo names pass.
    ensure_demo_target("postgresql+asyncpg://u:p@h:5432/neuronection_health_demo")


def test_seeder_refuses_non_demo_database_name():
    """The shared test DB URL is refused before anything is touched."""
    url = (
        f"postgresql+asyncpg://{settings.POSTGRES_USER}:"
        f"{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:"
        f"{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    assert settings.POSTGRES_DB.endswith("_test")  # guard the guard
    result = _run_seeder(url)
    assert result.returncode == 2
    assert "not a demo database" in (result.stderr + result.stdout)


def test_seeder_refuses_unmigrated_demo_database(seeder_scratch):
    """Unreadable instance facts ⇒ unprovable ⇒ refuse (fail-closed §4.1)."""
    result = _run_seeder(seeder_scratch.url)
    assert result.returncode == 2
    assert "cannot read instance_settings" in (result.stderr + result.stdout)


def test_seeder_refuses_non_demo_instance(seeder_scratch):
    seeder_scratch.sql(
        """
        CREATE TABLE instance_settings (
            key VARCHAR(100) PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """,
        "INSERT INTO instance_settings (key, value) VALUES ('demo_mode', 'false')",
    )
    result = _run_seeder(seeder_scratch.url)
    assert result.returncode == 2
    assert "demo_mode" in (result.stderr + result.stdout)


def test_seeder_init_demo_refused_on_non_empty(seeder_scratch):
    seeder_scratch.sql(
        """
        CREATE TABLE instance_settings (
            key VARCHAR(100) PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """,
        "INSERT INTO instance_settings (key, value) VALUES ('demo_mode', 'false')",
    )
    result = _run_seeder(seeder_scratch.url, "--init-demo")
    assert result.returncode == 2
    assert "--init-demo requires an EMPTY demo database" in (
        result.stderr + result.stdout
    )


@pytest.mark.asyncio
async def test_seeder_init_demo_flags_empty_demo_database(seeder_scratch):
    """``--init-demo`` on a truly empty (but migrated) demo DB writes the
    fact — the only non-refused write path besides instance init."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.base import Base
    from app.models.fhir.patient import Patient
    from app.models.instance_setting_model import InstanceSettingModel
    from app.models.tenant_model import TenantModel
    from app.models.user_model import UserModel
    from scripts.seed_demo import ensure_demo_instance

    engine = create_async_engine(seeder_scratch.url)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    InstanceSettingModel.__table__,
                    TenantModel.__table__,
                    UserModel.__table__,
                    Patient.__table__,
                ],
            )
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        reason = await ensure_demo_instance(session, init_demo=True)
        assert reason == "demo_mode=true (--init-demo)"
        row = await session.get(InstanceSettingModel, "demo_mode")
        assert str(row.value).strip().lower() == "true"
    await engine.dispose()
