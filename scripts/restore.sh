#!/usr/bin/env bash
# Health Assistant — throwaway-target restore (plan 65, ADR-0063 §4).
#
# This script NEVER writes into the live database. It restores an archive
# produced by scripts/backup.sh into a NEW (or explicitly disposable)
# empty target database, Timescale-aware (pre/post restore hooks), verifies
# it, and leaves the switchover decision to the operator. The live database
# and the uploads volume are read from at most, never written.
#
# Usage:
#   scripts/restore.sh <archive.tar.gz> --target <new-db-name> [options]
#
# Required:
#   --target NAME        Throwaway database to restore into. Refused when it
#                        is the live database (POSTGRES_DB / HA_DATABASE_URL),
#                        postgres, template0/template1 or the test database.
# Options:
#   --drop-existing      The target exists → DROP and recreate it. This is the
#                        explicit disposable-target confirmation (a target left
#                        by a previous restore run, never the live DB).
#   --yes                Non-interactive (CI). Without it the script asks you
#                        to type the target name as the disposable confirmation.
#   --probe-tenant UUID  Tenant used for the known-record RLS probe
#                        (default: first tenant by created_at).
#   --refresh-caggs      Force a full refresh_continuous_aggregate of the
#                        ladder right after restore (drill-time rebuild).
#   --skip-erasure-replay  Do not replay the erasure ledger into the target.
#   --skip-app-tier      Skip the backend-container checks (erasure replay,
#                        audit hash walk, readiness probe); SQL checks still run.
#   --pg-container NAME  Client mode "container": talk to a scratch/standalone
#                        Timescale container directly (docker run + psql) instead
#                        of the compose stack. Used by scripts/test-restore-scratch.sh
#                        and for restores into a second, empty Timescale instance.
#   --pg-network NET     Docker network of that container (required with
#                        --pg-container; the timescale image provides psql).
#   -h, --help           This help.
#
# Exit codes: 0 ok · 1 usage · 2 refused/safety stop · 3 restore failed ·
#             4 post-restore verification failed (target kept for forensics).
#
# Procedure (plan 65 / implementations/cloud-deployment-t1 restore design):
#   1. extract the archive (manifest.json + database.dump + uploads.tar.gz)
#   2. ensure both roles exist (owner + app, ADR-0064)
#   3. ensure the target database is a fresh, empty database
#   4. CREATE EXTENSION IF NOT EXISTS timescaledb (+ pgcrypto, pg_trgm)
#   5. SELECT timescaledb_pre_restore();
#   6. pg_restore -Fc --no-owner  (single-threaded — Timescale does not
#      support -j; ACLs are kept so the app-role grants ride the dump)
#   7. SELECT timescaledb_post_restore();
#   8. ANALYZE; reconcile CAGG refresh + retention + compression policies
#      (CAGG *contents* are rebuilt by the policies, not re-derived)
#   9. re-grant the app role + default privileges
#  10. replay the erasure ledger into the target (backend one-shot)
#  11. verify: row counts vs the live DB, RLS fail-closed + one known record
#      under a tenant context, trend query, pg_policies, CAGG policies,
#      audit-chain integrity, app readiness probe + storage status
#
# The uploads volume / object storage is NOT touched. Objects are versioned
# (or re-pointed at cutover) — see docker/README.md → "Backup & restore".
#
# Rollback: the live database is never modified. To abandon a restore, do
# nothing (drop the throwaway target manually when convenient). To roll back
# after a switchover, point .env back at the previous database and restart.
# Never delete the previous database before the retention window passes.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib-docker.sh"

TIMESCALE_IMAGE="${TIMESCALE_IMAGE:-timescale/timescaledb:latest-pg16}" # pinned like docker-compose.standalone.yml
ARCHIVE=""
TARGET=""
DROP_EXISTING=0
CONFIRM=0
REFRESH_CAGGS=0
SKIP_ERASURE=0
SKIP_APP_TIER=0
PROBE_TENANT=""
PG_MODE="compose"
PG_CONTAINER=""
PG_NETWORK=""

usage() {
  awk 'FNR==1 { next } /^# Usage:/ { p=1 } p && /^#/ { sub(/^# ?/, ""); print; next } p { exit }' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="${2:?--target requires a database name}"; shift 2 ;;
    --drop-existing) DROP_EXISTING=1; shift ;;
    --yes|-y) CONFIRM=1; shift ;;
    --probe-tenant) PROBE_TENANT="${2:?--probe-tenant requires a tenant uuid}"; shift 2 ;;
    --refresh-caggs) REFRESH_CAGGS=1; shift ;;
    --skip-erasure-replay) SKIP_ERASURE=1; shift ;;
    --skip-app-tier) SKIP_APP_TIER=1; SKIP_ERASURE=1; shift ;;
    --pg-container) PG_MODE="container"; PG_CONTAINER="${2:?--pg-container requires a container name}"; shift 2 ;;
    --pg-network) PG_NETWORK="${2:?--pg-network requires a network name}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    *) [[ -z "$ARCHIVE" ]] || { echo "Unexpected argument: $1" >&2; exit 1; }; ARCHIVE="$1"; shift ;;
  esac
