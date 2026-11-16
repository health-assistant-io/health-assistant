#!/bin/bash
# Health Assistant — Postgres role bootstrap (runs once via
# /docker-entrypoint-initdb.d, on first boot of an empty data volume;
# extensions are handled by init-db.sql which sorts first).
#
# Creates the two-role credential model (ADR-0064 / deployment.md):
#   neuronection_health_owner — POSTGRES_USER: owns the schema and runs
#                               migrations (DDL). Its secret is
#                               POSTGRES_PASSWORD (the image bootstrap
#                               password, sourced from the deployment's
#                               owner credential; scripts/restore.sh and
#                               the one-shot `migrate` service use it too).
#   neuronection_health_app   — runtime role: CONNECT + DML only, never DDL.
#                               Its secret is POSTGRES_APP_PASSWORD —
#                               DISTINCT from the owner password (ADR-0064:
#                               the compose files pass HA_DB_PASSWORD here;
#                               a shared value refuses to init).
#
# The app containers only ever receive the app credential (via
# HA_DATABASE_URL), so a leaked app container cannot CREATE or DROP
# anything.
#
# Optionally creates the companion test database (POSTGRES_TEST_DB) next to
# the main DB — the dev-db flavor sets it to neuronection_health_test (backend
# tests refuse any database whose name does not end in _test).
set -e

DB="${POSTGRES_DB:-neuronection_health}"
OWNER="${POSTGRES_USER:-neuronection_health_owner}"
APP_USER="${POSTGRES_APP_USER:-neuronection_health_app}"
APP_PASSWORD="${POSTGRES_APP_PASSWORD:-}"
TEST_DB="${POSTGRES_TEST_DB:-}"

if [ -z "$APP_PASSWORD" ]; then
  echo "POSTGRES_APP_PASSWORD must be set — cannot create role '${APP_USER}' (ADR-0064: distinct owner/app credentials)." >&2
  exit 1
fi
if [ -z "${POSTGRES_PASSWORD:-}" ]; then
  echo "POSTGRES_PASSWORD must be set — cannot bootstrap the owner role." >&2
  exit 1
fi
if [ "$APP_PASSWORD" = "$POSTGRES_PASSWORD" ]; then
  echo "Refusing: POSTGRES_APP_PASSWORD equals POSTGRES_PASSWORD (ADR-0064 requires distinct owner/app secrets)." >&2
  exit 1
fi

echo "Creating runtime role '${APP_USER}' (login, DML-only privileges)..."
psql -v ON_ERROR_STOP=1 -U "$OWNER" -d "$DB" \
    -v app_user="$APP_USER" -v app_password="$APP_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user')
\gexec
SQL

grant_app_dml() {
  echo "Granting runtime (DML-only) privileges on '$1' to '${APP_USER}'..."
  psql -v ON_ERROR_STOP=1 -U "$OWNER" -d "$1" -v app_user="$APP_USER" <<'SQL'
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user')
\gexec
GRANT USAGE ON SCHEMA public TO :"app_user";
-- Migrations run as the owner role, so its future objects pick up the
-- runtime grants automatically (no DDL for the app role, ever).
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
SQL
}

grant_app_dml "$DB"

if [ -n "$TEST_DB" ]; then
  echo "Creating test database '$TEST_DB' if missing..."
  psql -v ON_ERROR_STOP=1 -U "$OWNER" -d "$DB" -v test_db="$TEST_DB" <<'SQL'
SELECT format('CREATE DATABASE %I', :'test_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'test_db')
\gexec
SQL
  grant_app_dml "$TEST_DB"
fi

echo "Role bootstrap complete."
