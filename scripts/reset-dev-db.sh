#!/bin/bash
#
# scripts/reset-dev-db.sh
#
# Wipe the dev Postgres (+ Redis) Docker volumes and bring up a fresh, empty
# database + cache. Use this when local schema/seed data gets into a bad state,
# when you want to test the fresh-install migration path, or when a migration
# can't be rolled back cleanly.
#
# This is DESTRUCTIVE — every row in the dev DB is lost. The init scripts
# (docker/init-db.sql + init-test-db.sh) recreate both the app DB and the
# `health_assistant_test` DB on first start of the new volume.
#
# Usage:
#   ./scripts/reset-dev-db.sh                  # prompt to confirm, wipe both, restart
#   ./scripts/reset-dev-db.sh -y               # no confirmation prompt
#   ./scripts/reset-dev-db.sh --keep-redis     # preserve the Redis volume (wipe Postgres only)
#   ./scripts/reset-dev-db.sh --no-start       # wipe only, leave the stack down
#   ./scripts/reset-dev-db.sh --migrate        # run `alembic upgrade head` after start
#   ./scripts/reset-dev-db.sh --purge-dangling # also delete leftover docker_* volumes
#
# This script manages Docker infra only. Stop the app first
# (./scripts/run-dev.sh --force-stop) and start it again afterwards
# (./scripts/run-dev.sh) — it will run migrations + seeding on the fresh DB.
#
# Exits non-zero on any failure (set -euo pipefail) so it's safe to chain.

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}!${NC} $*"; }
fatal() { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

# ── Defaults & arg parsing ──────────────────────────────────────────────────
ASSUME_YES=false
KEEP_REDIS=false
NO_START=false
MIGRATE=false
PURGE_DANGLING=false

print_help() {
  sed -n '3,28p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -y|--yes)        ASSUME_YES=true ;;
    --keep-redis)    KEEP_REDIS=true ;;
    --no-start)      NO_START=true ;;
    --migrate)       MIGRATE=true ;;
    --purge-dangling) PURGE_DANGLING=true ;;
    -h|--help)       print_help ;;
    *) fatal "Unknown argument: $1 (try --help)" ;;
  esac
  shift
done

# ── Locate project root (run from anywhere) ─────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

[[ -f docker/docker-compose.dev-db.yml ]] || fatal "Run from the Health Assistant project root (cannot find docker/docker-compose.dev-db.yml)."
[[ -f .env ]] || fatal ".env not found at project root."

# ── Preflight checks ────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || fatal "docker is not installed or not in PATH."
docker compose version >/dev/null 2>&1 || fatal "docker compose v2 is required (the 'docker compose' subcommand)."

# Base compose invocation (matches the documented dev workflow).
COMPOSE=(docker compose --env-file .env -f docker/docker-compose.dev-db.yml)

# Resolve the compose project name so we can reason about volume names.
PROJECT="$("${COMPOSE[@]}" config --format json 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("name",""))' 2>/dev/null || true)"
# Fallback: the compose file lives in docker/, so the default project is "docker".
[[ -z "$PROJECT" ]] && PROJECT="docker"

# ── Warn if the app is still running against this DB ────────────────────────
if command -v lsof >/dev/null 2>&1 && lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
  warn "Backend is listening on :8000 — stopping the DB under it will cause errors."
  warn "Stop it first with: ./scripts/run-dev.sh --force-stop"
  [[ "$ASSUME_YES" = true ]] || warn "Continuing anyway in 3s (Ctrl+C to abort)..." && sleep 3
fi

# ── Enumerate the volumes we're about to destroy ────────────────────────────
mapfile -t PG_VOLS < <(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT}" \
                                 --filter "label=com.docker.compose.volume=postgres_data-dev1" 2>/dev/null || true)
mapfile -t RD_VOLS < <(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT}" \
                                --filter "label=com.docker.compose.volume=redis_data-dev1" 2>/dev/null || true)

