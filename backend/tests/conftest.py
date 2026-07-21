import uuid
from typing import Dict

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
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
    from alembic import command
    from alembic.config import Config
    import logging

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
                cur.execute(
                    'TRUNCATE TABLE "'
                    + '", "'.join(tables)
                    + '" RESTART IDENTITY CASCADE'
                )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def system_admin_headers() -> Dict[str, str]:
    """Authorization headers carrying a real JWT for a SYSTEM_ADMIN user.

    Creates a real tenant row first (so tenant-scoped FK constraints — e.g.
    ``anatomy_structures.tenant_id -> tenants.id`` — are satisfied for
    integration tests that persist rows) and then mints a genuine JWT
    referencing it. The full auth path (``get_token`` -> ``get_current_user``
    -> ``RoleChecker``) is exercised end-to-end. Each test gets its own
    isolated tenant via a UUID-derived slug.
    """
    from app.core.database import AsyncSessionLocal
    from app.core.security import create_access_token
    from app.models.tenant_model import TenantModel

    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(
                id=tenant_id,
                name="Test Tenant",
                slug=f"test-tenant-{tenant_id}",
            )
        )
        await session.commit()

    token = create_access_token(
        {
            "sub": "sysadmin@test.local",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": "SYSTEM_ADMIN",
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(autouse=True)
async def clear_db_engine_pool():
    yield
    from app.core.database import engine
    if engine:
        await engine.dispose()
