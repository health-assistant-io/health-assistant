#!/usr/bin/env bash
# Health Assistant instance backup (ADR-0063 / plan 64): Postgres dump +
# uploads volume (documents, OCR artifacts), ENCRYPTED, with an optional
# off-host upload and a backup_runs ledger row per run.
#
# Usage: scripts/backup.sh [output-dir]
#
# Reads the root .env (or environment) for POSTGRES_* settings. Output:
#   <output-dir>/health-assistant-YYYYMMDD-HHMMSS.tar.gz[.enc]
#     manifest.json   — what this archive contains (+ encryption method)
#     database.dump   — pg_dump custom format (neuronection_health)
#     uploads.tar.gz  — the `uploads` volume (HA_UPLOAD_DIR=/app/uploads),
#                       only when object storage is NOT the active backend
#                       (provider-durable bytes need no local tarball)
#
# Encryption (health: REQUIRED per ADR-0063 §3 — PHI backups; the key must
# NOT live with the backup — keep it in the operator's key ring):
#   BACKUP_ENCRYPTION_KEY    passphrase for OpenSSL AES-256-CBC (PBKDF2,
#                            200k iterations) — the recommended default.
#   BACKUP_AGE_RECIPIENT     age public key (alternative; needs `age`).
#   BACKUP_ALLOW_UNENCRYPTED only for local, non-DR convenience backups —
#                           refuses otherwise.
#
# Off-host upload (ADR-0063 §2 — T1 requires an off-host target):
#   BACKUP_DESTINATION       rclone-style remote/path: s3://bucket/prefix,
#                            azure://container/prefix, gcs://bucket/prefix,
#                            or a configured rclone remote (remote:path).
#                            Upload tool is auto-detected per scheme:
#                            aws s3 cp / azcopy / rclone copy (rclone is
#                            the common denominator).
#   BACKUP_UPLOAD_TOOL       force one: auto (default) | aws | azcopy | rclone
#
# Ledger (ADR-0063 §2): every run INSERTs into backup_runs (created here if
# the migrate provisioning hasn't yet): started_at, finished_at, status,
# bytes, destination, checksum, error_class. The writer is this script, not
# the web app. A run without a row is a conformance violation; a stale row
# (> 2× the interval) should raise the external alert.
#
# Example host timer (T1 — systemd, /etc/systemd/system/health-backup.timer):
#   [Timer]
#   OnCalendar=*-*-* 03:15:00
# or plain cron:  15 3 * * * cd /opt/health-assistant && scripts/backup.sh
set -euo pipefail