done

# ── Usage-level validation (no docker needed) ───────────────────────────────
if [[ -z "$ARCHIVE" || -z "$TARGET" ]]; then
  echo "Usage: scripts/restore.sh <archive.tar.gz> --target <new-db-name> [--yes] [options]" >&2
  [[ -n "$ARCHIVE" ]] || echo "  missing: <archive.tar.gz>" >&2
  [[ -n "$TARGET" ]] || echo "  missing: --target <new-db-name> (a THROWAWAY database — the live DB is never a target)" >&2
  exit 1
fi
if [[ ! -f "$ARCHIVE" ]]; then
  echo "No such archive: $ARCHIVE" >&2
  exit 2
fi
if [[ ! "$TARGET" =~ ^[a-z_][a-z0-9_\$]{0,62}$ ]]; then
  echo "Invalid target database name: '$TARGET' (lowercase identifier expected)" >&2
  exit 1
fi

# Load the root .env without clobbering variables already set in the
# environment (explicit env wins over .env).
if [[ -f "$ROOT/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ROOT/.env")
fi

DB_NAME="${POSTGRES_DB:-neuronection_health}"
DB_OWNER="${POSTGRES_USER:-neuronection_health_owner}"
DB_APP="${POSTGRES_APP_USER:-neuronection_health_app}"
DB_PASSWORD="${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD (env or root .env)}"
APP_PASSWORD="${HA_APP_DB_PASSWORD:-${POSTGRES_PASSWORD:-}}"
TEST_DB="${POSTGRES_TEST_DB:-}"

# Identifiers below are interpolated into SQL (SET ROLE, probes) — refuse
# anything that is not a plain lowercase identifier before anything runs.
for ident in "$DB_NAME" "$DB_OWNER" "$DB_APP" "$TARGET"; do
  if [[ ! "$ident" =~ ^[a-z_][a-z0-9_\$]{0,62}$ ]]; then
    echo "refusing: '$ident' is not a valid Postgres identifier (POSTGRES_DB/POSTGRES_USER/POSTGRES_APP_USER/--target)." >&2
    exit 1
  fi
done

# ── Live-DB refusal (ADR-0063 §7: restoring into the live DB is a violation) ─
LIVE_NAMES=("$DB_NAME")
if [[ -n "${HA_DATABASE_URL:-}" ]]; then
  LIVE_DB_FROM_URL="${HA_DATABASE_URL%%\?*}"        # strip query string
  LIVE_DB_FROM_URL="${LIVE_DB_FROM_URL##*/}"        # path segment = db name
  [[ -n "$LIVE_DB_FROM_URL" ]] && LIVE_NAMES+=("$LIVE_DB_FROM_URL")
fi
for refused in postgres template0 template1; do
  [[ "$TARGET" == "$refused" ]] && { echo "refusing: '$TARGET' is a system database, not a restore target." >&2; exit 2; }
done
for live in "${LIVE_NAMES[@]}"; do
  if [[ "$TARGET" == "$live" ]]; then
    echo "refusing: '$TARGET' is the LIVE database of this instance." >&2
    echo "  Restore into a throwaway target: scripts/restore.sh $ARCHIVE --target ${DB_NAME}_restored --yes" >&2
    echo "  Switchover happens only after the target verifies (docker/README.md → Backup & restore)." >&2
    exit 2
  fi
done
if [[ -n "$TEST_DB" && "$TARGET" == "$TEST_DB" ]]; then
  echo "refusing: '$TARGET' is the test database (POSTGRES_TEST_DB)." >&2
  exit 2
fi

if [[ "$PG_MODE" == "container" ]]; then
  [[ -n "$PG_NETWORK" ]] || { echo "--pg-container requires --pg-network" >&2; exit 1; }
fi

# ── Confirmation (disposable-target consent) ────────────────────────────────
if [[ "$CONFIRM" -ne 1 ]]; then
  if [[ -t 0 ]]; then
    echo "This restore writes ONLY into the throwaway target '$TARGET'."
    echo "  live database : '$DB_NAME' (untouched)"
    echo "  uploads/objects: untouched"
    echo "  archive       : $ARCHIVE"
    if [[ "$DROP_EXISTING" -eq 1 ]]; then
      echo "  EXISTING TARGET '$TARGET' WILL BE DROPPED AND RECREATED (disposable)."
    fi
    read -r -p "Type the target database name to confirm: " REPLY
    [[ "$REPLY" == "$TARGET" ]] || { echo "Aborted — nothing was changed." >&2; exit 2; }
  else
    echo "Refusing to proceed without --yes (no interactive terminal)." >&2
    exit 2
  fi
fi

# ── Docker / compose plumbing ───────────────────────────────────────────────
if [[ "$PG_MODE" == "compose" ]]; then
  check_cwd
  check_docker
  COMPOSE_FILE="${HA_COMPOSE_FILE:-docker/docker-compose.standalone.yml}"
  [[ "$COMPOSE_FILE" = /* ]] || COMPOSE_FILE="$ROOT/$COMPOSE_FILE"
  read -r -a COMPOSE_BIN <<< "$DOCKER_COMPOSE_CMD" # "docker compose" → two words
  COMPOSE=("${COMPOSE_BIN[@]}" --env-file "$ROOT/.env" -f "$COMPOSE_FILE")

  compose_has_service() {
    "${COMPOSE[@]}" config --services 2>/dev/null | grep -qx "$1"
  }
  # One-shot in the backend image with the DB pointed at the target.
  # backend_run <target-db-url> <command and args...> — runs as the app env
  # with HA_DATABASE_URL overridden; never touches the live DB.
  backend_run() {
    local url="$1"; shift
    "${COMPOSE[@]}" run --rm --no-deps -T -e "HA_DATABASE_URL=$url" backend "$@"
  }
  pg_client() { # pg_client <dbname> [psql args...]; SQL arrives on stdin
    local db="$1"; shift
    "${COMPOSE[@]}" exec -T postgres env PGPASSWORD="$DB_PASSWORD" \
      psql -U "$DB_OWNER" -d "$db" "$@"
  }
  pg_app_client() { # the app-role connection — the way the backend reads
    local db="$1"; shift
    "${COMPOSE[@]}" exec -T postgres env PGPASSWORD="$APP_PASSWORD" \
      psql -U "$DB_APP" -d "$db" "$@"
  }
  pg_restore_client() { # pg_restore_client <dbname> [pg_restore args...]; dump on stdin
    local db="$1"; shift
    "${COMPOSE[@]}" exec -T postgres env PGPASSWORD="$DB_PASSWORD" \
      pg_restore -U "$DB_OWNER" -d "$db" "$@"
  }
  pg_restore_list_client() { # dump on stdin → table-of-contents
    "${COMPOSE[@]}" exec -T postgres env PGPASSWORD="$DB_PASSWORD" pg_restore --list
  }
  pg_isready_client() {
    "${COMPOSE[@]}" exec -T postgres env PGPASSWORD="$DB_PASSWORD" \
      pg_isready -U "$DB_OWNER" -d "$1" >/dev/null 2>&1
  }
else
  check_docker
  docker inspect "$PG_CONTAINER" >/dev/null 2>&1 \
    || { echo "Container not found: $PG_CONTAINER" >&2; exit 2; }
  docker network inspect "$PG_NETWORK" >/dev/null 2>&1 \
    || { echo "Network not found: $PG_NETWORK" >&2; exit 2; }
  compose_has_service() { return 1; }
  backend_run() { echo "backend one-shots are not available in container mode (skipped): $*" >&2; return 1; }
  pg_client() {
    local db="$1"; shift
    docker run --rm -i --network "$PG_NETWORK" -e PGPASSWORD="$DB_PASSWORD" \
      "$TIMESCALE_IMAGE" psql -h "$PG_CONTAINER" -U "$DB_OWNER" -d "$db" "$@"
  }
  pg_app_client() {
    local db="$1"; shift
    docker run --rm -i --network "$PG_NETWORK" -e PGPASSWORD="$APP_PASSWORD" \
      "$TIMESCALE_IMAGE" psql -h "$PG_CONTAINER" -U "$DB_APP" -d "$db" "$@"
  }
  pg_restore_client() {
    local db="$1"; shift
    docker run --rm -i --network "$PG_NETWORK" -e PGPASSWORD="$DB_PASSWORD" \
      "$TIMESCALE_IMAGE" pg_restore -h "$PG_CONTAINER" -U "$DB_OWNER" -d "$db" "$@"
  }
  pg_restore_list_client() {
    docker run --rm -i --network "$PG_NETWORK" -e PGPASSWORD="$DB_PASSWORD" \
      "$TIMESCALE_IMAGE" pg_restore --list
  }
  pg_isready_client() {
    docker run --rm --network "$PG_NETWORK" -e PGPASSWORD="$DB_PASSWORD" \
      "$TIMESCALE_IMAGE" pg_isready -h "$PG_CONTAINER" -U "$DB_OWNER" -d "$1" >/dev/null 2>&1
  }
fi

TS() { date +%s; }
T_START="$(TS)"

psql_scalar() { # psql_scalar <db> <sql> → single value on stdout (owner role).
  # Failures come back as empty output; callers guard with ${var:-ERR}.
  pg_client "$1" -v ON_ERROR_STOP=1 -tAc "$2" 2>/dev/null || return 0
}
app_scalar() { # app_scalar <db> <sql> → app-role connection (RLS enforced)
  pg_app_client "$1" -v ON_ERROR_STOP=1 -tAc "$2" 2>/dev/null || return 0
}

db_exists() {
  [[ "$(psql_scalar postgres "SELECT 1 FROM pg_database WHERE datname='$TARGET'")" == "1" ]]
}

target_is_empty() {
  local tables views
  tables="$(psql_scalar "$TARGET" "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema','_timescaledb_internal','timescaledb','timescaledb_information','timescaledb_experimental')")"
  views="$(psql_scalar "$TARGET" "SELECT count(*) FROM pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema','timescaledb','timescaledb_information','timescaledb_experimental')")"
  [[ "${tables:-1}" == "0" && "${views:-1}" == "0" ]]
}

# ── Reachability ────────────────────────────────────────────────────────────
echo "==> Waiting for Postgres to accept connections (mode: $PG_MODE)"
for _ in $(seq 1 30); do
  pg_isready_client postgres && break
  sleep 2
done
pg_isready_client postgres || {
  if [[ "$PG_MODE" == "compose" ]]; then
    echo "refusing: postgres is not reachable. Start it first:" >&2
    echo "  ${COMPOSE[*]} up -d postgres" >&2
  else
    echo "refusing: postgres in container '$PG_CONTAINER' is not reachable." >&2
  fi
  exit 2
}

# ── Stop the scheduled backup sidecar — its dump tick can race pg_restore ───
if [[ "$PG_MODE" == "compose" ]] && compose_has_service backup; then
  echo "==> Stopping the backup sidecar (its dump tick could race pg_restore)"
  "${COMPOSE[@]}" stop backup 2>/dev/null || true
fi

# ── Extract the archive ─────────────────────────────────────────────────────
WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

echo "==> Extracting $ARCHIVE"
tar xzf "$ARCHIVE" -C "$WORK"
[[ -f "$WORK/database.dump" ]] || { echo "refusing: archive has no database.dump" >&2; exit 2; }
if [[ -f "$WORK/manifest.json" ]]; then
  echo "==> Manifest:"
  sed 's/^/    /' "$WORK/manifest.json"
fi
if [[ -f "$WORK/uploads.tar.gz" ]]; then
  echo "==> Archive carries uploads.tar.gz — object/volume restore is a separate, documented step."
  echo "    This script does not touch the live uploads volume or the object store."
fi
if ! pg_restore_list_client < "$WORK/database.dump" >/dev/null 2>&1; then
  echo "refusing: database.dump does not parse (not a pg_dump -Fc archive?)" >&2
  exit 2
fi
echo "==> Dump parses (pg_restore --list ok)"

# ── Roles (ADR-0064: owner + app, before restore; dump ACLs name the app) ──
echo "==> Ensuring roles '$DB_OWNER' (owner) and '$DB_APP' (app) exist"
pg_client postgres -v ON_ERROR_STOP=1 \
  -v owner="$DB_OWNER" -v app="$DB_APP" \
  -v owner_pw="$DB_PASSWORD" -v app_pw="$APP_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'owner', :'owner_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'owner')\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app', :'app_pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app')\gexec
SQL

# ── Fresh, empty target ─────────────────────────────────────────────────────
if db_exists; then
  if target_is_empty; then
    echo "==> Target '$TARGET' exists and is empty — restoring into it"
  elif [[ "$DROP_EXISTING" -eq 1 ]]; then
    echo "==> Dropping existing (disposable) target '$TARGET'"
    pg_client postgres -v ON_ERROR_STOP=1 -v t="$TARGET" <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname = :'t' AND pid <> pg_backend_pid();
SELECT format('DROP DATABASE %I', :'t')\gexec
SQL
    pg_client postgres -v ON_ERROR_STOP=1 -v t="$TARGET" -v owner="$DB_OWNER" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'t', :'owner')\gexec
SQL
  else
    echo "refusing: target '$TARGET' already exists and is NOT empty." >&2
    echo "  Pick another --target, or pass --drop-existing to discard it (throwaway targets only)." >&2
    exit 2
  fi
else
  echo "==> Creating empty target '$TARGET' (owner: $DB_OWNER)"
  pg_client postgres -v ON_ERROR_STOP=1 -v t="$TARGET" -v owner="$DB_OWNER" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'t', :'owner')\gexec
SQL
fi

# ── Extensions as provider admin/owner (before pre_restore!) ───────────────
echo "==> Creating extensions (timescaledb, pgcrypto, pg_trgm)"
pg_client "$TARGET" -v ON_ERROR_STOP=1 <<'SQL'
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL

# ── Row counts on the live DB (read-only; used for count verification) ─────
declare -A SOURCE_COUNTS=()
if [[ "$PG_MODE" == "compose" ]] && psql_scalar "$DB_NAME" "SELECT 1" >/dev/null 2>&1; then
  COUNT_SQL="SELECT 'tenants', count(*) FROM tenants
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'fhir_patients', count(*) FROM fhir_patients
UNION ALL SELECT 'documents', count(*) FROM documents
UNION ALL SELECT 'telemetry_data', count(*) FROM telemetry_data
UNION ALL SELECT 'audit_events', count(*) FROM audit_events"
  while IFS='|' read -r k v; do SOURCE_COUNTS["$k"]="$v"; done \
    < <(psql_scalar "$DB_NAME" "$COUNT_SQL")
fi

# ── Timescale-aware restore ─────────────────────────────────────────────────
echo "==> timescaledb_pre_restore()"
pg_client "$TARGET" -v ON_ERROR_STOP=1 -c "SELECT timescaledb_pre_restore();"

T_RESTORE="$(TS)"
echo "==> pg_restore -Fc --no-owner (single-threaded — Timescale does not support parallel restores)"
pg_restore_client "$TARGET" --no-owner --exit-on-error < "$WORK/database.dump"
echo "    pg_restore took $(( $(TS) - T_RESTORE ))s"

echo "==> timescaledb_post_restore()"
pg_client "$TARGET" -v ON_ERROR_STOP=1 -c "SELECT timescaledb_post_restore();"

echo "==> ANALYZE"
pg_client "$TARGET" -v ON_ERROR_STOP=1 -c "ANALYZE;" >/dev/null

# ── CAGG ladder + retention/compression policy reconciliation ───────────────
# The dump usually carries the policies, but a dump taken while a policy was
# missing (or a managed target that stripped job rows) must not survive: the
# ladder below re-asserts the frozen 0005_telemetry_unified decision with
# if_not_exists => true. CAGG *contents* are rebuilt by these policies.
if [[ "$(psql_scalar "$TARGET" "SELECT 1 FROM pg_available_extensions WHERE name='timescaledb'")" == "1" ]]; then
  echo "==> Reconciling Timescale policies (CAGG refresh, retention, compression)"
  pg_client "$TARGET" -v ON_ERROR_STOP=1 <<'SQL'
DO $$ BEGIN
  IF to_regclass('public.telemetry_data') IS NOT NULL THEN
    PERFORM add_compression_policy('telemetry_data', INTERVAL '7 days', if_not_exists => true);
    PERFORM add_retention_policy('telemetry_data', INTERVAL '2 years', if_not_exists => true);
  END IF;
END $$;
DO $$ BEGIN
  IF to_regclass('public.telemetry_hourly') IS NOT NULL THEN
    PERFORM add_continuous_aggregate_policy('telemetry_hourly',
      start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
      schedule_interval => INTERVAL '1 hour', if_not_exists => true);
  END IF;
  IF to_regclass('public.telemetry_daily') IS NOT NULL THEN
    PERFORM add_continuous_aggregate_policy('telemetry_daily',
      start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 day',
      schedule_interval => INTERVAL '1 day', if_not_exists => true);
  END IF;
  IF to_regclass('public.telemetry_monthly') IS NOT NULL THEN
    PERFORM add_continuous_aggregate_policy('telemetry_monthly',
      start_offset => INTERVAL '12 months', end_offset => INTERVAL '1 month',
      schedule_interval => INTERVAL '1 day', if_not_exists => true);
  END IF;
END $$;
SQL
  if [[ "$REFRESH_CAGGS" -eq 1 ]]; then
    echo "==> --refresh-caggs: full refresh of the CAGG ladder"
    for view in telemetry_hourly telemetry_daily telemetry_monthly; do
      if [[ -n "$(psql_scalar "$TARGET" "SELECT to_regclass('public.$view')::text")" ]]; then
        # refresh_continuous_aggregate is a PROCEDURE on Timescale 2.x — CALL.
        pg_client "$TARGET" -v ON_ERROR_STOP=1 -c "CALL refresh_continuous_aggregate('$view', NULL, NULL);" >/dev/null
      fi
    done
  fi
  pg_client "$TARGET" -tAc \
    "SELECT job_id, application_name, schedule_interval FROM timescaledb_information.jobs ORDER BY job_id" \
    | sed 's/^/    job /'
fi

# ── Re-grant the app role + default privileges (ADR-0064) ───────────────────
# Idempotent; also heals pre-role-split (admin-era) dumps whose ACLs name no
# app role, and re-asserts the scoped-view surface from 0016_rls. (\gexec, not
# DO blocks: psql does not interpolate :vars inside dollar-quoted strings.)
echo "==> Re-granting '$DB_APP' (DML + default privileges + scoped views)"
pg_client "$TARGET" -v ON_ERROR_STOP=1 -v app="$DB_APP" -v db="$TARGET" <<'SQL'
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'db', :'app')\gexec
GRANT USAGE ON SCHEMA public TO :"app";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app";
-- The runtime role never touches the version row or the raw CAGGs — the
-- GUC-filtered *_scoped views are the only aggregate surface it gets.
SELECT format('REVOKE ALL ON alembic_version FROM %I', :'app')
WHERE to_regclass('public.alembic_version') IS NOT NULL\gexec
SELECT format('REVOKE ALL ON %I FROM %I', v.raw, :'app')
FROM (VALUES ('telemetry_hourly'),('telemetry_daily'),('telemetry_monthly')) AS v(raw)
WHERE to_regclass('public.' || v.raw) IS NOT NULL
  AND to_regclass('public.' || v.raw || '_scoped') IS NOT NULL\gexec
SELECT format('GRANT SELECT ON %I TO %I', v.raw || '_scoped', :'app')
FROM (VALUES ('telemetry_hourly'),('telemetry_daily'),('telemetry_monthly')) AS v(raw)
WHERE to_regclass('public.' || v.raw || '_scoped') IS NOT NULL\gexec
SELECT format('GRANT SELECT ON telemetry_live_scoped TO %I', :'app')
WHERE to_regclass('public.telemetry_live_scoped') IS NOT NULL\gexec
SQL

# ── Erasure-ledger replay into the target (before it can serve traffic) ─────
# A backup taken before an erasure must not resurrect the subject. Replayed
# against the throwaway target so even a cutover cannot serve erased data.
OWNER_TARGET_URL="postgresql+asyncpg://$DB_OWNER:$DB_PASSWORD@postgres:5432/$TARGET"
if [[ "$PG_MODE" == "compose" && "$SKIP_ERASURE" -ne 1 ]] && compose_has_service backend; then
  echo "==> Replaying the erasure ledger into '$TARGET' (backend one-shot, pointed at the target)"
  backend_run "$OWNER_TARGET_URL" python scripts/replay_erasure_ledger.py \
    || { echo "restore failed: erasure-ledger replay errored (target kept for forensics)" >&2; exit 3; }
else
  echo "==> Erasure-ledger replay: skipped ($PG_MODE mode or --skip)."
  echo "    Run BEFORE serving traffic after cutover:"
  echo "    docker compose run --rm backend python scripts/replay_erasure_ledger.py"
fi

# ── Verification ────────────────────────────────────────────────────────────
echo "==> Verification: row counts"
VERIFY_FAIL=0
verify_assert() { # verify_assert <label> <expected> <actual>
  if [[ "$2" == "$3" ]]; then
    echo "    PASS: $1 ($3)"
  else
    echo "    FAIL: $1 (expected '$2', got '$3')"
    VERIFY_FAIL=1
  fi
}

COUNT_SQL="SELECT 'tenants', count(*) FROM tenants
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'fhir_patients', count(*) FROM fhir_patients
UNION ALL SELECT 'documents', count(*) FROM documents
UNION ALL SELECT 'telemetry_data', count(*) FROM telemetry_data
UNION ALL SELECT 'audit_events', count(*) FROM audit_events"
declare -A TARGET_COUNTS=()
while IFS='|' read -r k v; do TARGET_COUNTS["$k"]="$v"; done \
  < <(psql_scalar "$TARGET" "$COUNT_SQL")

for table in tenants users fhir_patients documents telemetry_data audit_events; do
  line="    ${table}: ${TARGET_COUNTS[$table]:-MISSING}"
  if [[ -z "${TARGET_COUNTS[$table]:-}" ]]; then
    echo "$line"
    VERIFY_FAIL=1
    continue
  fi
  if [[ -n "${SOURCE_COUNTS[$table]:-}" ]]; then
    if [[ "${TARGET_COUNTS[$table]}" == "${SOURCE_COUNTS[$table]}" ]]; then
      line+=" (== live)"
    else
      # The live DB legitimately drifts between backup and restore (ingest,
      # retention, erasure) — a mismatch here is a WARN, not a failure. The
      # authoritative comparison is against the backup itself (drill/ci).
      line+=" (live now: ${SOURCE_COUNTS[$table]} — drift since the backup is expected)"
    fi
  fi
  echo "$line"
done
if [[ -n "${SOURCE_COUNTS[tenants]:-}" ]]; then
  echo "    (counts compared against the live DB — read-only; drift = warn)"
fi

echo "==> Verification: RLS fail-closed + one known record under tenant context"
# Fail-closed: connecting AS the app role (the way the backend connects) with
# no planted context must see zero rows.
for probe_table in audit_events documents fhir_patients; do
  no_ctx="$(app_scalar "$TARGET" "SELECT count(*) FROM $probe_table")"
  verify_assert "no-context read of $probe_table is 0 rows (RLS fail-closed)" "0" "${no_ctx:-ERR}"
done

# Tenant context probe: the app role must see the tenant's rows only with the
# GUC the auth layer plants — one known record, read the way the app reads.
UUID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
if [[ -z "$PROBE_TENANT" ]]; then
  PROBE_TENANT="$(psql_scalar "$TARGET" "SELECT id::text FROM tenants ORDER BY created_at LIMIT 1" || true)"
fi
if [[ -n "$PROBE_TENANT" && "$PROBE_TENANT" =~ $UUID_RE ]]; then
  known_patient="$(psql_scalar "$TARGET" \
    "SELECT id::text FROM fhir_patients WHERE tenant_id='$PROBE_TENANT' ORDER BY created_at DESC LIMIT 1" \
    || true)"
  if [[ -n "$known_patient" && "$known_patient" =~ $UUID_RE ]]; then
    seen="$(app_scalar "$TARGET" \
      "SELECT set_config('app.tenant_id', '$PROBE_TENANT', false); SELECT count(*) FROM fhir_patients WHERE id='$known_patient'" \
      | tail -1)"
    verify_assert "known record visible under tenant context" "1" "${seen:-ERR}"
    echo "    known record: fhir_patients.id=$known_patient (tenant $PROBE_TENANT)"
  else
    echo "    SKIP: probe tenant has no patients"
  fi
  trend="$(app_scalar "$TARGET" \
    "SELECT set_config('app.tenant_id', '$PROBE_TENANT', false); SELECT count(*) FROM telemetry_hourly_scoped" \
    | tail -1)"
  if [[ "${TARGET_COUNTS[telemetry_data]:-0}" -gt 0 ]]; then
    verify_assert "trend query (telemetry_hourly_scoped) returns data" "NONZERO" \
      "$([[ "${trend:-0}" -gt 0 ]] && echo NONZERO || echo ZERO)"
  else
    echo "    SKIP: trend query — telemetry_data is empty (${trend:-0} CAGG rows)"
  fi
else
  echo "    SKIP: no tenant rows to probe — tenant-context probes skipped (counts above still compared)"
fi

echo "==> Verification: RLS policies + Timescale jobs"
policy_count="$(psql_scalar "$TARGET" "SELECT count(*) FROM pg_policies")"
verify_assert "pg_policies present" "NONZERO" \
  "$([[ "${policy_count:-0}" -gt 0 ]] && echo NONZERO || echo ZERO)"
echo "    pg_policies: ${policy_count:-0}"
if [[ "$(psql_scalar "$TARGET" "SELECT 1 FROM pg_available_extensions WHERE name='timescaledb'")" == "1" ]]; then
  # proc_name, not job_type: older information-view schemas (e.g. the pinned
  # timescale/timescaledb image) do not carry job_type.
  jobs="$(psql_scalar "$TARGET" \
    "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name IN ('policy_refresh_continuous_aggregate','policy_retention','policy_compression')")"
  verify_assert "CAGG refresh + retention + compression policies scheduled" "NONZERO" \
    "$([[ "${jobs:-0}" -ge 5 ]] && echo NONZERO || echo ZERO)"
  echo "    timescaledb jobs: ${jobs:-0} (ladder expects 3 CAGG + retention + compression)"
fi

echo "==> Verification: audit-chain integrity (SQL tier: contiguity + heads)"
broken_chains="$(psql_scalar "$TARGET" \
  "SELECT (SELECT count(*) FROM (
      SELECT c.chain_key FROM (
        SELECT chain_key, count(*) AS n, count(DISTINCT seq) AS d, max(seq) AS m
        FROM audit_events GROUP BY chain_key
      ) c LEFT JOIN audit_chain_heads h USING (chain_key)
      WHERE h.chain_key IS NULL OR h.seq <> c.m OR c.n <> c.d
    ) b)
   + (SELECT count(*) FROM audit_chain_heads h
      WHERE NOT EXISTS (SELECT 1 FROM audit_events e WHERE e.chain_key = h.chain_key))")"
verify_assert "no broken audit chains (gaps/duplicates/orphan heads)" "0" "${broken_chains:-ERR}"
chain_total="$(psql_scalar "$TARGET" "SELECT count(DISTINCT chain_key) FROM audit_events")"
echo "    audit chains: ${chain_total:-0} (hash recomputation runs in the app tier)"

# App tier: full hash walk + readiness probe, in the backend image, pointed at
# the target — never at the live DB.
if [[ "$PG_MODE" == "compose" && "$SKIP_APP_TIER" -ne 1 ]] && compose_has_service backend; then
  echo "==> Verification: audit-chain hash walk (backend one-shot, owner URL → target)"
  if backend_run "$OWNER_TARGET_URL" python -c "
import asyncio, json, sys
from app.core.database import AsyncSessionLocal, engine
from app.services.audit.audit_chain import verify_all_chains

async def main():
    async with AsyncSessionLocal() as db:
        results = await verify_all_chains(db)
    await engine.dispose()
    bad = [r for r in results if not r.get('ok')]
    print(json.dumps({'chains': len(results), 'failed': bad}, default=str))
    sys.exit(1 if bad else 0)

asyncio.run(main())
"; then
    echo "    PASS: audit-chain hash walk"
  else
    echo "    FAIL: audit-chain hash walk"
    VERIFY_FAIL=1
  fi

  echo "==> Verification: app readiness probe against the target (/ready)"
  PROBE_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
  PROBE_NAME="health-restore-probe-$$"
  if "${COMPOSE[@]}" run -d --rm --no-deps --name "$PROBE_NAME" \
      -e "HA_DATABASE_URL=$OWNER_TARGET_URL" \
      -p "127.0.0.1:${PROBE_PORT}:8000" backend >/dev/null 2>&1; then
    health_json=""
    for _ in $(seq 1 30); do
      if health_json="$(python3 -c "
import sys, urllib.request
try:
    print(urllib.request.urlopen('http://127.0.0.1:${PROBE_PORT}/ready', timeout=4).read().decode())
except Exception:
    sys.exit(1)
" 2>/dev/null)"; then
        break
      fi
      sleep 2
    done
    docker stop -t 2 "$PROBE_NAME" >/dev/null 2>&1 || true
    if [[ -n "$health_json" ]]; then
      probe_status="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('status','?'))" "$health_json" 2>/dev/null || echo '?')"
      probe_db="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('database','?'))" "$health_json" 2>/dev/null || echo '?')"
      probe_storage="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('storage',{}).get('status','?'))" "$health_json" 2>/dev/null || echo '?')"
      verify_assert "readiness reports ready" "ready" "$probe_status"
      verify_assert "backend boots against the target (database)" "connected" "$probe_db"
      echo "    storage probe: $probe_storage (informational; provider probe is authoritative on cloud)"
    else
      echo "    FAIL: backend probe did not answer on :${PROBE_PORT}"
      VERIFY_FAIL=1
    fi
  else
    echo "    FAIL: could not start the backend probe container"
    VERIFY_FAIL=1
  fi
fi

T_END="$(TS)"
if [[ "$VERIFY_FAIL" -ne 0 ]]; then
  echo "" >&2
  echo "VERIFICATION FAILED — target '$TARGET' is kept for forensics; nothing else was touched." >&2
  echo "The live database '$DB_NAME' is UNTOUCHED — no rollback needed. Do not cut over." >&2
  echo "Investigate, drop '$TARGET' manually, and re-run with --drop-existing." >&2
  exit 4
fi

echo ""
echo "==> Restore verified into throwaway target '$TARGET' (total $(( T_END - T_START ))s — RTO actual)"
echo ""
echo "Cutover (only after you reviewed the verification above):"
if [[ "$PG_MODE" == "compose" ]]; then
  echo "  1. ${COMPOSE[*]} stop backend worker beat   # stop writers"
  echo "  2. .env: set POSTGRES_DB=$TARGET (and HA_DATABASE_URL/HA_MIGRATIONS_DATABASE_URL if you set them explicitly)"
  echo "  3. ${COMPOSE[*]} up -d   # migrate gate re-runs alembic against the target"
  echo "  4. Log in and spot-check data; the erasure ledger was already replayed above."
else
  echo "  Re-point the app at '$PG_CONTAINER'/'$TARGET' (HA_DATABASE_URL + HA_MIGRATIONS_DATABASE_URL) and restart."
  echo "  Then run the erasure-ledger replay before serving traffic."
fi
echo ""
echo "Rollback:"
echo "  - Before cutover: do nothing — the live DB was never touched. Remove '$TARGET' when convenient."
echo "  - After cutover: restore the previous POSTGRES_DB in .env and restart. The previous database"
echo "    is left in place — never delete it before the retention window passes."
echo "  - Keep the archive ($(basename "$ARCHIVE")) until the new target has survived its first backup cycle."
