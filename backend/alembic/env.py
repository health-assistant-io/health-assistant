from logging.config import fileConfig

from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, inspect, pool, text
from sqlalchemy.engine import Connection

from alembic import context

# Import your models' Base
from app.core.schema_gate import include_object
from app.models.base import Base

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata


def get_url():
    """Get database URL from config or environment"""
    # Use the Pydantic settings model which correctly handles fallbacks,
    # environment variables, and Docker network hostnames.
    from app.core.config import settings

    if not settings.HA_DATABASE_URL:
        raise ValueError("HA_DATABASE_URL must be defined to run migrations")

    url = settings.HA_DATABASE_URL
    # Convert async URL to sync for Alembic
    return url.replace("+asyncpg", "+psycopg2")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


UNSUPPORTED_CHAIN_MESSAGE = (
    "This database was created by Health Assistant v0.8.0 or earlier. v0.9.0 "
    "replaced the migration chain with 0001_baseline, so the recorded "
    "revision(s) {revisions} no longer exist and an in-place upgrade is not "
    "supported.\n"
    "Back up the database first, then choose one route:\n"
    "  - no real data yet: recreate the stack with "
    "./scripts/install.sh --reset --yes\n"
    "  - keep your data: docs/INSTALL.md -> Updates -> 'Upgrading from v0.8.0'\n"
    "    (dump, fresh PostgreSQL 16 volume, restore; the pre-0.9 schema is not\n"
    "    migrated in place).\n"
    "Nothing was changed."
)


def supported_revisions() -> set[str]:
    """Revision ids present in this checkout's migration chain."""
    return {revision.revision for revision in ScriptDirectory.from_config(config).walk_revisions()}


def assert_database_revision_supported(connection: Connection) -> None:
    """Refuse a database whose recorded revision left this chain.

    v0.9.0 squashed the published v0.8.0 chain into ``0001_baseline``; an
    older database records an id that no longer exists, and Alembic's own
    failure ("Can't locate revision identified by ...") gives the operator
    nothing to act on. This preflight runs on every online migration path
    (compose ``migrate``, install/update scripts, bare CLI) and turns that
    into the supported routes. An empty database and a database at a known
    revision both pass — the normal chain is untouched.
    """
    known = supported_revisions()
    if not known:
        return
    try:
        if not inspect(connection).has_table("alembic_version"):
            return
        recorded = [
            revision
            for revision in connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
            if revision
        ]
    finally:
        if connection.in_transaction():
            connection.rollback()

    unknown = sorted(set(recorded) - known)
    if unknown:
        raise SystemExit(UNSUPPORTED_CHAIN_MESSAGE.format(revisions=", ".join(unknown)))


def run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    assert_database_revision_supported(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()

    # Use sync engine for Alembic
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