# Legacy/fallback: if labels didn't match (older compose), fall back to name-prefix matching.
if [[ ${#PG_VOLS[@]} -eq 0 ]]; then
  mapfile -t PG_VOLS < <(docker volume ls -q 2>/dev/null | grep -E "^${PROJECT}_postgres_data-dev1$" || true)
fi
if [[ ${#RD_VOLS[@]} -eq 0 ]]; then
  mapfile -t RD_VOLS < <(docker volume ls -q 2>/dev/null | grep -E "^${PROJECT}_redis_data-dev1$" || true)
fi

echo ""
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${RED}  THIS WILL PERMANENTLY DELETE THE DEV DATABASE${NC}"
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Project:      ${PROJECT}"
echo -e "  Compose file: docker/docker-compose.dev-db.yml"
echo -n  -e "  Postgres vol: "
if [[ ${#PG_VOLS[@]} -gt 0 ]]; then echo -e "${RED}${PG_VOLS[*]}${NC}"; else echo "(none found — fresh volume will be created)"; fi
if [[ "$KEEP_REDIS" = true ]]; then
  echo -e "  Redis vol:    ${GREEN}preserved (--keep-redis)${NC}"
else
  echo -n  -e "  Redis vol:    "
  if [[ ${#RD_VOLS[@]} -gt 0 ]]; then echo -e "${RED}${RD_VOLS[*]}${NC}"; else echo "(none found)"; fi
fi
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [[ "$ASSUME_YES" != true ]]; then
  read -r -p "Type 'yes' to destroy the volumes listed above: " REPLY
  [[ "$REPLY" = "yes" ]] || { warn "Aborted (nothing was changed)."; exit 130; }
fi

# ── 1. Stop the stack ───────────────────────────────────────────────────────
info "Stopping the dev stack…"
"${COMPOSE[@]}" down --remove-orphans
ok "Stack stopped."

# ── 2. Remove volumes ───────────────────────────────────────────────────────
# `down -v` is the clean path for the default (wipe both). For --keep-redis we
# remove only the Postgres volume(s) by name (preserving Redis + any other data).
remove_volumes_by_name() {
  local label="$1" name="$2"
  mapfile -t vols < <(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT}" \
                                --filter "label=com.docker.compose.volume=${name}" 2>/dev/null || true)
  if [[ ${#vols[@]} -gt 0 ]]; then
    docker volume rm "${vols[@]}" >/dev/null
    ok "Removed ${#vols[@]} ${label} volume(s): ${vols[*]}"
  fi
}

if [[ "$KEEP_REDIS" = true ]]; then
  info "Preserving Redis (--keep-redis); removing Postgres volume only…"
  remove_volumes_by_name "Postgres" "postgres_data-dev1"
  # Belt-and-suspenders: also catch the bare-name match if the label filter missed.
  mapfile -t pg_leftover < <(docker volume ls -q 2>/dev/null | grep -E "^${PROJECT}_postgres_data-dev1$" || true)
  [[ ${#pg_leftover[@]} -gt 0 ]] && docker volume rm "${pg_leftover[@]}" >/dev/null && ok "Removed leftover: ${pg_leftover[*]}"
else
  info "Removing all named volumes (Postgres + Redis)…"
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null
  # Verify; fall back to per-volume removal if `down -v` left anything behind.
  remove_volumes_by_name "Postgres" "postgres_data-dev1"
  remove_volumes_by_name "Redis"    "redis_data-dev1"
fi

# Optional: clean any stale docker_* volumes from prior compose layouts.
if [[ "$PURGE_DANGLING" = true ]]; then
  info "Purging dangling ${PROJECT}_* volumes (--purge-dangling)…"
  mapfile -t dangling < <(docker volume ls -q 2>/dev/null | grep -E "^${PROJECT}_" || true)
  [[ ${#dangling[@]} -gt 0 ]] && docker volume rm "${dangling[@]}" >/dev/null && ok "Removed ${#dangling[@]} dangling: ${dangling[*]}"
fi

# Final verification — warn (don't fail) if something survived.
mapfile -t survivors < <(docker volume ls -q 2>/dev/null | grep -E "^${PROJECT}_(postgres_data-dev1|redis_data-dev1)$" || true)
if [[ ${#survivors[@]} -gt 0 ]]; then
  [[ "$KEEP_REDIS" = true ]] && survivors=("${survivors[@]/${PROJECT}_redis_data-dev1/}")
  survivors=("${survivors[@]// }")
  [[ -n "${survivors[*]// }" ]] && warn "Some volumes survived removal: ${survivors[*]}"
fi
ok "Volumes cleared."

# ── 3. Start (unless --no-start) ────────────────────────────────────────────
if [[ "$NO_START" = true ]]; then
  warn "Stack left down (--no-start). Bring it up with: ${COMPOSE[*]##* } up -d"
  exit 0
fi

info "Starting fresh stack (waiting for healthchecks)…"
# `--wait` blocks until containers are healthy (compose >= 2.1.1; we require v2).
if ! "${COMPOSE[@]}" up -d --wait >/dev/null 2>&1; then
  # Fallback: older/edge compose where --wait misbehaves — poll manually.
  warn "'up --wait' failed; falling back to a manual health poll…"
  "${COMPOSE[@]}" up -d
  for i in {1..30}; do
    health="$(docker inspect --format '{{.State.Health.Status}}' health-assistant-postgres-dev1 2>/dev/null || echo unknown)"
    [[ "$health" = "healthy" ]] && break
    sleep 1
  done
fi

# Confirm healthy.
pg_health="$(docker inspect --format '{{.State.Health.Status}}' health-assistant-postgres-dev1 2>/dev/null || echo unknown)"
rd_state="$(docker inspect  --format '{{.State.Status}}'       health-assistant-redis-dev1    2>/dev/null || echo unknown)"
if [[ "$pg_health" != "healthy" ]]; then
  fatal "Postgres did not become healthy (status: ${pg_health}). Check 'docker compose -f docker/docker-compose.dev-db.yml logs postgres-dev1'."
fi
[[ "$rd_state" != "running" ]] && warn "Redis is ${rd_state} (expected running)."
ok "Stack is up. Postgres=${pg_health}, Redis=${rd_state}."

# ── 4. Optional: run migrations ─────────────────────────────────────────────
if [[ "$MIGRATE" = true ]]; then
  info "Running alembic upgrade head (--migrate)…"
  [[ -d backend/venv ]] && source backend/venv/bin/activate 2>/dev/null || true
  ( cd backend && PYTHONPATH=.:.. alembic upgrade head ) && ok "Migrations applied." \
    || warn "Migration failed — the app will retry on startup (./scripts/run-dev.sh)."
fi

# ── 5. Done — show what to do next ──────────────────────────────────────────
# Read back the actual ports from .env so the hint is accurate.
PG_PORT="$(grep -E '^POSTGRES_PORT=' .env | cut -d= -f2 || true)"; PG_PORT="${PG_PORT:-5432}"
RD_PORT="$(grep -E '^REDIS_PORT='    .env | cut -d= -f2 || true)"; RD_PORT="${RD_PORT:-6379}"
PG_DB="$(grep -E '^POSTGRES_DB='     .env | cut -d= -f2 || true)"; PG_DB="${PG_DB:-health_assistant}"
PG_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2 || true)"; PG_USER="${PG_USER:-admin}"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Dev DB reset complete${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Postgres:  ${PG_USER}@localhost:${PG_PORT}/${PG_DB}  (+ ${PG_DB}_test)"
echo -e "  Redis:     localhost:${RD_PORT}"
echo -e "  Next:      ${BLUE}./scripts/run-dev.sh${NC}  (migrates + seeds + starts the app)"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
