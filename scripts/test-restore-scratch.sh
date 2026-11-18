#!/usr/bin/env bash
# Scratch-container restore drill (plan 65 Gate A / CI).
#
# Proves scripts/restore.sh end-to-end against real TimescaleDB containers:
#
#   1. container "src"  — pinned timescale/timescaledb image with the shipped
#      init-db.sql / init-roles.sh (same bootstrap as the standalone stack)
#   2. alembic upgrade head + a small known seed (tenant, user, patient,
#      document, device, ~2 days of telemetry, one audit chain entry)
#   3. pg_dump -Fc (single-threaded) → archive shaped like backup.sh output
#   4. restore.sh into container "tgt" (EMPTY instance, container mode)
#      with the Timescale pre/post hooks, policy reconciliation, re-grants
#      and the script's own verification
#   5. assertions: row counts parity, RLS fail-closed + tenant-context read,
#      trend/CAGG query returns data, CAGG + retention + compression jobs,
#      RLS policy parity, alembic head parity, audit-chain hash walk,
#      live DB untouched, live-DB refusal fires
#
# Usage: scripts/test-restore-scratch.sh [--keep] [timescale-image]
#   --keep            keep the containers/network for debugging
#   <image>           image to pin (default: timescale/timescaledb:latest-pg16,
#                     the tag the compose files pin)
#
# Requires: docker, uv (backend venv for alembic + the app-code seed/verify).
# Docker unavailable? CI runs this on docker-enabled runners; everything else
# (refusal contracts) is covered by backend/tests/test_restore_script.py.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="timescale/timescaledb:latest-pg16"
KEEP=0
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) IMAGE="$arg" ;;
  esac
done

NET="ha-restore-scratch-net"
SRC="ha-restore-scratch-src"
TGT="ha-restore-scratch-tgt"
DB="neuronection_health"          # the "live" database on src
TARGET_DB="ha_restore_scratch"    # the throwaway target on tgt
OWNER="neuronection_health_owner"
APP="neuronection_health_app"
PW="ha-restore-scratch-pw"
# Distinct app-role secret (ADR-0064): docker/init-roles.sh refuses to bootstrap
# when POSTGRES_APP_PASSWORD is missing or equals POSTGRES_PASSWORD, so the
# scratch drill must mirror the real two-credential bootstrap.
APP_PW="ha-restore-scratch-app-pw"
SRC_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
TGT_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
WORK="$(mktemp -d)"
T_TOTAL="$(date +%s)"