print_help() {
    sed -n '3,/^[^#]/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 0
}
case "${1:-}" in -h|--help) print_help ;; esac

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
: "${DB_NAME:?}" "${DB_OWNER:?}"
# Owner credential (pg_dump + ledger writes). HA_DB_OWNER_PASSWORD is the
# canonical ADR-0064 name when a deployment splits the env; POSTGRES_PASSWORD
# remains the owner secret in this repo's compose/.env surface (and restore.sh
# reads it — plan 65 owns the restore redesign).
DB_PASSWORD="${HA_DB_OWNER_PASSWORD:-${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD (DB OWNER password) in .env}}"
COMPOSE_FILE="${HA_COMPOSE_FILE:-docker/docker-compose.standalone.yml}"
[[ "$COMPOSE_FILE" = /* ]] || COMPOSE_FILE="$ROOT/$COMPOSE_FILE"
PROJECT="${HA_COMPOSE_PROJECT:-$(resolve_compose_project)}"
read -r -a COMPOSE_BIN <<< "$DOCKER_COMPOSE_CMD" # "docker compose" → two words
COMPOSE=("${COMPOSE_BIN[@]}" --env-file "$ROOT/.env" -f "$COMPOSE_FILE")

STORAGE_BACKEND="${HA_OBJECT_STORAGE_BACKEND:-}"
CLOUD="${HA_CLOUD_PROFILE:-}"
DESTINATION="${BACKUP_DESTINATION:-}"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
RUN_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ERROR_CLASS="unknown"
BYTES=0
CHECKSUM=""
FINAL_DESTINATION=""

ledger() {
  # ADR-0063 §2: one row per run, written by THIS script (not the app).
  # Self-healing CREATE for installs whose migrate predates the table.
  local status="$1" bytes="$2" destination="$3" checksum="$4" error_class="$5"
  local finished
  finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "${COMPOSE[@]}" exec -T postgres psql -U "$DB_OWNER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL 2>/dev/null || {
CREATE TABLE IF NOT EXISTS backup_runs (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL,
    bytes       BIGINT,
    destination TEXT,
    checksum    TEXT,
    error_class TEXT
);
INSERT INTO backup_runs (started_at, finished_at, status, bytes, destination, checksum, error_class)
VALUES ('$RUN_STARTED', '$finished', '$status', ${bytes:-NULL}, '$destination', '$checksum', '$error_class');
SQL
    echo "WARN: could not write the backup_runs ledger row (is the stack up?)" >&2
    return 1
  }
}

fail_run() {
  ERROR_CLASS="$1"
  ledger "failed" "$BYTES" "$DESTINATION" "$CHECKSUM" "$ERROR_CLASS" || true
  echo "==> backup FAILED ($ERROR_CLASS)" >&2
  exit 1
}

# ─── 1. Encryption posture (decided FIRST — the manifest records it) ─────────
if [[ -n "${BACKUP_ENCRYPTION_KEY:-}" ]]; then
  ENCRYPTION="openssl-aes-256-cbc-pbkdf2"
elif command -v age >/dev/null 2>&1 && [[ -n "${BACKUP_AGE_RECIPIENT:-}" ]]; then
  ENCRYPTION="age"
elif [[ "${BACKUP_ALLOW_UNENCRYPTED:-}" == "1" ]]; then
  ENCRYPTION="none (BACKUP_ALLOW_UNENCRYPTED=1)"
  echo "==> WARNING: writing an UNENCRYPTED archive (BACKUP_ALLOW_UNENCRYPTED=1)." >&2
  echo "    Health backups must be encrypted for anything that leaves this host (ADR-0063)." >&2
else
  echo "refusing: no encryption configured (health/PHI backups are encrypted: required — ADR-0063)." >&2
  echo "Set BACKUP_ENCRYPTION_KEY (recommended) or BACKUP_AGE_RECIPIENT (+ age)." >&2
  echo "Local-only convenience runs may set BACKUP_ALLOW_UNENCRYPTED=1 to override." >&2
  ledger "refused" "0" "$DESTINATION" "" "encryption_required" || true
  exit 2
fi

# ─── 2. Database dump ────────────────────────────────────────────────────────
echo "==> Dumping database '$DB_NAME' (as $DB_OWNER)"
"${COMPOSE[@]}" exec -T postgres \
  env PGPASSWORD="$DB_PASSWORD" pg_dump -U "$DB_OWNER" -Fc "$DB_NAME" \
  > "$WORK/database.dump" || fail_run "pg_dump"

# ─── 3. Uploads volume (local backend only — object bytes are provider-durable)
if [[ -z "$CLOUD" ]] && { [[ -z "$STORAGE_BACKEND" ]] || [[ "$STORAGE_BACKEND" == "local" ]]; }; then
  echo "==> Archiving uploads volume (documents, OCR artifacts)"
  docker run --rm -v "${PROJECT}_uploads":/data:ro -v "$WORK":/out alpine \
    tar czf /out/uploads.tar.gz -C /data . || fail_run "uploads_tar"
else
  echo "==> Object storage backend '${STORAGE_BACKEND:-${CLOUD}}': object bytes are provider-durable; skipping the uploads volume"
fi

# ─── 4. Manifest + archive ───────────────────────────────────────────────────
CONTENTS='"database.dump (pg_dump custom format)"'
if [[ -f "$WORK/uploads.tar.gz" ]]; then
  CONTENTS+=' , "uploads.tar.gz (HA_UPLOAD_DIR volume)"'
fi
cat > "$WORK/manifest.json" <<EOF
{
  "created_at": "$RUN_STARTED",
  "contents": [$CONTENTS],
  "encryption": "$ENCRYPTION",
  "postgres_db": "$DB_NAME",
  "postgres_owner": "$DB_OWNER",
  "app_version": "see /health"
}
EOF

ARCHIVE_NAME="health-assistant-$STAMP.tar.gz"
if [[ -f "$WORK/uploads.tar.gz" ]]; then
  tar czf "$WORK/$ARCHIVE_NAME" -C "$WORK" ./database.dump ./manifest.json ./uploads.tar.gz
else
  tar czf "$WORK/$ARCHIVE_NAME" -C "$WORK" ./database.dump ./manifest.json
fi

# ─── 5. Encrypt ──────────────────────────────────────────────────────────────
case "$ENCRYPTION" in
  openssl-*)
    echo "==> Encrypting (OpenSSL AES-256-CBC, PBKDF2)"
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
      -in "$WORK/$ARCHIVE_NAME" -out "$WORK/$ARCHIVE_NAME.enc" \
      -pass env:BACKUP_ENCRYPTION_KEY || fail_run "encrypt"
    ;;
  age)
    echo "==> Encrypting (age)"
    age -r "$BACKUP_AGE_RECIPIENT" -o "$WORK/$ARCHIVE_NAME.enc" \
      "$WORK/$ARCHIVE_NAME" || fail_run "encrypt"
    ;;
esac
if [[ -f "$WORK/$ARCHIVE_NAME.enc" ]]; then
  rm -f "$WORK/$ARCHIVE_NAME"
  ARCHIVE_NAME="$ARCHIVE_NAME.enc"
fi
FINAL_DESTINATION="$OUT_DIR/$ARCHIVE_NAME"

# ─── 6. Checksum + size ──────────────────────────────────────────────────────
CHECKSUM="$(sha256sum "$WORK/$ARCHIVE_NAME" | awk '{print $1}')"
BYTES="$(stat -c %s "$WORK/$ARCHIVE_NAME")"

mv "$WORK/$ARCHIVE_NAME" "$FINAL_DESTINATION"
echo "==> Archive: $FINAL_DESTINATION ($BYTES bytes, sha256 $CHECKSUM)"

# ─── 7. Off-host upload (T1 requires an off-host target — ADR-0063 §2) ──────
if [[ -n "$DESTINATION" ]]; then
  TOOL="${BACKUP_UPLOAD_TOOL:-auto}"
  case "$DESTINATION" in
    s3://*) [[ "$TOOL" == "auto" ]] && { command -v aws >/dev/null 2>&1 && TOOL=aws || TOOL=rclone; } ;;
    azure://*) [[ "$TOOL" == "auto" ]] && { command -v azcopy >/dev/null 2>&1 && TOOL=azcopy || TOOL=rclone; } ;;
    *) [[ "$TOOL" == "auto" ]] && TOOL=rclone ;;
  esac
  echo "==> Uploading off-host to $DESTINATION (tool: $TOOL)"
  case "$TOOL" in
    aws)
      # s3://bucket/prefix — aws cli reads credentials from env/instance profile
      aws s3 cp "$FINAL_DESTINATION" "$DESTINATION/$ARCHIVE_NAME" || fail_run "upload_aws"
      ;;
    azcopy)
      azcopy copy "$FINAL_DESTINATION" "${DESTINATION%/}/$ARCHIVE_NAME" || fail_run "upload_azcopy"
      ;;
    rclone)
      # rclone copy <local> <remote:path> — remote configured via `rclone config`
      rclone copy "$FINAL_DESTINATION" "$DESTINATION/$ARCHIVE_NAME" || fail_run "upload_rclone"
      ;;
    *)
      echo "unknown BACKUP_UPLOAD_TOOL '$TOOL' (auto|aws|azcopy|rclone)" >&2
      fail_run "upload_tool"
      ;;
  esac
  FINAL_DESTINATION="$DESTINATION/$ARCHIVE_NAME"
else
  echo "==> NOTE: no BACKUP_DESTINATION set — archive stayed on-host." >&2
  echo "    Same-host copies are NOT disaster recovery (ADR-0063): configure an" >&2
  echo "    off-host target (s3://…, azure://…, rclone remote:path) for T1." >&2
fi

ledger "success" "$BYTES" "$FINAL_DESTINATION" "$CHECKSUM" "" \
  || echo "    (the run itself succeeded — fix the ledger writer)" >&2

echo "==> Done: $ARCHIVE_NAME → $FINAL_DESTINATION"
echo "    Restore (throwaway target, never the live DB): decrypt if needed, then"
echo "      scripts/restore.sh <plain.tar.gz> --target ${DB_NAME}_restored --yes"
echo "    Ledger row written to backup_runs (stale runs > 2× interval should alert)."
