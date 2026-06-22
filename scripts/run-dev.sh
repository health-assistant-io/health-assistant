#!/bin/bash

# Health Assistant Development Startup Script
#
# Bootstrap (venv, deps, migrations, admin user) then runs every dev process
# as a single group under honcho (Procfile.dev): backend + worker + beat +
# flower + frontend. A single Ctrl+C cleanly stops everything, and if any
# process crashes honcho exits loud — no more "celery silently not running"
# surprises with jobs stuck in PENDING.

echo "Starting Health Assistant Development Environment..."

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
FORCE_STOP=false

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --force-stop) FORCE_STOP=true ;;
        --force-celery)
            # Accepted for backward compat; honcho owns the worker lifecycle now.
            echo -e "${YELLOW}--force-celery is deprecated (honcho owns celery now); ignoring.${NC}"
            ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ "$FORCE_STOP" = true ]; then
    echo -e "${YELLOW}Force stopping all Health Assistant services...${NC}"

    # Kill the honcho parent first so it doesn't respawn children.
    pkill -f "honcho start" || true

    # Then kill the individual processes (covers the case where someone ran them manually).
    pkill -f "uvicorn app.main:app" || true
    pkill -f "celery -A app.workers.celery_app" || true
    pkill -f "vite.*--port 3000" || true

    sleep 2

    # Force kill stragglers
    pkill -9 -f "honcho start" || true
    pkill -9 -f "uvicorn app.main:app" || true
    pkill -9 -f "celery -A app.workers.celery_app" || true
    pkill -9 -f "vite.*--port 3000" || true

    for port in 8000 3000 5555; do
        if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo -e "${YELLOW}Force killing process on port $port...${NC}"
            lsof -ti :$port | xargs kill -9 2>/dev/null || true
        fi
    done

    rm -f backend/celerybeat.pid

    sleep 1
    for port in 8000 3000; do
        if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo -e "${RED}Error: Failed to free port $port. Check 'lsof -i :$port'.${NC}"
            exit 1
        fi
    done

    echo -e "${GREEN}All services stopped successfully.${NC}"
    exit 0
fi

# Check if we're in the right directory
if [ ! -d "backend" ] || [ ! -d "frontend" ]; then
    echo -e "${RED}Error: Please run this script from the Health Assistant root directory${NC}"
    exit 1
fi

if [ ! -f "Procfile.dev" ]; then
    echo -e "${RED}Error: Procfile.dev not found in project root.${NC}"
    exit 1
fi

# Function to check if a port is in use
check_port() {
    if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        return 0
    else
        return 1
    fi
}

echo -e "${YELLOW}Checking required ports...${NC}"
if check_port 8000; then
    echo -e "${RED}Port 8000 is already in use. Stop the existing process (./scripts/run-dev.sh --force-stop).${NC}"
    exit 1
fi
if check_port 5555; then
    echo -e "${YELLOW}Port 5555 (Flower) is in use; Flower may fail to start.${NC}"
fi
if check_port 3000; then
    echo -e "${YELLOW}Port 3000 is in use. Frontend will use an alternative port.${NC}"
fi

# Start backend setup
echo -e "${GREEN}Preparing backend environment...${NC}"
cd backend

VENV_DIR="venv"
if [ -d ".venv" ]; then
    VENV_DIR=".venv"
fi

if [ -d "$VENV_DIR" ]; then
    if ! "$VENV_DIR/bin/python" -c "import sys" &> /dev/null || ! "$VENV_DIR/bin/pip" --version &> /dev/null; then
        echo -e "${YELLOW}Existing virtual environment appears broken. Recreating...${NC}"
        rm -rf "$VENV_DIR"
    fi
fi

if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating one in '$VENV_DIR'...${NC}"
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}Error: python3 is not installed or not in PATH.${NC}"
        exit 1
    fi
    if ! python3 -m venv "$VENV_DIR"; then
        echo -e "${RED}Error: Failed to create virtual environment. Ensure 'python3-venv' is installed.${NC}"
        exit 1
    fi
    echo -e "${GREEN}Virtual environment created successfully.${NC}"
