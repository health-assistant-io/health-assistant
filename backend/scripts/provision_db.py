#!/usr/bin/env python3
"""Owner-side database provisioning seam (ADR-0064 §3, plan 64).

Runs inside the one-shot ``migrate`` service, as the **owner** role, never
from a serving container:

    python scripts/provision_db.py pre    # extensions + app role + grants + ledger table
    alembic upgrade head
    python scripts/setup_checkpoints.py
    python scripts/provision_db.py post   # re-grant + app-role privilege verification

``pre`` provisions (idempotent, ordered per ADR-0064 §3):

1. extensions — ``timescaledb`` (availability-checked, Timescale-optional
   image), ``pgcrypto``, ``pg_trgm`` (hard deps, init-db.sql parity);
2. the app role (created from ``POSTGRES_APP_PASSWORD`` when missing);
3. grants — CONNECT, schema USAGE, SELECT/INSERT/UPDATE/DELETE on existing
   tables, USAGE/SELECT on sequences, and ``ALTER DEFAULT PRIVILEGES`` so
   future owner-created objects are usable by the app role automatically;
4. the ``backup_runs`` ledger table (ADR-0063 §2: the backup script is the
   writer; the app role gets INSERT/SELECT so host-side psql and the app can
   read staleness).

``post`` re-asserts the DEFAULT PRIVILEGES (owner-created objects keep
flowing to the app role) and **verifies** the app role is DML-only: no
SUPERUSER/CREATEROLE/CREATEDB and no CREATE on schema public. A violation
exits non-zero so the migrate job fails loudly instead of shipping a
least-privilege lie.

Deliberately NO table-level blanket re-grant in ``post``: the migration
chain owns table-level ACLs after ``pre`` — ``0016_rls`` REVOKEs
``alembic_version`` and the raw caggs from the app role, and a post-alembic
re-grant would silently re-enable them (the runtime role must never read
the version row or raw aggregates). Tables created by alembic are covered
by the default privileges; a dump restored without them re-runs ``pre``.

Usage (from backend/, owner env — the compose ``migrate`` service provides
it; dev uses the POSTGRES_* settings):
    python scripts/provision_db.py {pre,post}
"""

from __future__ import annotations

import asyncio
import sys

import psycopg2
from psycopg2 import sql

from app.core.config import settings
from app.core.db_conninfo import dsn_parts

APP_USER = "neuronection_health_app"

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS backup_runs (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL,
    bytes       BIGINT,
    destination TEXT,
    checksum    TEXT,
    error_class TEXT
)
"""


def _connect() -> psycopg2.extensions.connection:
    """One owner connection (HA_MIGRATIONS_DATABASE_URL, falling back to
    the POSTGRES_* dev settings)."""
    parts = dsn_parts(migrations=True)
    return psycopg2.connect(
        host=parts["host"],
        port=parts["port"],
        user=parts["user"],
        password=parts["password"],
        dbname=parts["dbname"],
    )


def _app_password() -> str:
    """The app role's secret (compose passes HA_DB_PASSWORD through as
    POSTGRES_APP_PASSWORD on the db + migrate services; ADR-0064 keeps it
    distinct from the owner password)."""
    password = settings.POSTGRES_PASSWORD
    app_password = settings.POSTGRES_APP_PASSWORD or ""
    if not app_password:
        print(
            "provision: POSTGRES_APP_PASSWORD not set — cannot (re)create the app role. "
            "Grants/default privileges still applied (ADR-0064 pre-phase requires it "
            "on the db service; add it to the migrate env to allow role creation).",
            file=sys.stderr,
        )
    elif app_password == password:
        print(
            "provision: refusing — POSTGRES_APP_PASSWORD equals the owner password "
            "(ADR-0064 requires distinct secrets); role creation skipped.",
            file=sys.stderr,
        )
        return ""
    return app_password


def _extensions(cur: psycopg2.extensions.cursor) -> None:
    # timescaledb stays availability-checked (Timescale-optional image —
    # a hard CREATE would abort provisioning, parity with init-db.sql).
    cur.execute(
        "SELECT 'CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE' "
        "WHERE EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb')"
    )
    row = cur.fetchone()
    if row and row[0]:
        cur.execute(row[0])
    for extension in ("pgcrypto", "pg_trgm"):
        cur.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
    print("provision: extensions ensured (timescaledb?, pgcrypto, pg_trgm)")


def _ensure_app_role(cur: psycopg2.extensions.cursor, app_password: str) -> None:
    cur.execute("SELECT FROM pg_roles WHERE rolname = %s", (APP_USER,))
    if cur.fetchone() is None:
        if not app_password:
            raise SystemExit(
                f"provision: app role '{APP_USER}' is missing and POSTGRES_APP_PASSWORD "
                "is unset — cannot continue (ADR-0064: the app role is required)."
            )
        cur.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(APP_USER)),
            (app_password,),
        )
        print(f"provision: created app role '{APP_USER}'")
    else:
        if app_password:
            cur.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(APP_USER)),
                (app_password,),
            )
        print(f"provision: app role '{APP_USER}' present (password synced)")


_GRANT_APP_DML = """
DO $$
DECLARE app_user text := {app_user!r};
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), app_user);
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', app_user);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
        app_user);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I',
        app_user);
    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', app_user);
    EXECUTE format(
        'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', app_user);
