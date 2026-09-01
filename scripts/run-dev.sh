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
#   ./scripts/run-dev.sh --force          # free the dev ports, then start
#   ./scripts/run-dev.sh --force-stop     # kill every HA dev process and exit
#   ./scripts/run-dev.sh --no-bootstrap   # skip venv/deps bootstrap, just start
#   ./scripts/run-dev.sh --no-admin       # skip create_system_admin so the
#                                         # browser first-run setup wizard fires
#   ./scripts/run-dev.sh --force-celery   # deprecated (honcho owns celery),
#                                         # accepted for backward compatibility
#   ./scripts/run-dev.sh -h | --help      # print this help and exit
#
# Run from the Health Assistant project root. Reads the root .env for ports +
# secrets. Exits non-zero on any failure during bootstrap.

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
VENV_DIR="venv"

NO_ADMIN=false
NO_BOOTSTRAP=false
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

# Load the root .env so every process (backend, worker, frontend, scripts)
# gets the same config regardless of how it's launched. honcho also loads
# .env, but exporting here makes direct-uvicorn / IDE / script launches work
# too. dc_load_env auto-exports every variable it defines.
dc_load_env .env

# Tell the backend exactly where its .env lives (absolute path) so Pydantic
# Settings doesn't have to guess via CWD. The backend's _resolve_env_file()
# checks HA_ENV_FILE first, then walks up from config.py as a fallback.
export HA_ENV_FILE="$PWD/.env"

# .env may override the ports; re-read them after loading.
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
FLOWER_PORT="${FLOWER_PORT:-5555}"
REDIS_PORT="${REDIS_PORT:-6379}"

dc_step "checking required ports"
dc_check_port_free "$BACKEND_PORT" "backend"
dc_check_port_warn "$FLOWER_PORT" "Flower"
dc_check_port_warn "$FRONTEND_PORT" "frontend (vite will pick an alternative port)"

if [[ "$NO_BOOTSTRAP" = false ]]; then
  dc_step "preparing backend environment"
  cd backend
  if [[ -d ".venv" ]]; then
    VENV_DIR=".venv"
  fi
  dc_ensure_venv "$VENV_DIR" requirements.txt
  export PATH="$PWD/$VENV_DIR/bin:$PATH"

  # PYTHONPATH must include backend (for `app.*`) and project root (for
  # `integrations.*`). Exported here so every Procfile.dev process inherits it
  # without re-setting it.
  dc_backend_root="$PWD"
  dc_backend_parent="$(dirname "$PWD")"
  export PYTHONPATH="$dc_backend_root:$dc_backend_parent"

  dc_step "running database migrations"
  alembic upgrade head || dc_error "Migration failed. Proceeding anyway..."

  if [[ "$NO_ADMIN" = true ]]; then
    dc_warn "Skipping admin creation (--no-admin)."
    dc_warn "  If the DB has no users, visit http://localhost:$FRONTEND_PORT to run the"
    dc_warn "  first-run setup wizard. To test it from a clean slate, reset the"
    dc_warn "  existing admin first, e.g.:"
    dc_warn "    psql \"\$DATABASE_URL\" -c 'TRUNCATE users, tenants RESTART IDENTITY CASCADE;'"
  else
    dc_step "setting up admin user"
    python3 scripts/create_system_admin.py --email admin@healthassistant.local --password admin123 2>&1 | grep -E "(Health Assistant|Creating|Database|Admin|Credentials|Email|Password|IMPORTANT|Error|already exists)" || true
  fi
  cd ..

  dc_ensure_node_deps frontend npm
fi

# Make the venv's honcho/alembic/python resolvable even with --no-bootstrap.
if [[ -d "backend/$VENV_DIR/bin" ]]; then
  export PATH="$PWD/backend/$VENV_DIR/bin:$PATH"
fi

# Pre-flight: warn if Redis is not running (celery worker + beat + flower all need it).
if ! dc_port_in_use "$REDIS_PORT"; then
  dc_warn "Redis (port $REDIS_PORT) is not running. Worker/beat/flower will fail to connect."
  dc_warn "Start it via: docker compose -f docker/docker-compose.dev-db.yml up -d redis"
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
