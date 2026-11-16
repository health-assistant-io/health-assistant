#!/bin/bash
# Health Assistant Development Startup Script
#
# Bootstrap (venv, deps, migrations, admin user) then runs every dev process
# as a single group under honcho (Procfile.dev): backend + worker + beat +
# flower + frontend. A single Ctrl+C cleanly stops everything, and if any
# process crashes honcho exits loud — no more "celery silently not running"
# surprises with jobs stuck in PENDING.
#
# Usage:
#   ./scripts/run-dev.sh                  # bootstrap + start the honcho group
#                                         # (dev admin: admin@healthassistant.local
#                                         #  / devadmin12345)
#   ./scripts/run-dev.sh --force          # free the dev ports, then start
#   ./scripts/run-dev.sh --force-stop     # kill every HA dev process and exit
#   ./scripts/run-dev.sh --no-bootstrap   # skip venv/deps bootstrap, just start
#   ./scripts/run-dev.sh --no-admin       # skip create_system_admin so the
#                                         # browser first-run setup wizard fires
#   ./scripts/run-dev.sh --env-file PATH  # use another env file (e.g. the
#                                         # primary checkout's .env in a worktree)
#   ./scripts/run-dev.sh --no-env-check   # skip the DB/Redis reachability preflight
#   ./scripts/run-dev.sh --force-celery   # deprecated (honcho owns celery),
#                                         # accepted for backward compatibility
#   ./scripts/run-dev.sh -h | --help      # print this help and exit
#
# Run from the Health Assistant project root. Reads the root .env for ports +
# secrets. Fails fast when the env file or the dev database/Redis is missing
# instead of letting Alembic/workers die on a default localhost:5432.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
cd "$SCRIPT_DIR/.."
# shellcheck source=lib/dev-common.sh
source scripts/lib/dev-common.sh

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
FLOWER_PORT="${FLOWER_PORT:-5555}"
REDIS_PORT="${REDIS_PORT:-6379}"

# A fresh worktree has no venv yet; uv workspaces may keep it at the repo
# root (`./.venv`) or in `backend/` (legacy `.venv`/`venv`). Resolve whichever
# one actually contains the interpreter instead of assuming `backend/venv`.
REPO_ROOT="$PWD"
VENV_BIN=""
dc_find_venv_bin() {
  local candidate
  for candidate in "$REPO_ROOT/backend/.venv" "$REPO_ROOT/backend/venv" "$REPO_ROOT/.venv"; do
    if [[ -x "$candidate/bin/python" ]]; then
      VENV_BIN="$candidate/bin"
      return 0
    fi
  done
  return 1
}

NO_ADMIN=false
NO_BOOTSTRAP=false
NO_ENV_CHECK=false
ENV_FILE=".env"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --force-stop)
      echo -e "${DC_YELLOW}Force stopping all Health Assistant services...${DC_NC}"
      # SIGTERM pass first: kill the honcho parent so it doesn't respawn
      # children, then the individual processes (covers manually-run ones).
      dc_pkill "honcho start"
      dc_pkill "uvicorn app.main:app"
      dc_pkill "celery -A app.workers.celery_app"
      dc_pkill "vite.*--port $FRONTEND_PORT"
      sleep 2
      # SIGKILL escalation — celery worker/beat hold no TCP port, so without
      # this a stuck worker would survive in warm shutdown.
      dc_pkill9 "honcho start"
      dc_pkill9 "uvicorn app.main:app"
      dc_pkill9 "celery -A app.workers.celery_app"
      dc_pkill9 "vite.*--port $FRONTEND_PORT"
      dc_kill_port "$BACKEND_PORT"
      dc_kill_port "$FLOWER_PORT"
      dc_kill_port "$FRONTEND_PORT"
      rm -f backend/celerybeat.pid
      dc_check_port_free "$BACKEND_PORT" "backend"
      dc_check_port_free "$FRONTEND_PORT" "frontend"
      dc_ok "All services stopped successfully."
      exit 0
      ;;
    --force)
      dc_kill_port "$BACKEND_PORT"
      dc_kill_port "$FRONTEND_PORT"
      dc_kill_port "$FLOWER_PORT"
      ;;
    --no-admin) NO_ADMIN=true ;;
    --no-bootstrap) NO_BOOTSTRAP=true ;;
    --env-file)
      [[ -n "${2:-}" ]] || dc_die "--env-file requires a path"
      ENV_FILE="$2"
      shift
      ;;
    --no-env-check) NO_ENV_CHECK=true ;;
    --force-celery)
      dc_warn "--force-celery is deprecated (honcho owns celery now); ignoring."
      ;;
    -h|--help) dc_help "$SCRIPT_PATH" ;;
    *) dc_die "Unknown parameter: $1 (try --help)" ;;
  esac
  shift
done

