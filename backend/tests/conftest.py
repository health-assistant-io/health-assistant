import os
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# The local object-storage backend defaults to HA_UPLOAD_DIR; the production
# default (/var/healthassistant) is not writable for a developer/test user.
# Pin a fresh writable root before Settings is constructed unless the caller
# explicitly configured one.
os.environ.setdefault("HA_UPLOAD_DIR", tempfile.mkdtemp(prefix="ha-test-uploads-"))

from app.core.authz.enforce import get_optional_session_user
from app.core.security import get_current_user
from app.main import app

# The data tables the truncate pass must NOT clear (ops-owned state): the
# migration head marker alembic writes for /ready, and the backup ledger.
_TRUNCATE_EXEMPT_TABLES = ("migration_state", "backup_runs")


@pytest.fixture(scope="session", autouse=True)
def run_migrations():
    """Run migrations on the test database at the beginning of the test session,
    then truncate every data table so each pytest invocation starts from a
    clean slate.

    Without the truncate, committed rows from prior test sessions accumulate
    forever (the test DB is never reset) and eventually push newly-created
    test rows off the first page of paginated endpoints — causing catalog
    search / concept list assertions to flake.
    """
    import logging

    from alembic.config import Config

    from alembic import command

    # Suppress verbose alembic logs during test setup unless needed
    logging.getLogger("alembic").setLevel(logging.WARNING)

    # Load alembic configuration from the backend root
    alembic_cfg = Config("alembic.ini")

    # Run migrations
    command.upgrade(alembic_cfg, "head")

    # Belt and braces against logger disabling (fileConfig in alembic env.py
    # used to mark every pre-existing logger disabled): whatever ran before,
    # no app logger may stay disabled for the suite — caplog asserts depend
    # on their records.
    for logger_obj in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger_obj, logging.Logger):
            logger_obj.disabled = False

    # Wipe all data tables (keep alembic_version so migrations aren't rerun).
    # We only do this for the *_test database to avoid nuking a dev DB if the
    # env is misconfigured.
    from app.core.config import settings

    if not settings.POSTGRES_DB.endswith("_test"):
        return

    import psycopg2

    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB,
    )
    try:
        with conn.cursor() as cur:
            exempt = ", ".join(f"'{t}'" for t in _TRUNCATE_EXEMPT_TABLES)
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version' "
                f"AND tablename NOT IN ({exempt})"
            )
            tables = [r[0] for r in cur.fetchall()]
            if tables:
                cur.execute('TRUNCATE TABLE "' + '", "'.join(tables) + '" RESTART IDENTITY CASCADE')
            # The AI task vocabulary is a seeded lookup (plan 4 W2): boot seeds
            # it from the TaskDef registry, and the truncated test DB needs the
            # same rows before any assignment / audit write can FK into it.
            from app.ai.providers.task_defs import task_def_rows

            cur.executemany(
                "INSERT INTO ai_task_defs (id, code, label_key, enabled) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (code) DO NOTHING",
                [
                    (str(row["id"]), row["code"], row["label_key"], row["enabled"])
                    for row in task_def_rows()
                ],
            )
            # The proposal-kind vocabulary is the same shape (plan 5 W1): boot
            # seeds it from the family contract, so the truncated test DB needs
            # the rows before any proposal can FK into them.
            from app.extensions.proposal_kinds import kind_rows

            cur.executemany(
                "INSERT INTO proposal_kinds "
                "(id, code, tier, schema_ref, resolver, card_variant, enabled) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (code) DO NOTHING",
                [
                    (
                        str(row["id"]),
                        row["code"],
                        row["tier"],
                        row["schema_ref"],
                        row["resolver"],
                        row["card_variant"],
                        row["enabled"],
                    )
                    for row in kind_rows()
                ],
            )
            # Retention windows are data too (plan 6 W3): the truncated test DB
            # needs the seeded policy rows before retention/checkpoint tests
            # read their cutoffs.
            from app.services.data_lifecycle.retention_policies import policy_rows

            cur.executemany(
                "INSERT INTO retention_policies "
                "(id, scope, scope_value, action, days, timestamp_column, enabled) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [
                    (
                        str(row["id"]),
                        row["scope"],
                        row["scope_value"],
                        row["action"],
                        row["days"],
                        row["timestamp_column"],
                        row["enabled"],
                    )
                    for row in policy_rows()
                ],
            )
        conn.commit()
    finally:
        conn.close()

    # LangGraph's checkpoint tables are runtime-managed (not in the migration
    # lane): ``AsyncPostgresSaver.setup()`` creates them with
    # ``CREATE INDEX CONCURRENTLY``, which waits for every open transaction to
    # end. On a database where they do not exist yet, the first chat-graph test
    # opening a CheckpointStore while holding an open session deadlocks the
    # suite. Rebuild them here — before any test opens a transaction — so the
    # shape always matches the installed langgraph version (a table left over
    # from an older library version silently breaks checkpoint writes).
    import asyncio

    from app.ai.graphs.checkpointer import CheckpointStore

    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, "
                "checkpoints, checkpoint_migrations CASCADE"
            )
        conn.commit()
    finally:
        conn.close()

    async def _prepare_checkpoint_tables() -> None:
        store = CheckpointStore()
        await store.open()
        await store.close()

    asyncio.run(_prepare_checkpoint_tables())


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def system_admin_headers() -> dict[str, str]:
    """Authorization headers carrying a real JWT for a SYSTEM_ADMIN user.

    Creates a real tenant row (so tenant-scoped FK constraints — e.g.
    ``anatomy_structures.tenant_id -> tenants.id`` — are satisfied for
    integration tests that persist rows) and a **real user row** (the live
    verifier checks ``is_active`` + the ``ver`` claim against
    ``users.token_version`` — identity-auth §8/§18.9), then signs in
    through the real issuance path (contract claims + registered session
    jti + ``auth_sessions`` family). The full auth path
    (``get_token`` -> ``get_current_user`` -> ``RoleChecker``) is
    exercised end-to-end. Each test gets its own isolated tenant + user
    via a UUID-derived slug.
    """
    from app.models.enums import Role
    from tests._auth_helpers import auth_headers, create_user

    user = await create_user(role=Role.SYSTEM_ADMIN, full_name="Test Sysadmin")
    return await auth_headers(user, step_up=True)