fi

if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo -e "${RED}Error: Activation script not found at $VENV_DIR/bin/activate.${NC}"
    exit 1
fi

VENV_PYTHON="$PWD/$VENV_DIR/bin/python"
VENV_PIP="$PWD/$VENV_DIR/bin/pip"

if [ ! -x "$VENV_PYTHON" ]; then
    echo -e "${RED}Error: Virtual environment python not found or not executable.${NC}"
    exit 1
fi

echo -e "${YELLOW}Installing/Verifying backend dependencies...${NC}"
"$VENV_PYTHON" -m pip install -q --upgrade pip
if ! "$VENV_PIP" install -q -r requirements.txt; then
    echo -e "${RED}Error: Failed to install requirements.${NC}"
    exit 1
fi

# Honcho is the process supervisor for dev — verify it landed.
if ! command -v honcho &> /dev/null; then
    echo -e "${RED}Error: honcho is not installed even after requirements install. Add 'honcho' to requirements.txt.${NC}"
    exit 1
fi

# PYTHONPATH must include backend (for `app.*`) and project root (for `integrations.*`).
# Exported here so every Procfile.dev process inherits it without re-setting it.
export PYTHONPATH="$PWD:$(dirname "$PWD")"

# Run database migrations
echo -e "${YELLOW}Running database migrations...${NC}"
alembic upgrade head || echo -e "${RED}Migration failed. Proceeding anyway...${NC}"

# Create admin user if database is available
echo -e "${YELLOW}Setting up admin user...${NC}"
python3 scripts/create_system_admin.py --email admin@healthassistant.local --password admin123 2>&1 | grep -E "(Health Assistant|Creating|Database|Admin|Credentials|Email|Password|IMPORTANT|Error|already exists)" || true

# Frontend deps (done here so the honcho-managed `npm run dev` doesn't pay the install cost on every start)
cd ../frontend
if ! command -v npm &> /dev/null; then
    echo -e "${RED}Error: npm is not installed or not in PATH.${NC}"
    exit 1
fi
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}Installing frontend dependencies...${NC}"
    if ! npm install; then
        echo -e "${RED}Error: Failed to install frontend dependencies.${NC}"
        exit 1
    fi
fi
cd ..

# Pre-flight: warn if Redis is not running (celery worker + beat + flower all need it).
if ! lsof -Pi :6379 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo -e "${YELLOW}Warning: Redis (port 6379) is not running. Worker/beat/flower will fail to connect.${NC}"
    echo -e "${YELLOW}Start it via: docker compose -f docker/docker-compose.dev-db.yml up -d redis${NC}"
fi

# Clean up any stale celery beat lock from a previous run.
rm -f backend/celerybeat.pid

# Remove stale consolidated celery logs from before per-process split (worker/beat/flower now log separately).
rm -f logging/celery.log logging/celery.*.log

# Snapshot the LAN IP for the success banner (best-effort, non-fatal).
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$LAN_IP" ] && LAN_IP="localhost"

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Starting dev processes under honcho...${NC}"
echo -e "${GREEN}================================${NC}"
echo -e "Backend:   ${GREEN}http://localhost:8000${NC}"
echo -e "API Docs:  ${GREEN}http://localhost:8000/docs${NC}"
echo -e "Frontend:  ${GREEN}http://localhost:3000${NC}"
echo -e "Mobile:    ${GREEN}http://${LAN_IP}:3000${NC}"
echo -e "Flower:    ${GREEN}http://localhost:5555${NC}  (Celery monitoring)"
echo -e ""
echo -e "${YELLOW}Processes: backend, worker, beat, flower, frontend (see Procfile.dev).${NC}"
echo -e "${YELLOW}If any process crashes, honcho stops the whole group so you see the error.${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop all services.${NC}"
echo -e ""

# `exec` replaces this script with honcho so signals (Ctrl+C) go straight to
# honcho and propagate to all children. honcho reads Procfile.dev from cwd.
cd "$(dirname "$0")/.."
exec honcho start -f Procfile.dev
