import os
import re
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
    # disable_existing_loggers stays False: the test suite runs alembic
    # in-process (tests/conftest.py), and the default True marks every
    # logger the app already created as disabled — silently dropping
    # records pytest's caplog needs.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata


def get_url():
    """Get database URL from config or environment.

    ADR-0064 tri-state policy lives in ``app.core.db_conninfo.migrations_url``:
    the owner DSN wins; ``HA_DB_SPLIT=1`` without it refuses (fail closed).
    """
    from app.core.db_conninfo import migrations_url

    return migrations_url().replace("+asyncpg", "+psycopg2")


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
    record_migration_head(connection)


def record_migration_head(connection: Connection) -> None:
    """Publish the DB's migration position into an app-readable marker
    (plan 64 §1.4).

    ``0016_rls`` REVOKEs ``alembic_version`` from the runtime role — the
    app can never read the version row, so ``/ready`` cannot check "DB at
    head" against it. Every alembic run (the compose ``migrate`` job, the
    install/update scripts, the test harness) runs as the OWNER and ends
    by writing the DB's reached revision into ``migration_state`` (single
    row, SELECT granted to the app role). The readiness probe compares
    that marker with the head compiled into this release: a half-migrated
    database, a stale migrate, or a fresh database with no migrate yet all
    fail the check instead of taking traffic. A database downgraded to
    base drops the marker — ``downgrade base`` leaves an empty database.

    The marker records what the DATABASE reports (``alembic_version``),
    not the chain's head: the probe's comparison against the release's
    head is what turns "behind" into a 503.
    """
    position = None
    if inspect(connection).has_table("alembic_version"):
        # At base the version table itself is dropped — an empty database.
        position = connection.execute(text("SELECT version_num FROM alembic_version")).first()
    if position is None or not position[0]:
        connection.execute(text("DROP TABLE IF EXISTS migration_state"))
        connection.commit()
        return
    head = str(position[0])
    app_role = os.environ.get("POSTGRES_APP_USER", "neuronection_health_app")
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS migration_state ("
            "id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1), "
            "head_revision TEXT NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    )
    # The role name comes from deployment config, not user input — validate
    # it to a safe identifier shape before interpolation.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", app_role):
        raise ValueError(f"POSTGRES_APP_USER {app_role!r} is not a valid role name")
    # Grant only when the runtime role exists — the same pg_roles guard the
    # RLS migrations (0016/0018…) apply to their grants. Test/CI databases
    # provisioned without the app role (and pre-provisioning environments)
    # skip the grant; production grants normally (the marker stays readable
    # by the app role either way — see /ready).
    role_exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": app_role}
    ).scalar()
    if role_exists:
        connection.execute(text(f"GRANT SELECT ON migration_state TO {app_role}"))
    connection.execute(
        text(
            "INSERT INTO migration_state (id, head_revision) VALUES (1, :head) "
            "ON CONFLICT (id) DO UPDATE SET head_revision = :head, updated_at = now()"
        ),
        {"head": head},
    )
    connection.commit()


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
