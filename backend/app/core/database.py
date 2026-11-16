import json
import logging
from collections.abc import AsyncGenerator
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core import tenant_context
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


# The RLS request context (ADR 0054 D4, program phase 11): both lanes'
# sessions plant the contextvars GUCs at every transaction begin — a
# distinct ``sync_session_class`` per factory scopes the hook to the
# application's sessions only.
class _ContextSession(Session):
    """Sync session class whose ``after_begin`` plants the RLS GUCs."""


class _AuditContextSession(Session):
    """The audit lane's session class (same hook, own type)."""


class ContextAsyncSession(AsyncSession):
    """One-off :class:`AsyncSession` whose transactions plant the RLS GUCs.

    Sessions built directly on a bare connection — the seed applier's
    locked apply runs ``ContextAsyncSession(bind=conn)`` — bypass
    ``async_sessionmaker`` and its ``after_begin`` hook, so their
    transactions would otherwise run with no planted context and answer
    nothing under row-level security. This class carries the same
    ``sync_session_class`` as the pools, so every transaction it begins
    plants the ambient context exactly like the pool sessions do.
    """

    sync_session_class = _ContextSession


try:
    assert settings.HA_DATABASE_URL, "HA_DATABASE_URL must be configured"
    engine = create_async_engine(
        settings.HA_DATABASE_URL,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=settings.DEBUG,
        json_serializer=json_serializer,
    )
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, sync_session_class=_ContextSession
    )
    tenant_context.install_session_hook(AsyncSessionLocal)
    DATABASE_AVAILABLE = True
except Exception as e:
    logger.error(f"Could not initialize database engine: {e}")
    engine = None  # type: ignore[assignment]  # fail-soft; DATABASE_AVAILABLE gates use
    AsyncSessionLocal = None  # type: ignore[assignment]  # fail-soft; DATABASE_AVAILABLE gates use
    DATABASE_AVAILABLE = False

# The audit lane's own small pool (ADR 0054 D3): audit writes — required
# (fail-closed) or post-rollback denial records — never borrow a request
# connection, and an audit storm cannot exhaust the request pool. The pool
# size is a setting (``AUDIT_DB_POOL_SIZE``) pinned by tests.
try:
    assert settings.HA_DATABASE_URL, "HA_DATABASE_URL must be configured"
    audit_engine = create_async_engine(
        settings.HA_DATABASE_URL,
        pool_size=settings.AUDIT_DB_POOL_SIZE,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=False,
        json_serializer=json_serializer,
    )
    AuditSessionLocal = async_sessionmaker(
        bind=audit_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        sync_session_class=_AuditContextSession,
    )
    tenant_context.install_session_hook(AuditSessionLocal)
    AUDIT_DATABASE_AVAILABLE = True
except Exception as e:
    logger.error(f"Could not initialize the audit lane engine: {e}")
    audit_engine = None  # type: ignore[assignment]  # fail-soft; AUDIT_DATABASE_AVAILABLE gates use
    AuditSessionLocal = None  # type: ignore[assignment]  # fail-soft; AUDIT_DATABASE_AVAILABLE gates use
    AUDIT_DATABASE_AVAILABLE = False


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
