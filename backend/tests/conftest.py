import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


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
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
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
    return await auth_headers(user)


@pytest_asyncio.fixture(autouse=True)
async def clear_db_engine_pool():
    yield
    from app.core.database import engine

    if engine:
        await engine.dispose()
