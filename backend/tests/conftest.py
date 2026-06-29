import uuid
from typing import Dict

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def run_migrations():
    """Run migrations on the test database at the beginning of the test session"""
    from alembic import command
    from alembic.config import Config
    import logging

    # Suppress verbose alembic logs during test setup unless needed
    logging.getLogger("alembic").setLevel(logging.WARNING)

    # Load alembic configuration from the backend root
    alembic_cfg = Config("alembic.ini")
    
    # Run migrations
    command.upgrade(alembic_cfg, "head")


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
