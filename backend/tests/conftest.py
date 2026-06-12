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


@pytest_asyncio.fixture(autouse=True)
async def clear_db_engine_pool():
    yield
    from app.core.database import engine
    if engine:
        await engine.dispose()
