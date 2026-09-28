#!/usr/bin/env bash
# Restore a Health Assistant instance backup produced by scripts/backup.sh.
# Usage: scripts/restore.sh <archive.tar.gz> [--yes]
#        FORCE=1 scripts/restore.sh <archive.tar.gz>
#
# WARNING: replaces the current database contents and the uploads volume.
# Nothing is touched without the explicit --yes flag (or FORCE=1).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCHIVE=""
CONFIRM=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) CONFIRM=1 ;;
    -h|--help) awk 'FNR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; exit 0 ;;
    *) ARCHIVE="$arg" ;;
  esac
done
if [[ -z "$ARCHIVE" ]]; then
  echo "Usage: scripts/restore.sh <archive.tar.gz> [--yes]  (or FORCE=1)" >&2
  exit 1
fi
if [[ ! -f "$ARCHIVE" ]]; then
  echo "No such archive: $ARCHIVE" >&2
  exit 1
fi
if [[ "${FORCE:-0}" == "1" ]]; then
  CONFIRM=1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib-docker.sh"

check_cwd
check_docker

# Load the root .env without clobbering variables already set in the
# environment (explicit env wins over .env; compose gets the same file via
# --env-file, where shell env also wins).
if [[ -f "$ROOT/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ROOT/.env")
fi

DB_NAME="${POSTGRES_DB:-neuro_health}"
DB_OWNER="neuro_health_owner"
DB_PASSWORD="${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD (env or root .env)}"
COMPOSE_FILE="${HA_COMPOSE_FILE:-docker/docker-compose.standalone.yml}"
[[ "$COMPOSE_FILE" = /* ]] || COMPOSE_FILE="$ROOT/$COMPOSE_FILE"
PROJECT="${HA_COMPOSE_PROJECT:-$(resolve_compose_project)}"
COMPOSE=($DOCKER_COMPOSE_CMD --env-file "$ROOT/.env" -f "$COMPOSE_FILE")

echo "==> This restore will DESTROY:"
echo "    - every object in the '$DB_NAME' database (volume ${PROJECT}_postgres_data),"
echo "      replaced from database.dump with pg_restore --clean --if-exists"
echo "    - all files in the '${PROJECT}_uploads' uploads volume (documents,"
echo "      OCR artifacts), replaced from uploads.tar.gz"
echo "    Archive: $ARCHIVE"
if [[ "$CONFIRM" -ne 1 ]]; then
  echo "==> Refusing to proceed without explicit confirmation." >&2
  echo "    Re-run with: scripts/restore.sh $ARCHIVE --yes   (or FORCE=1)" >&2
  exit 2
fi

# Stop the scheduled backup sidecar first — its dump tick can race
# pg_restore and capture a half-empty schema.
"${COMPOSE[@]}" stop backup 2>/dev/null || true

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar xzf "$ARCHIVE" -C "$WORK"

echo "==> Restoring database '$DB_NAME' (drops existing rows)"
"${COMPOSE[@]}" exec -T postgres \
  env PGPASSWORD="$DB_PASSWORD" pg_restore -U "$DB_OWNER" \
  -d "$DB_NAME" --clean --if-exists --no-owner < "$WORK/database.dump"

# Re-apply the runtime-role grants. --no-owner makes every restored object
# belong to neuro_health_owner, and dumps taken before the two-role split
# (single `admin` user) carry no grants for neuro_health_app — without this
# step the backend would lose DML on the restored tables.
echo "==> Re-applying runtime-role grants for neuro_health_app"
"${COMPOSE[@]}" exec -T postgres \
  env PGPASSWORD="$DB_PASSWORD" psql -U "$DB_OWNER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<'SQL'
GRANT USAGE ON SCHEMA public TO neuro_health_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO neuro_health_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO neuro_health_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO neuro_health_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO neuro_health_app;
SQL

echo "==> Restoring uploads volume"
docker run --rm -v "${PROJECT}_uploads":/data -v "$WORK":/in:ro alpine \
  sh -c "rm -rf /data/* && tar xzf /in/uploads.tar.gz -C /data"

echo "==> Done. Restart the app to re-run migrations if needed:"
echo "    ${COMPOSE[*]} up -d"