if [[ ! -d "backend" || ! -d "frontend" ]]; then
  dc_die "Please run this script from the Health Assistant root directory (backend/ and frontend/ not found)."
fi
if [[ ! -f "Procfile.dev" ]]; then
  dc_die "Procfile.dev not found in project root."
fi

echo "Starting Health Assistant Development Environment..."

# Load the env file so every process (backend, worker, frontend, scripts)
# gets the same config regardless of how it's launched. honcho also loads
# .env, but exporting here makes direct-uvicorn / IDE / script launches work
# too. dc_load_env auto-exports every variable it defines.
ENV_ABS=""
if [[ -f "$ENV_FILE" ]]; then
  ENV_ABS="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
  dc_load_env "$ENV_ABS"
elif [[ -n "${HA_DATABASE_URL:-}" || -n "${DATABASE_URL:-}" || -n "${POSTGRES_HOST:-}" || -n "${POSTGRES_PORT:-}" ]]; then
  dc_warn "No $ENV_FILE found; using exported environment variables."
else
  dc_error "No $ENV_FILE found and no database environment is set."
  dc_error "Generate one from the template (writes keys + default ports):"
  dc_error "    python3 scripts/setup_env.py --mode=3"
  _common_dir="$(git rev-parse --git-common-dir 2>/dev/null || true)"
  if [[ -n "$_common_dir" && "$_common_dir" != ".git" ]]; then
    _primary_root="$(cd "$(dirname "$_common_dir")" && pwd)"
    dc_error "This checkout is a git worktree: env files are not shared with the primary."
    dc_error "    cp \"$_primary_root/.env\" .env"
    dc_error "    # or: ./scripts/run-dev.sh --env-file \"$_primary_root/.env\""
  fi
  dc_die "Refusing to start without an explicit environment."
fi

# Tell the backend exactly where its .env lives (absolute path) so Pydantic
# Settings doesn't have to guess via CWD. The backend's _resolve_env_file()
# checks HA_ENV_FILE first, then walks up from config.py as a fallback.
export HA_ENV_FILE="${ENV_ABS:-$PWD/$ENV_FILE}"

# .env may override the ports; re-read them after loading.
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
FLOWER_PORT="${FLOWER_PORT:-5555}"
REDIS_PORT="${REDIS_PORT:-6379}"

dc_step "checking required ports"
dc_check_port_free "$BACKEND_PORT" "backend"
dc_check_port_warn "$FLOWER_PORT" "Flower"
dc_check_port_warn "$FRONTEND_PORT" "frontend (vite will pick an alternative port)"

if [[ "$NO_ENV_CHECK" = false ]]; then
  dc_step "checking dev services"
  DB_HOST="${POSTGRES_HOST:-localhost}"
  DB_PORT="${POSTGRES_PORT:-5432}"
  REDIS_HOST="${REDIS_HOST:-localhost}"
  REDIS_PORT="${REDIS_PORT:-6379}"

  dc_tcp_reachable() {
    timeout 2 bash -c "</dev/tcp/$1/$2" >/dev/null 2>&1
  }

  _published_port() {
    # A running container can have no published host port (a stale/partial
    # compose recreation). Do not let `set -e` turn that into a silent exit;
    # return an empty value and let the explicit checks below explain it.
    local container="$1" internal="$2" published
    published="$(docker port "$container" "$internal/tcp" 2>/dev/null || true)"
    printf '%s\n' "${published##*:}"
  }

  if command -v docker >/dev/null 2>&1; then
    for _spec in "health-assistant-postgres-dev1:5432:POSTGRES_PORT:$DB_PORT:$DB_HOST"                  "health-assistant-redis-dev1:6379:REDIS_PORT:$REDIS_PORT:$REDIS_HOST"; do
      IFS=':' read -r _container _internal _var _configured _host <<< "$_spec"
      if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$_container"; then
        _published="$(_published_port "$_container" "$_internal")"
        if [[ -z "$_published" ]]; then
          dc_warn "$_container is running but publishes no host port."
          dc_warn "Recreate the dev database: docker compose --env-file \"$ENV_FILE\" -f docker/docker-compose.dev-db.yml up -d --force-recreate"
          continue
        fi
        if [[ "$_published" != "$_configured" && "$_host" =~ ^(localhost|127\.0\.0\.1)$ ]]; then
          dc_error "Port mismatch: $_container publishes $_published, but $ENV_FILE sets $_var=$_configured."
          dc_error "Set $_var=$_published in $ENV_FILE (or use --env-file with the checkout that owns this container)."
          dc_die "Refusing to start against the wrong port."
        fi
      fi
    done
  fi

  if ! dc_tcp_reachable "$DB_HOST" "$DB_PORT"; then
    dc_error "Postgres is not reachable at $DB_HOST:$DB_PORT."
    dc_error "Start the dev database and Redis:"
    dc_error "    docker compose --env-file \"$HA_ENV_FILE\" -f docker/docker-compose.dev-db.yml up -d"
    dc_die "Refusing to migrate against an unreachable database."
  fi
  if ! dc_tcp_reachable "$REDIS_HOST" "$REDIS_PORT"; then
    dc_error "Redis is not reachable at $REDIS_HOST:$REDIS_PORT."
    dc_error "Start the dev database and Redis:"
    dc_error "    docker compose --env-file \"$HA_ENV_FILE\" -f docker/docker-compose.dev-db.yml up -d"
    dc_die "Refusing to start without Redis (worker/beat/flower need it)."
  fi
  dc_ok "database $DB_HOST:$DB_PORT and redis $REDIS_HOST:$REDIS_PORT reachable"
