"""Deployment-conformance unit tests (plan 64 §1.3/§1.6, ADR-0064).

Pins the seams the conformance gate cannot see from static files:

- cookie ``Secure`` **derivation** from the public scheme / ``HA_TLS``
  (never a static default; LAN/VPN HTTP login keeps working);
- libpq conninfo derived from ``HA_DATABASE_URL`` (serving containers no
  longer receive ``POSTGRES_PASSWORD`` — the checkpointer must still
  connect as the app role);
- the app-role **DDL refusal** the credential split promises (a plain
  LOGIN role cannot CREATE on schema public on PG15+ — the same posture
  ``scripts/provision_db.py post`` verifies at migrate time).
"""

from __future__ import annotations

import uuid

import psycopg2
import pytest

from app.core.config import settings
from tests.settings_factory import dev_settings, prod_settings

# ─── Cookie Secure derivation (plan 64 §1.6) ────────────────────────────────


def test_cookie_secure_derives_true_from_https_app_url():
    s = prod_settings(APP_URL="https://health.example.com")
    assert s.HA_COOKIE_SECURE is True


def test_cookie_secure_derives_false_for_lan_http():
    s = dev_settings(APP_URL="http://192.168.1.20:8090")
    assert s.HA_COOKIE_SECURE is False


def test_cookie_secure_derives_true_from_ha_tls_flag():
    """A BYO TLS proxy in front of an http APP_URL: HA_TLS=true derives
    Secure without hand-coding HA_COOKIE_SECURE=true."""
    s = prod_settings(APP_URL="http://health.internal", HA_TLS=True)
    assert s.HA_COOKIE_SECURE is True


def test_explicit_cookie_secure_wins_over_derivation():
    s = dev_settings(APP_URL="https://health.example.com", HA_COOKIE_SECURE=False)
    assert s.HA_COOKIE_SECURE is False


def test_empty_cookie_secure_env_means_derive(monkeypatch):
    """The compose files render ``${HA_COOKIE_SECURE:-}`` — an empty env
    value must behave exactly like an unset one."""
    monkeypatch.setenv("HA_COOKIE_SECURE", "")
    s = prod_settings(APP_URL="https://health.example.com")
    assert s.HA_COOKIE_SECURE is True


def test_samesite_none_requires_derived_secure(monkeypatch):
    """The §10 invariant holds AFTER derivation: SameSite=None passes with
    an https APP_URL even though no explicit Secure flag was set."""
    s = prod_settings(APP_URL="https://health.example.com", HA_COOKIE_SAMESITE="none")
    assert s.HA_COOKIE_SECURE is True


def test_samesite_none_over_http_still_refuses():
    with pytest.raises(ValueError, match="HA_COOKIE_SAMESITE=none requires"):
        prod_settings(APP_URL="http://lan-host", HA_COOKIE_SAMESITE="none")


# ─── Checkpoint conninfo from the DSN (ADR-0064) ─────────────────────────────


def test_checkpoint_conninfo_parses_the_app_dsn(monkeypatch):
    from app.core.db_conninfo import checkpoint_conninfo

    monkeypatch.setattr(
        settings,
        "HA_DATABASE_URL",
        "postgresql+asyncpg://app_user:p%40ss%3Aword@db-host:6543/health_db",
    )
    conninfo = checkpoint_conninfo()
    assert "user=app_user" in conninfo
    assert "password=p@ss:word" in conninfo  # URL-decoded
    assert "host=db-host" in conninfo
    assert "port=6543" in conninfo
    assert "dbname=health_db" in conninfo


def test_checkpoint_conninfo_quotes_passwords_with_spaces(monkeypatch):
    from app.core.db_conninfo import checkpoint_conninfo

    monkeypatch.setattr(
        settings,
        "HA_DATABASE_URL",
        "postgresql+asyncpg://app_user:two%20words@db-host:5432/health_db",
    )
    conninfo = checkpoint_conninfo()
    assert "password='two words'" in conninfo


