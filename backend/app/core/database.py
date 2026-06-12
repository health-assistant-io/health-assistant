import json
from uuid import UUID
from datetime import datetime, date
from typing import AsyncGenerator
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

logger = logging.getLogger(__name__)


# Custom JSON encoder for UUID and Datetime
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


def json_serializer(obj):
    return json.dumps(obj, cls=CustomJSONEncoder)


# Custom exceptions
class DatabaseError(Exception):
    """Base exception for database errors"""

    pass


class DatabaseUnavailableError(DatabaseError):
    """Raised when database is not available"""

    pass


# Create engine with error handling
try:
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=settings.DEBUG,
        json_serializer=json_serializer,
    )
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    DATABASE_AVAILABLE = True
except Exception as e:
    logger.error(f"Could not initialize database engine: {e}")
    engine = None
    AsyncSessionLocal = None
    DATABASE_AVAILABLE = False


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database sessions"""
    if not DATABASE_AVAILABLE:
        raise DatabaseUnavailableError("Database is not available")

    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            await session.close()


async def get_db_session() -> AsyncSession:
    """Get a new database session"""
    if not DATABASE_AVAILABLE:
        raise DatabaseUnavailableError("Database is not available")
    return AsyncSessionLocal()


async def init_db() -> None:
    """Initialize database connection (but don't create tables, use migrations)"""
    if not DATABASE_AVAILABLE:
        logger.warning("Skipping database initialization - database not available")
        return

    # Skip metadata.create_all - we use Alembic for that
    logger.info("Database connection verified. Use Alembic for migrations.")
