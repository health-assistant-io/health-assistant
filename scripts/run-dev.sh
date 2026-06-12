#!/bin/bash

# Health Assistant Development Startup Script

echo "Starting Health Assistant Development Environment..."

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
FORCE_CELERY_RESTART=false
FORCE_STOP=false

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --force-celery) FORCE_CELERY_RESTART=true ;;
        --force-stop) FORCE_STOP=true ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ "$FORCE_STOP" = true ]; then
    echo -e "${YELLOW}Force stopping all Health Assistant services...${NC}"
    pkill -f "uvicorn app.main:app" || true
    pkill -f "celery -A app.workers.celery_app" || true
    # Find and kill the node process associated with the frontend vite dev server
    # avoiding killing unrelated npm run dev commands on the system by matching the frontend dir
    pkill -f "vite.*--port 3000" || true
    rm -f backend/celerybeat.pid
    echo -e "${GREEN}All services stopped.${NC}"
    exit 0
fi

# Check if we're in the right directory
if [ ! -d "backend" ] || [ ! -d "frontend" ]; then
    echo -e "${RED}Error: Please run this script from the Health Assistant root directory${NC}"
    exit 1
fi

# Function to check if a port is in use
check_port() {
    if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        return 0  # Port is in use
    else
        return 1  # Port is free
    fi
}

# Check required ports
echo -e "${YELLOW}Checking required ports...${NC}"
if check_port 8000; then
    echo -e "${RED}Port 8000 is already in use. Please stop the existing process.${NC}"
    exit 1
fi

if check_port 3000; then
    echo -e "${YELLOW}Port 3000 is in use. Frontend will use an alternative port.${NC}"
fi

# Start backend
echo -e "${GREEN}Preparing backend environment...${NC}"
cd backend

# Robust virtual environment setup
VENV_DIR="venv"
if [ -d ".venv" ]; then
    VENV_DIR=".venv"
fi

# Verify existing venv is functional (paths can break if directory is moved)
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
        echo -e "${RED}Error: Failed to create virtual environment. Ensure 'python3-venv' (or equivalent) is installed.${NC}"
        exit 1
    fi
    echo -e "${GREEN}Virtual environment created successfully.${NC}"
fi

# Activate virtual environment
if [ -f "$VENV_DIR/bin/activate" ]; then
    # We source it so that subsequent commands (like python/pip/alembic/uvicorn) run in the venv context
    source "$VENV_DIR/bin/activate"
else
    echo -e "${RED}Error: Activation script not found at $VENV_DIR/bin/activate.${NC}"
    exit 1
fi

# Fallback: ensure we are using the venv's binaries directly in case 'source' partially fails
VENV_PYTHON="$PWD/$VENV_DIR/bin/python"
VENV_PIP="$PWD/$VENV_DIR/bin/pip"

if [ ! -x "$VENV_PYTHON" ]; then
    echo -e "${RED}Error: Virtual environment python not found or not executable. Please delete '$VENV_DIR' and try again.${NC}"
    exit 1
fi

# Install dependencies
echo -e "${YELLOW}Installing/Verifying backend dependencies...${NC}"
"$VENV_PYTHON" -m pip install -q --upgrade pip
if ! "$VENV_PIP" install -q -r requirements.txt; then
    echo -e "${RED}Error: Failed to install requirements. Please check requirements.txt.${NC}"
    exit 1
fi

# Run database migrations
echo -e "${YELLOW}Running database migrations...${NC}"
alembic upgrade head || echo -e "${RED}Migration failed. Proceeding anyway...${NC}"

# Start backend
echo -e "${GREEN}Starting backend server...${NC}"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 > ../logging/backend.log 2>&1 &

BACKEND_PID=$!
echo -e "${YELLOW}Backend started (PID: $BACKEND_PID, logs: logging/backend.log)${NC}"

# Start Celery worker
echo -e "${GREEN}Starting Celery worker...${NC}"
# Check if Redis is running
if ! lsof -Pi :6379 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo -e "${YELLOW}Warning: Redis (port 6379) is not running. OCR processing will not work.${NC}"
else
    if [ "$FORCE_CELERY_RESTART" = true ]; then
        echo -e "${YELLOW}Force restarting existing Celery processes...${NC}"
        pkill -f "celery -A app.workers.celery_app" || true
        sleep 2
    fi

    # Start worker using the app instance directly
    celery -A app.workers.celery_app worker --loglevel=info >> ../logging/celery.log 2>&1 &
    WORKER_PID=$!
    echo -e "${YELLOW}Celery worker started (PID: $WORKER_PID, logs: logging/celery.log)${NC}"

    # Start Celery Beat for periodic tasks
    rm -f celerybeat.pid # Clean up stale lock files
    celery -A app.workers.celery_app beat --loglevel=info >> ../logging/celery.log 2>&1 &
    BEAT_PID=$!
    echo -e "${YELLOW}Celery Beat started (PID: $BEAT_PID, logs: logging/celery.log)${NC}"
fi

cd ..

# Wait for backend to start
echo -e "${YELLOW}Waiting for backend to start...${NC}"
sleep 5

# Check if backend started successfully
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo -e "${RED}Backend failed to start. Check logs above for details.${NC}"
    echo -e "${YELLOW}Note: Database connection is optional. App will run without PostgreSQL.${NC}"
    exit 1
fi

# Create admin user if database is available
echo -e "${YELLOW}Setting up admin user...${NC}"
cd backend
source "$VENV_DIR/bin/activate"
python3 scripts/create_system_admin.py --email admin@healthassistant.local --password admin123 2>&1 | grep -E "(Health Assistant|Creating|Database|Admin|Credentials|Email|Password|IMPORTANT|Error|already exists)" || true
cd ..

# Start frontend
echo -e "${GREEN}Starting frontend server...${NC}"
cd frontend

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

npm run dev -- --host 0.0.0.0 --port 3000 &
FRONTEND_PID=$!
cd ..

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Health Assistant is now running!${NC}"
echo -e "${GREEN}================================${NC}"
echo -e "Backend:  ${GREEN}http://localhost:8000${NC}"
echo -e "Frontend: ${GREEN}http://localhost:3000${NC}"
echo -e "Mobile:   ${GREEN}http://$(hostname -I | awk '{print $1}'):3000${NC}"
echo -e "API Docs: ${GREEN}http://localhost:8000/docs${NC}"
echo -e ""
echo -e "${YELLOW}Note: Database connection is optional. Run with PostgreSQL for full functionality.${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"

# Cleanup function
cleanup() {
    echo -e "\n${YELLOW}Shutting down...${NC}"
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    kill $WORKER_PID 2>/dev/null
    kill $BEAT_PID 2>/dev/null
    rm -f backend/celerybeat.pid
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait for both processes
wait $BACKEND_PID $FRONTEND_PID