def test_checkpoint_conninfo_falls_back_to_postgres_settings(monkeypatch):
    """Dev/test assemble path: no URL password → POSTGRES_* settings."""
    from app.core.db_conninfo import checkpoint_conninfo

    monkeypatch.setattr(settings, "HA_DATABASE_URL", None)
    monkeypatch.setattr(settings, "POSTGRES_HOST", "localhost")
    monkeypatch.setattr(settings, "POSTGRES_PORT", 5432)
    monkeypatch.setattr(settings, "POSTGRES_USER", "neuronection_health_owner")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "dev-secret")
    monkeypatch.setattr(settings, "POSTGRES_DB", "neuronection_health_test")
    conninfo = checkpoint_conninfo()
    assert "user=neuronection_health_owner" in conninfo
    assert "password=dev-secret" in conninfo


# ─── ADR-0064 tri-state: the "broken" state refuses (alembic env) ───────────


def test_migrations_url_refuses_split_marker_without_migrations_url(monkeypatch):
    """HA_DB_SPLIT=1 with no HA_MIGRATIONS_DATABASE_URL is the ADR's
    'broken' state — refuse, never silently fall back to the app DSN."""
    from app.core.db_conninfo import migrations_url

    monkeypatch.setattr(settings, "HA_MIGRATIONS_DATABASE_URL", None)
    monkeypatch.setattr(settings, "HA_DB_SPLIT", "1")
    monkeypatch.setattr(settings, "HA_DATABASE_URL", "postgresql+asyncpg://app:x@db/health")
    with pytest.raises(SystemExit, match="HA_DB_SPLIT=1 requires"):
        migrations_url()


def test_migrations_url_prefers_the_owner_url(monkeypatch):
    from app.core.db_conninfo import migrations_url

    monkeypatch.setattr(
        settings, "HA_MIGRATIONS_DATABASE_URL", "postgresql+asyncpg://owner:x@db/health"
    )
    monkeypatch.setattr(
        settings, "HA_DATABASE_URL", "postgresql+asyncpg://app:y@db/health"
    )
    assert migrations_url().startswith("postgresql+asyncpg://owner:"), "the owner DSN must win"


def test_migrations_url_requires_a_url(monkeypatch):
    from app.core.db_conninfo import migrations_url

    monkeypatch.setattr(settings, "HA_MIGRATIONS_DATABASE_URL", None)
    monkeypatch.setattr(settings, "HA_DATABASE_URL", None)
    monkeypatch.setattr(settings, "HA_DB_SPLIT", None)
    with pytest.raises(ValueError, match="must be defined"):
        migrations_url()


# ─── App-role DDL refusal (ADR-0064 §3) ──────────────────────────────────────


def _admin_conn():
    return psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB,
    )


def test_app_role_privileges_refuse_ddl():
    """A least-privilege runtime role (LOGIN only, CONNECT granted) cannot
    CREATE on schema public — the guarantee the owner/app credential split
    buys (plan 64 Gate A). Mirrors scripts/provision_db.py's post-phase
    verification with a live connection."""
    role = f"ha_ddl_probe_{uuid.uuid4().hex[:12]}"
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE ROLE {role} LOGIN PASSWORD 'ddl-probe-password'"
            )
            cur.execute(f"GRANT CONNECT ON DATABASE \"{settings.POSTGRES_DB}\" TO {role}")
        conn.commit()
    finally:
        conn.close()

    probe = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=role,
        password="ddl-probe-password",
        dbname=settings.POSTGRES_DB,
    )
    try:
        with probe.cursor() as cur, pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("CREATE TABLE ha_ddl_probe_should_fail (id int)")
    finally:
        probe.close()
        conn = _admin_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"REVOKE CONNECT ON DATABASE \"{settings.POSTGRES_DB}\" FROM {role}"
                )
                cur.execute(f"DROP ROLE IF EXISTS {role}")
            conn.commit()
        finally:
            conn.close()
