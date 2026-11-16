"""Libpq conninfo helpers derived from the configured DSN.

ADR-0064: the serving services receive only the app role's DSN
(``HA_DATABASE_URL``) — the raw ``POSTGRES_PASSWORD`` env no longer rides
the container env. Anything that needs libpq conninfo (the LangGraph
checkpoint saver, the owner-side provisioning seam) parses the DSN instead
of reading separate ``POSTGRES_USER``/``POSTGRES_PASSWORD`` settings, which
remain as the ``assemble_db_connection`` fallback for local dev/tests only.
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from app.core.config import settings


def _quote(value: str) -> str:
    """Quote one libpq conninfo parameter (spaces/backslashes/quotes)."""
    if value == "":
        return "''"
    if " " in value or "'" in value or "\\" in value:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return value


def dsn_parts(*, migrations: bool = False) -> dict[str, str]:
    """host/port/user/password/dbname from the DSN.

    ``migrations=True`` (owner-side DDL entrypoints) prefers
    ``HA_MIGRATIONS_DATABASE_URL`` and falls back to ``HA_DATABASE_URL``
    (legacy single-DSN dev setups). Runtime callers read the app DSN.
    Per-component fallback goes to the ``POSTGRES_*`` settings (dev/test
    assemble path).
    """
    url = settings.HA_DATABASE_URL or ""
    if migrations:
        url = settings.HA_MIGRATIONS_DATABASE_URL or url
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or settings.POSTGRES_HOST,
        "port": str(parsed.port or settings.POSTGRES_PORT),
        "user": unquote(parsed.username or "") or settings.POSTGRES_USER,
        "password": unquote(parsed.password or "") or settings.POSTGRES_PASSWORD,
        "dbname": (parsed.path or "").lstrip("/") or settings.POSTGRES_DB,
    }


def checkpoint_conninfo() -> str:
    """Libpq conninfo for the checkpoint tables (same DB as the app)."""
    return conninfo_from_parts(dsn_parts())


def conninfo_from_parts(parts: dict[str, str]) -> str:
    """Render dsn_parts() output as a quoted libpq conninfo string."""
    return (
        f"host={_quote(parts['host'])} "
        f"port={_quote(parts['port'])} "
        f"user={_quote(parts['user'])} "
        f"password={_quote(parts['password'])} "
        f"dbname={_quote(parts['dbname'])}"
    )


def migrations_url() -> str:
    """The OWNER DSN for DDL entrypoints (alembic, provision_db,
    setup_checkpoints) — ADR-0064 tri-state, fail closed:

    - ``HA_MIGRATIONS_DATABASE_URL`` wins (the compose migrate service sets
      it; serving containers never see it);
    - fallback to ``HA_DATABASE_URL`` only in the legacy single-DSN shape;
    - ``HA_DB_SPLIT=1`` without a migrations URL is the ADR's "broken"
      state: refuse, never silently run DDL against the app DSN.
    """
    url = settings.HA_MIGRATIONS_DATABASE_URL or settings.HA_DATABASE_URL
    if not url:
        raise ValueError(
            "HA_MIGRATIONS_DATABASE_URL (or HA_DATABASE_URL) must be defined to run migrations"
        )
    if settings.HA_DB_SPLIT == "1" and not settings.HA_MIGRATIONS_DATABASE_URL:
        raise SystemExit(
            "HA_DB_SPLIT=1 requires HA_MIGRATIONS_DATABASE_URL (ADR-0064 'broken' state — "
            "refusing to migrate against the app DSN)."
        )
    return url
