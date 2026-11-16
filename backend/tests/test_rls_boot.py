"""The boot sequence under row-level security, run as the **app role** (B1).

The lifespan's database steps run with no request context; under RLS a
closed context answers zero rows, so every step carries its explicit
``tenant_context.system_scope("boot:<step>")`` (``app/core/boot.py``). This
suite proves the whole sequence works when the database actually enforces
the policies: a scratch database (``..._demo`` — the §13 name the demo
seeder requires) is created, migrated to head, and every boot operation
runs against it **as ``neuronection_health_app``** — the least-privilege
runtime role the production containers use, with no owner bypass:

1. **fresh install** — instance facts initialize (consume the env once);
2. **bundled apply** — the digest-gated seed pack applies (its NULL-tenant
   system rows are writable only under the system scope, and the applier's
   locked apply must plant the context through its own session class);
3. **demo seed** — the demo tenant + user + clinical data seed (§13-guarded;
   the guards themselves run under RLS);
4. **setup-token count** — the zero-user check sees the seeded user (the
   pre-fix behavior read 0 under RLS and re-announced setup every boot);
5. **stuck-exam cleanup** — a stale processing exam is failed, a fresh one
   untouched (the pre-fix behavior matched nothing).

The contextvars are cleared throughout: any step that works here works
*because* of its own scope, never an ambient one.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import psycopg2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import boot, tenant_context
from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[1]
APP_ROLE = "neuronection_health_app"


def _app_role_password() -> str:
    """The app role's secret (ADR-0064 split: POSTGRES_APP_PASSWORD).
    Falls back to POSTGRES_PASSWORD so dev volumes created before the split
    (shared-password legacy state) keep working with a warning."""
    password = settings.POSTGRES_APP_PASSWORD or settings.POSTGRES_PASSWORD
    if not settings.POSTGRES_APP_PASSWORD:
        print(
            "rls_boot: POSTGRES_APP_PASSWORD unset — using the legacy shared "
            "password for the app role (pre-split dev volume; ADR-0064 wants "
            "the split everywhere)."
        )
    return password


def _admin_dsn() -> dict:
    return {
        "host": settings.POSTGRES_HOST,
        "port": settings.POSTGRES_PORT,
        "user": settings.POSTGRES_USER,
        "password": settings.POSTGRES_PASSWORD,
        "dbname": "postgres",
    }


def _db_url(name: str, *, role: str | None = None) -> str:
    user = quote(role or settings.POSTGRES_USER, safe="")
    password = quote(
        _app_role_password() if role == APP_ROLE else (settings.POSTGRES_PASSWORD or ""),
        safe="",
    )
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
def scratch_demo_db():
    """A fresh ``*_demo`` database, migrated to head (the §13 name guard)."""
    name = f"neuronection_health_boot_{uuid.uuid4().hex[:8]}_demo"
    admin = psycopg2.connect(**_admin_dsn())
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cur.fetchone()[0]:
                pytest.skip("the database role cannot CREATE DATABASE")
            cur.execute(f'CREATE DATABASE "{name}"')
        _alembic("upgrade", "head", _db_url(name))
        yield name
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


@pytest.fixture(scope="module")
def app_boot_factory(scratch_demo_db):
    """The runtime-role engine + hooked session factory for the scratch DB."""
    engine = create_async_engine(_db_url(scratch_demo_db, role=APP_ROLE), poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    tenant_context.install_session_hook(factory)
    yield factory, engine
    asyncio.run(engine.dispose())


@pytest.fixture
def app_role_boot(app_boot_factory, monkeypatch):
    """Point every boot consumer at the app-role scratch-DB sessions.

    The boot modules resolve their sessions through ``app.core.database``
    (imported at call time), ``instance_state``, the bundled seed source,
    the pack applier (its locked apply reads the module ``engine``) and
    the demo seeder — all are repointed here; nothing else changes.
    """
    factory, engine = app_boot_factory
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", factory)
    monkeypatch.setattr("app.core.instance_state.AsyncSessionLocal", factory)
    monkeypatch.setattr("scripts.seed_demo.AsyncSessionLocal", factory)
    monkeypatch.setattr("app.services.seed.bundled_source.AsyncSessionLocal", factory)
    monkeypatch.setattr("app.services.seed.applier.engine", engine)
    return factory


@pytest.fixture
def demo_env(monkeypatch):
    """A first-boot env: demo mode on, so the seeder's §13 instance guard passes."""
    monkeypatch.setattr(settings, "HA_DEMO_MODE", True)


async def _scalar(factory, sql: str, params: dict | None = None):
    async with factory() as session:
        return (await session.execute(text(sql), params or {})).scalar_one()