fi

if [[ "$NO_BOOTSTRAP" = false ]]; then
  dc_step "preparing backend environment"
  cd backend
  uv sync --frozen
  dc_find_venv_bin || true
  if [[ -n "$VENV_BIN" ]]; then
    export PATH="$VENV_BIN:$PATH"
  fi

  # PYTHONPATH needs backend only — the extension subsystem lives inside
  # the app package (backend/app/extensions, ADR-0040 D24). The bridge SDK
  # satellites under ../integrations are standalone packages, never imported
  # by the backend.
  export PYTHONPATH="$PWD"

  dc_step "running database migrations"
  if ! alembic upgrade head; then
    dc_die "Database migration failed — fix the connection settings above and re-run."
  fi

  if [[ "$NO_ADMIN" = true ]]; then
    dc_warn "Skipping admin creation (--no-admin)."
    dc_warn "  If the DB has no users, visit http://localhost:$FRONTEND_PORT to run the"
    dc_warn "  first-run setup wizard. To test it from a clean slate, reset the"
    dc_warn "  existing admin first, e.g.:"
    dc_warn "    psql \"\$HA_DATABASE_URL\" -c 'TRUNCATE users, tenants RESTART IDENTITY CASCADE;'"
  else
    dc_step "setting up admin user"
    python3 scripts/create_system_admin.py --email admin@healthassistant.local --password devadmin12345 2>&1 | grep -E "(Health Assistant|Creating|Database|Admin|Credentials|Email|Password|IMPORTANT|Error|already exists)" || true
  fi
  cd ..

  dc_ensure_node_deps frontend pnpm
fi

# Make the venv's honcho/alembic/python resolvable even with --no-bootstrap.
if [[ -z "$VENV_BIN" ]]; then
  dc_find_venv_bin || true
fi
if [[ -n "$VENV_BIN" ]]; then
  export PATH="$VENV_BIN:$PATH"
fi

# Pre-flight: warn if Redis is not running (celery worker + beat + flower all need it).
if ! dc_port_in_use "$REDIS_PORT"; then
  dc_warn "Redis (port $REDIS_PORT) is not running. Worker/beat/flower will fail to connect."
  dc_warn "Start it via: docker compose --env-file .env -f docker/docker-compose.dev-db.yml up -d redis"
fi

# Clean up any stale celery beat lock from a previous run.
rm -f backend/celerybeat.pid

# Remove stale consolidated celery logs from before per-process split (worker/beat/flower now log separately).
rm -f logging/celery.log logging/celery.*.log

# Snapshot the LAN IP for the success banner (best-effort, non-fatal).
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$LAN_IP" ] && LAN_IP="localhost"

echo -e "${DC_GREEN}================================${DC_NC}"
echo -e "${DC_GREEN}Starting dev processes under honcho...${DC_NC}"
echo -e "${DC_GREEN}================================${DC_NC}"
echo -e "Backend:   ${DC_GREEN}http://localhost:$BACKEND_PORT${DC_NC}"
echo -e "API Docs:  ${DC_GREEN}http://localhost:$BACKEND_PORT/docs${DC_NC}"
echo -e "Frontend:  ${DC_GREEN}http://localhost:$FRONTEND_PORT${DC_NC}"
echo -e "Mobile:    ${DC_GREEN}http://${LAN_IP}:$FRONTEND_PORT${DC_NC}"
echo -e "Flower:    ${DC_GREEN}http://localhost:$FLOWER_PORT${DC_NC}  (Celery monitoring)"
echo -e ""
echo -e "${DC_YELLOW}Processes: backend, worker, beat, flower, frontend (see Procfile.dev).${DC_NC}"
echo -e "${DC_YELLOW}If any process crashes, honcho stops the whole group so you see the error.${DC_NC}"
echo -e "${DC_YELLOW}Press Ctrl+C to stop all services.${DC_NC}"
echo -e ""

# dc_exec_honcho replaces this script with honcho so signals (Ctrl+C) go
# straight to honcho and propagate to all children. honcho reads Procfile.dev
# from cwd.
dc_exec_honcho Procfile.dev