END $$;
"""

_DEFAULT_PRIVILEGES = """
DO $$
DECLARE app_user text := {app_user!r};
BEGIN
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
        app_user);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I',
        app_user);
END $$;
"""


def _grants(cur: psycopg2.extensions.cursor, label: str) -> None:
    """Pre-alembic blanket grant (init-roles.sh parity): tables existing at
    this point + default privileges for everything the owner creates after.
    Runs BEFORE alembic so the migration chain's explicit REVOKEs win."""
    cur.execute(_GRANT_APP_DML.format(app_user=APP_USER))
    print(f"provision: app-role grants + default privileges ensured ({label})")


def _default_privileges(cur: psycopg2.extensions.cursor) -> None:
    """Post-alembic: re-assert only the DEFAULT PRIVILEGES — never table
    ACLs (the migration chain's REVOKEs must survive)."""
    cur.execute(_DEFAULT_PRIVILEGES.format(app_user=APP_USER))
    print("provision: default privileges re-asserted (post-alembic)")


def _ledger_table(cur: psycopg2.extensions.cursor) -> None:
    cur.execute(LEDGER_DDL)
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE backup_runs TO " + APP_USER
    )
    cur.execute("GRANT USAGE, SELECT ON SEQUENCE backup_runs_id_seq TO " + APP_USER)
    print("provision: backup_runs ledger table ensured (ADR-0063)")


def _verify_app_role_dml_only(cur: psycopg2.extensions.cursor) -> None:
    """Typed refusal on any privilege drift (plan 64 §1.3: tests and the
    migrate job both assert the app role cannot DDL)."""
    cur.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = %s",
        (APP_USER,),
    )
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"provision: app role '{APP_USER}' missing at verification")
    rolsuper, rolcreaterole, rolcreatedb = row
    if rolsuper or rolcreaterole or rolcreatedb:
        raise SystemExit(
            f"provision: app role '{APP_USER}' holds DDL-grade role attributes "
            f"(superuser={rolsuper}, createrole={rolcreaterole}, createdb={rolcreatedb})"
        )
    cur.execute(
        "SELECT has_schema_privilege(%s, 'public', 'CREATE')", (APP_USER,)
    )
    if cur.fetchone()[0]:
        raise SystemExit(
            f"provision: app role '{APP_USER}' holds CREATE on schema public — "
            "the runtime role must be DML-only (ADR-0064)."
        )
    print(f"provision: verified '{APP_USER}' is DML-only (no superuser/role-create/schema-create)")


async def _run(phase: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if phase == "pre":
                _extensions(cur)
                _ensure_app_role(cur, _app_password())
                _grants(cur, "pre-alembic")
                _ledger_table(cur)
            elif phase == "post":
                # Default privileges only — NO table-level blanket re-grant:
                # the migration chain owns table ACLs after pre (0016_rls
                # revokes alembic_version + raw caggs; re-granting them here
                # would silently re-enable runtime access). See the module
                # docstring.
                _default_privileges(cur)
                _ledger_table(cur)
                _verify_app_role_dml_only(cur)
            else:
                raise SystemExit(f"unknown provision phase {phase!r} (use pre|post)")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("pre", "post"):
        raise SystemExit("usage: python scripts/provision_db.py {pre,post}")
    asyncio.run(_run(sys.argv[1]))
