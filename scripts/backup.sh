#!/usr/bin/env bash
# Health Assistant instance backup (Docker deployment): Postgres dump +
# uploads volume (documents, OCR artifacts).
# Usage: scripts/backup.sh [output-dir]
# Reads the root .env (or environment) for POSTGRES_* settings. Output:
#   <output-dir>/health-assistant-YYYYMMDD-HHMMSS.tar.gz
#     manifest.json   — what this archive contains
#     database.dump   — pg_dump custom format (neuronection_health)
#     uploads.tar.gz  — the `uploads` volume (UPLOAD_DIR=/app/uploads)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$ROOT/backups}"
mkdir -p "$OUT_DIR"

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

DB_NAME="${POSTGRES_DB:-neuronection_health}"
DB_OWNER="neuronection_health_owner"
DB_PASSWORD="${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD (env or root .env)}"
COMPOSE_FILE="${HA_COMPOSE_FILE:-docker/docker-compose.standalone.yml}"
[[ "$COMPOSE_FILE" = /* ]] || COMPOSE_FILE="$ROOT/$COMPOSE_FILE"
PROJECT="${HA_COMPOSE_PROJECT:-$(resolve_compose_project)}"
COMPOSE=($DOCKER_COMPOSE_CMD --env-file "$ROOT/.env" -f "$COMPOSE_FILE")

STAMP="$(date -u +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> Dumping database '$DB_NAME' (as $DB_OWNER)"
"${COMPOSE[@]}" exec -T postgres \
  env PGPASSWORD="$DB_PASSWORD" pg_dump -U "$DB_OWNER" -Fc "$DB_NAME" \
  > "$WORK/database.dump"

echo "==> Archiving uploads volume (documents, OCR artifacts)"
docker run --rm -v "${PROJECT}_uploads":/data:ro -v "$WORK":/out alpine \
  tar czf /out/uploads.tar.gz -C /data .

cat > "$WORK/manifest.json" <<EOF
{
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "contents": ["database.dump (pg_dump custom format)", "uploads.tar.gz (UPLOAD_DIR volume)"],
  "postgres_db": "$DB_NAME",
  "postgres_owner": "$DB_OWNER",
  "app_version": "see /health"
}
EOF

ARCHIVE="$OUT_DIR/health-assistant-$STAMP.tar.gz"
tar czf "$ARCHIVE" -C "$WORK" .
echo "==> Done: $ARCHIVE"
echo "    Restore with: scripts/restore.sh $ARCHIVE --yes"
echo "    Retention is manual: keep at least the last N archives off-machine."