say() { echo "==> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

cleanup() {
  if [[ "$KEEP" -eq 1 ]]; then
    say "KEEP=1 — keeping $SRC (127.0.0.1:$SRC_PORT), $TGT (127.0.0.1:$TGT_PORT), network $NET, workdir $WORK"
    return 0
  fi
  docker rm -f "$SRC" "$TGT" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

command -v docker >/dev/null || fail "docker is required"
docker info >/dev/null 2>&1 || fail "docker daemon is not running"
command -v uv >/dev/null || fail "uv is required (backend venv: alembic + seed)"

say "Scratch restore drill — image: $IMAGE"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE" >/dev/null
docker network create "$NET" >/dev/null

say "Starting source instance '$SRC' (127.0.0.1:$SRC_PORT) with the shipped init scripts"
docker run -d --name "$SRC" --network "$NET" \
  -e POSTGRES_USER="$OWNER" -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB="$DB" \
  -e POSTGRES_APP_USER="$APP" -e POSTGRES_APP_PASSWORD="$APP_PW" \
  -p "127.0.0.1:${SRC_PORT}:5432" \
  -v "$ROOT/docker/init-db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro" \
  -v "$ROOT/docker/init-roles.sh:/docker-entrypoint-initdb.d/02-init-roles.sh:ro" \
  "$IMAGE" >/dev/null

pg_wait() { # pg_wait <container> [user] — probes TCP inside the container:
  # the docker-entrypoint temp server answers on the unix socket while init
  # scripts still run; only the real server listens on 5432/TCP.
  for _ in $(seq 1 60); do
    docker exec "${1:?}" pg_isready -h localhost -U "${2:-$OWNER}" >/dev/null 2>&1 && return 0
    sleep 2
  done
  fail "postgres in $1 did not become ready"
}
pg_wait "$SRC"

# ── Migrate + seed the source (venv, app code) ──────────────────────────────
say "Migrating source to alembic head (backend venv)"
cd "$ROOT/backend"
export HA_DATABASE_URL="postgresql+asyncpg://$OWNER:$PW@127.0.0.1:${SRC_PORT}/$DB"
export HA_SESSION_KEY="scratch-session-key-000000000000000000000000000000000000"
export HA_REFRESH_KEY="scratch-refresh-key-00000000000000000000000000000000000"
uv run alembic upgrade head >/dev/null
say "Alembic head: $(uv run alembic current 2>/dev/null | awk 'NF {print $1; exit}')"

say "Seeding a small known dataset (tenant, user, patient, document, device, telemetry, audit)"
uv run python - <<'PY'
import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.core.security import get_password_hash
from app.models.audit_model import AuditEvent
from app.models.document_model import DocumentModel
from app.models.enums import Gender, Role
from app.models.fhir.patient import Patient
from app.models.telemetry_model import TelemetryDataModel, TelemetryDeviceModel
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.audit import audit_chain
from app.utils.slug import slugify


async def main() -> None:
    async with AsyncSessionLocal() as db:
        tenant = TenantModel(name="Restore Drill Tenant", slug=slugify("restore-drill-tenant"))
        db.add(tenant)
        await db.flush()
        admin = UserModel(
            email="drill@restore-scratch.local",
            password_hash=get_password_hash("correct-horse-battery"),
            full_name="Drill Operator",
            role=Role.SYSTEM_ADMIN,
            tenant_id=tenant.id,
        )
        db.add(admin)
        await db.flush()
        tenant.owner_id = admin.id

        patient = Patient(
            tenant_id=tenant.id,
            name=[{"family": "Drill", "given": ["Sample"], "text": "Sample Drill"}],
            gender=Gender.FEMALE,
            birth_date=date(1990, 1, 1),
        )
        db.add(patient)
        await db.flush()

        db.add(
            DocumentModel(
                filename="drill-report.pdf",
                file_path=f"tenants/{tenant.id}/documents/{patient.id}/drill-report.pdf",
                object_key=f"tenants/{tenant.id}/documents/drill-report.pdf",
                owner_id=admin.id,
                tenant_id=tenant.id,
                patient_id=patient.id,
                status="ready",
            )
        )

        device = TelemetryDeviceModel(
            tenant_id=tenant.id,
            patient_id=patient.id,
            source="scratch",
            external_ref="drill-device-1",
            label="Drill Watch",
        )
        db.add(device)
        await db.flush()

        base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        for i in range(48):  # ~2 days of hourly heart-rate samples
            db.add(
                TelemetryDataModel(
                    tenant_id=tenant.id,
                    patient_id=patient.id,
                    device_id=device.id,
                    slug="heart-rate",
                    timestamp=base - timedelta(hours=i),
                    value=60.0 + (i % 10),
                    unit="bpm",
                    raw_value=60.0 + (i % 10),
                    raw_unit="bpm",
                )
            )

        entry = AuditEvent(
            action="restore.drill.seed",
            resource_type="drill",
            resource_id=patient.id,
            outcome="ok",
            created_at=datetime.now(timezone.utc),
            tenant_id=tenant.id,
            chain_key=audit_chain.chain_key_for(tenant.id),
        )
        await audit_chain.append(db, entry)
        await db.commit()
        print(f"seeded tenant={tenant.id} patient={patient.id} device={device.id}")
    await engine.dispose()


asyncio.run(main())
PY

sql_src() { docker exec "$SRC" env PGPASSWORD="$PW" psql -U "$OWNER" -d "$DB" -tAc "$1"; }
sql_tgt() { docker exec "$TGT" env PGPASSWORD="$PW" psql -U "$OWNER" -d "$TARGET_DB" -tAc "$1"; }
# The app role connects with the distinct app secret (restore.sh creates the
# target role from HA_APP_DB_PASSWORD below — same split as init-roles.sh).
sql_tgt_app() { docker exec "$TGT" env PGPASSWORD="$APP_PW" psql -U "$APP" -d "$TARGET_DB" -tAc "$1"; }

COUNT_SQL="SELECT 'tenants', count(*) FROM tenants
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'fhir_patients', count(*) FROM fhir_patients
UNION ALL SELECT 'documents', count(*) FROM documents
UNION ALL SELECT 'telemetry_data', count(*) FROM telemetry_data
UNION ALL SELECT 'audit_events', count(*) FROM audit_events"
declare -A BEFORE=()
while IFS='|' read -r k v; do BEFORE["$k"]="$v"; done < <(sql_src "$COUNT_SQL")
say "Source counts: $(for k in tenants users fhir_patients documents telemetry_data audit_events; do printf '%s=%s ' "$k" "${BEFORE[$k]}"; done)"
[[ "${BEFORE[telemetry_data]}" -ge 48 ]] || fail "seed failed (telemetry_data=${BEFORE[telemetry_data]})"
[[ "${BEFORE[audit_events]}" -ge 1 ]] || fail "audit seed failed"

# ── Dump + archive (backup.sh output shape) ─────────────────────────────────
say "Dumping source (pg_dump -Fc, single-threaded)"
T_DUMP="$(date +%s)"
docker exec "$SRC" pg_dump -U "$OWNER" -Fc "$DB" > "$WORK/database.dump"
DUMP_SECONDS=$(( $(date +%s) - T_DUMP ))
DUMP_BYTES="$(wc -c < "$WORK/database.dump" | tr -d ' ')"
say "Dump: $DUMP_BYTES bytes in ${DUMP_SECONDS}s"
cat > "$WORK/manifest.json" <<EOF
{
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "contents": ["database.dump (pg_dump custom format)", "uploads.tar.gz (HA_UPLOAD_DIR volume)"],
  "postgres_db": "$DB",
  "postgres_owner": "$OWNER",
  "scratch_drill": true
}
EOF
tar czf "$WORK/uploads.tar.gz" -C "$WORK" manifest.json # stand-in for the uploads volume
ARCHIVE="$WORK/health-assistant-scratch.tar.gz"
tar czf "$ARCHIVE" -C "$WORK" manifest.json database.dump uploads.tar.gz

# ── Safety: the live DB must be refused, e2e ────────────────────────────────
say "Refusal check: --target $DB (the live DB) must exit 2"
if POSTGRES_DB="$DB" POSTGRES_PASSWORD="$PW" \
    bash "$ROOT/scripts/restore.sh" "$ARCHIVE" --target "$DB" --yes 2>"$WORK/refusal.err"; then
  fail "restore.sh accepted the LIVE database as target"
fi
[[ "$(grep -c "LIVE database" "$WORK/refusal.err")" -ge 1 ]] || fail "refusal message missing: $(cat "$WORK/refusal.err")"
say "Refused as expected"

# ── Restore into an empty second instance ───────────────────────────────────
say "Starting empty target instance '$TGT' (127.0.0.1:$TGT_PORT)"
docker run -d --name "$TGT" --network "$NET" \
  -e POSTGRES_USER="$OWNER" -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB="$DB" \
  -p "127.0.0.1:${TGT_PORT}:5432" \
  "$IMAGE" >/dev/null
pg_wait "$TGT"

say "Running scripts/restore.sh (throwaway target $TARGET_DB, Timescale hooks, --refresh-caggs)"
T_RESTORE="$(date +%s)"
# HA_APP_DB_PASSWORD gives the target's app role the distinct app secret
# (ADR-0064): restore.sh creates the role itself on the empty target, and the
# drill's app-role probes below must authenticate the way production does.
POSTGRES_DB="$DB" POSTGRES_PASSWORD="$PW" HA_APP_DB_PASSWORD="$APP_PW" TIMESCALE_IMAGE="$IMAGE" \
  bash "$ROOT/scripts/restore.sh" "$ARCHIVE" \
  --target "$TARGET_DB" --pg-container "$TGT" --pg-network "$NET" \
  --refresh-caggs --yes \
  | tee "$WORK/restore.out"
RESTORE_SECONDS=$(( $(date +%s) - T_RESTORE ))
say "restore.sh wall time: ${RESTORE_SECONDS}s"

# ── Assertions ──────────────────────────────────────────────────────────────
say "Assertions"
ok() { say "  PASS: $*"; }

declare -A AFTER_SRC=()
while IFS='|' read -r k v; do AFTER_SRC["$k"]="$v"; done < <(sql_src "$COUNT_SQL")
declare -A RESTORED=()
while IFS='|' read -r k v; do RESTORED["$k"]="$v"; done < <(sql_tgt "$COUNT_SQL")
for k in tenants users fhir_patients documents telemetry_data audit_events; do
  [[ "${RESTORED[$k]}" == "${BEFORE[$k]}" ]] || fail "row count mismatch for $k: target=${RESTORED[$k]} source=${BEFORE[$k]}"
  [[ "${AFTER_SRC[$k]}" == "${BEFORE[$k]}" ]] || fail "LIVE source changed during drill for $k: ${AFTER_SRC[$k]} != ${BEFORE[$k]}"
done
ok "row counts parity (all six tables) and live DB untouched"

[[ "$(sql_tgt "SELECT count(*) FROM pg_policies")" -ge 1 ]] && [[ "$(sql_tgt "SELECT count(*) FROM pg_policies")" == "$(sql_src "SELECT count(*) FROM pg_policies")" ]] \
  && ok "RLS policies restored ($(sql_tgt "SELECT count(*) FROM pg_policies") policies)"
[[ "$(sql_tgt "SELECT version_num FROM alembic_version")" == "$(sql_src "SELECT version_num FROM alembic_version")" ]] \
  && ok "alembic head parity ($(sql_tgt "SELECT version_num FROM alembic_version"))"

JOBS="$(sql_tgt "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name IN ('policy_refresh_continuous_aggregate','policy_retention','policy_compression')")"
[[ "$JOBS" -ge 5 ]] || fail "Timescale jobs missing on target: $JOBS (expect 3 CAGG + retention + compression)"
ok "CAGG refresh + retention + compression policies scheduled ($JOBS jobs)"
[[ "$(sql_tgt "SELECT count(*) FROM timescaledb_information.jobs")" -ge "$(sql_src "SELECT count(*) FROM timescaledb_information.jobs")" ]] \
  || fail "fewer timescaledb jobs on target than source"

# RLS fail-closed: the app role without context sees nothing on RLS-covered
# tables. telemetry_data is the documented exception (0016_rls: compression
# forbids RLS on the raw hypertable; its fail-closed surface is the
# GUC-filtered telemetry_live_scoped view).
for t in audit_events documents fhir_patients; do
  [[ "$(sql_tgt_app "SELECT count(*) FROM $t")" == "0" ]] || fail "RLS not fail-closed on $t (app role saw rows without context)"
done
[[ "$(sql_tgt_app "SELECT count(*) FROM telemetry_live_scoped")" == "0" ]] \
  || fail "RLS not fail-closed on telemetry_live_scoped"
ok "RLS fail-closed (app role, no context: 0 rows on RLS tables + scoped telemetry view)"

# One known record under the tenant context — the way the app reads.
TENANT="$(sql_tgt "SELECT id::text FROM tenants ORDER BY created_at LIMIT 1")"
PATIENT="$(sql_tgt "SELECT id::text FROM fhir_patients WHERE tenant_id='$TENANT' ORDER BY created_at DESC LIMIT 1")"
CTX_SQL="SELECT set_config('app.tenant_id', '$TENANT', false); SELECT count(*) FROM fhir_patients WHERE id='$PATIENT'"
[[ "$(sql_tgt_app "$CTX_SQL" | tail -1)" == "1" ]] || fail "known patient not visible under tenant context"
ok "known record visible under tenant context (patient $PATIENT)"
[[ "$(sql_tgt_app "SELECT set_config('app.tenant_id', '$TENANT', false); SELECT count(*) FROM documents" | tail -1)" == "${BEFORE[documents]}" ]] \
  && ok "tenant-context document count matches"
[[ "$(sql_tgt_app "SELECT set_config('app.tenant_id', '$TENANT', false); SELECT count(*) FROM telemetry_hourly_scoped" | tail -1)" -ge 1 ]] \
  || fail "trend query (telemetry_hourly_scoped) returned no data"
ok "trend query returns data through the scoped CAGG view"
[[ "$(sql_tgt "SELECT count(*) FROM telemetry_hourly")" -ge 1 ]] \
  && ok "CAGG contents materialized (--refresh-caggs rebuild: $(sql_tgt "SELECT count(*) FROM telemetry_hourly") hourly buckets)"

# Audit-chain integrity: full hash walk through the app code, against the
# restored target only.
say "Audit-chain hash walk (app code → target)"
cd "$ROOT/backend"
HA_DATABASE_URL="postgresql+asyncpg://$OWNER:$PW@127.0.0.1:${TGT_PORT}/$TARGET_DB" \
  uv run python - <<'PY'
import asyncio
import sys

from app.core.database import AsyncSessionLocal, engine
from app.services.audit.audit_chain import verify_all_chains


async def main() -> None:
    async with AsyncSessionLocal() as db:
        results = await verify_all_chains(db)
    await engine.dispose()
    bad = [r for r in results if not r.get("ok")]
    print(f"chains={len(results)} failed={bad}")
    sys.exit(1 if bad else 0)


asyncio.run(main())
PY
ok "audit-chain integrity (hash walk over the restored chain)"

echo
say "Scratch restore drill PASSED in $(( $(date +%s) - T_TOTAL ))s total"
say "  dump (RPO window closure): ${DUMP_SECONDS}s (${DUMP_BYTES} bytes)"
say "  restore.sh (RTO actual, scratch-sized dataset): ${RESTORE_SECONDS}s"
say "  drill timings are recorded in this log; run quarterly on real-shaped data (H4)"