@pytest_asyncio.fixture(autouse=True)
async def clear_db_engine_pool():
    yield
    from app.core.database import audit_engine, engine

    if engine:
        await engine.dispose()
    if audit_engine:
        await audit_engine.dispose()


# --- Central-gate ↔ legacy-override bridge ---------------------------------
#
# The central route gate (app.core.authz.enforce) resolves the session
# principal through `get_optional_session_user`, not through
# `get_current_user` — so the older endpoint tests that stub the principal
# via `app.dependency_overrides[get_current_user] = ...` (without sending a
# real bearer token) would otherwise 401 before reaching their route. This
# bridge mirrors every `get_current_user` override onto the gate's user
# source, in test infrastructure only: production behavior is untouched and
# the gate still applies its permission/scope checks to whatever principal
# the test provides (a stub that the matrix denies is denied, exactly as a
# real request would be).


def _mirror_override(value):
    """Adapt an override value (callable, async callable, or mock) for the gate."""
    import inspect

    async def _gate_user():
        result = value()
        while inspect.isawaitable(result):
            result = await result
        return result

    return _gate_user


# Captured at import time — never compare against a re-imported module
# attribute (tests monkeypatch ``app.core.security.get_current_user``).
_GATE_USER_DEP = get_current_user
_GATE_SESSION_DEP = get_optional_session_user


class _GateAwareOverrides(dict):
    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        if key is _GATE_USER_DEP:
            super().__setitem__(_GATE_SESSION_DEP, _mirror_override(value))


@pytest_asyncio.fixture(autouse=True)
async def bridge_gate_user_overrides():
    original = app.dependency_overrides
    app.dependency_overrides = _GateAwareOverrides(original)
    try:
        yield
    finally:
        current = app.dependency_overrides
        app.dependency_overrides = dict(current) if current is not original else {}


# --- Fail-closed audit ↔ stubbed principals --------------------------------
#
# The audit write is fail-closed (Phase 4A): a required write whose tenant
# has no row raises ``AuditWriteError`` and the request answers 500. Older
# endpoint tests stub the principal with a synthetic tenant (no ``tenants``
# row), so their requests would be refused by the very policy they don't
# test. The ``stub_audit_write`` fixture patches the audit write in the
# service namespace and in every endpoint/service module binding — opt-in
# per test module, never for modules that assert audit rows.

_AUDIT_WRITE_MODULES: tuple[str, ...] = (
    "app.services.audit.audit_service",
    "app.api.v1.endpoints.admin",
    "app.api.v1.endpoints.admin_tenants",
    "app.api.v1.endpoints.allergies",
    "app.api.v1.endpoints.auth",
    "app.api.v1.endpoints.clinical_events",
    "app.api.v1.endpoints.documents",
    "app.api.v1.endpoints.examinations",
    "app.api.v1.endpoints.medications",
    "app.api.v1.endpoints.me_mfa",
    "app.api.v1.endpoints.me",
    "app.api.v1.endpoints.observations",
    "app.api.v1.endpoints.patients",
    "app.api.v1.endpoints.users",
    "app.api.v1.endpoints.vaccines",
    "app.core.authz.enforce",
    "app.services.auth.patient_grants",
    "app.services.tenancy.tenant_admin_service",
)


@pytest.fixture
def stub_audit_write():
    """Patch every ``log_audit_action`` binding with one shared AsyncMock.

    Returns the mock for call assertions. Audit-asserting tests must NOT
    request this fixture (their rows would be mocks).
    """
    from unittest.mock import AsyncMock, patch

    audit_mock = AsyncMock()
    patches = [
        patch(f"{module}.log_audit_action", new=audit_mock) for module in _AUDIT_WRITE_MODULES
    ]
    for p in patches:
        p.start()
    try:
        yield audit_mock
    finally:
        for p in patches:
            p.stop()