# ---------------------------------------------------------------------------
# The fresh-install boot sequence, end to end, as the app role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_boot_operations_work_as_the_app_role(scratch_demo_db, app_role_boot, demo_env):
    tenant_context.clear()

    # 1. Fresh install: instance facts initialize (empty DB consumes env).
    await boot.initialize_instance_state()
    assert (
        await _scalar(app_role_boot, "SELECT value FROM instance_settings WHERE key = 'auth_mode'")
    ) == "authenticated"
    assert (
        await _scalar(app_role_boot, "SELECT value FROM instance_settings WHERE key = 'demo_mode'")
    ) == "true"

    # 2. Bundled apply: the digest-gated pack applies under the system
    #    scope (its NULL-tenant source rows are hidden-flavor — a plain
    #    tenant context can neither read nor write them).
    stats = await boot.apply_bundled_seed_pack()
    assert stats is not None, "a fresh database must apply the bundled pack"

    # 3. Integration registry init.
    await boot.initialize_integration_registry()

    # 4. Demo seed (boot only calls it when demo_mode is on — §13 holds).
    from app.core import instance_state

    assert await instance_state.demo_mode_enabled() is True
    await boot.seed_demo_data(database_url=_db_url(scratch_demo_db))

    # 5. The setup-token count sees the seeded user (pre-fix: 0 under RLS,
    #    so every boot re-announced first-run setup and the stale-token
    #    clear never ran). The count query reads tenants only through the
    #    step's own scope — pinned as the mechanism in the next test.
    await boot.prepare_first_run_setup_token()
    with tenant_context.system_scope("boot:test-verify"):
        async with app_role_boot() as session:
            user_count = (await session.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert int(user_count) == 1, "the demo instance should carry exactly the demo user"

    # The seed lane wrote its NULL-tenant (system) source row.
    with tenant_context.system_scope("boot:test-verify"):
        async with app_role_boot() as session:
            bundled = (
                await session.execute(
                    text("SELECT count(*) FROM seed_sources WHERE kind = 'bundled'")
                )
            ).scalar_one()
    assert int(bundled) >= 1


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_stuck_exam_cleanup_works_as_the_app_role(scratch_demo_db, app_role_boot):
    """A stale processing exam is failed; a fresh one stays untouched."""
    factory = app_role_boot
    tenant_id = uuid.uuid4()
    demo_user_id = uuid.uuid4()
    stale_id = uuid.uuid4()
    fresh_id = uuid.uuid4()

    async with factory() as session:
        with tenant_context.tenant_scope(tenant_id):
            await session.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
                {"id": str(tenant_id), "name": "boot-stale", "slug": f"boot-{tenant_id}"},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, tenant_id, role, is_active) "
                    "VALUES (:id, :email, :tenant_id, 'USER', true)"
                ),
                {
                    "id": str(demo_user_id),
                    "email": f"boot-stale-{tenant_id}@test.local",
                    "tenant_id": str(tenant_id),
                },
            )
            for exam_id, updated_at in (
                (stale_id, datetime.now(UTC) - timedelta(hours=2)),
                (fresh_id, datetime.now(UTC)),
            ):
                await session.execute(
                    text(
                        "INSERT INTO examinations (id, tenant_id, "
                        "extraction_status, extraction_progress, updated_at) "
                        "VALUES (:id, :tenant_id, 'processing', 10, :updated_at)"
                    ),
                    {
                        "id": str(exam_id),
                        "tenant_id": str(tenant_id),
                        "updated_at": updated_at,
                    },
                )
            await session.commit()

    tenant_context.clear()
    cleaned = await boot.cleanup_stuck_examinations()
    assert cleaned >= 1, "the cleanup matched nothing under RLS"

    # Verify through the tenant's own context (the run's operator view is
    # the system scope; the row itself belongs to the seeded tenant).
    # Verify through the tenant's own context (the run's operator view is
    # the system scope; the row itself belongs to the seeded tenant).
    with tenant_context.tenant_scope(tenant_id):
        async with factory() as session:
            stale_status = (
                await session.execute(
                    text("SELECT extraction_status FROM examinations WHERE id = :id"),
                    {"id": str(stale_id)},
                )
            ).scalar_one()
            fresh_status = (
                await session.execute(
                    text("SELECT extraction_status FROM examinations WHERE id = :id"),
                    {"id": str(fresh_id)},
                )
            ).scalar_one()
    assert stale_status == "failed"
    assert fresh_status == "processing"


@pytest.mark.asyncio
async def test_setup_token_count_is_context_dependent(scratch_demo_db, app_boot_factory):
    """The zero-user check answers 0 with a closed context, everything in scope.

    Pins the mechanism ``prepare_first_run_setup_token`` rides: the count
    is blind without its system scope (RLS fail-closed — the pre-fix bug)
    and sees every tenant's users with it.
    """
    factory, _engine = app_boot_factory
    tenant_id = uuid.uuid4()
    email = f"boot-count-{tenant_id}@test.local"
    async with factory() as session:
        with tenant_context.tenant_scope(tenant_id):
            await session.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
                {"id": str(tenant_id), "name": "boot-count", "slug": f"boot-{tenant_id}"},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, tenant_id, role, is_active) "
                    "VALUES (:id, :email, :tenant_id, 'USER', true)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "email": email,
                    "tenant_id": str(tenant_id),
                },
            )
            await session.commit()

    def _count() -> str:
        return "SELECT count(*) FROM users WHERE email = :email"

    # Closed context: zero rows — the answer the boot step must never act on.
    tenant_context.clear()
    assert int(await _scalar(factory, _count(), {"email": email})) == 0

    # A foreign tenant's context: still blind to the seeded user.
    with tenant_context.tenant_scope(uuid.uuid4()):
        assert int(await _scalar(factory, _count(), {"email": email})) == 0

    # The system scope the boot step plants: the user is there.
    with tenant_context.system_scope("boot:setup-token"):
        assert int(await _scalar(factory, _count(), {"email": email})) == 1
