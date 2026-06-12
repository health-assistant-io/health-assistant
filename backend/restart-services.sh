#!/bin/bash

# Restart Redis and Celery for Health Assistant backend
# Usage: ./restart-services.sh

set -e

echo "========================================"
echo "Restarting Redis and Celery Services"
echo "========================================"

# Change to backend directory
cd "$(dirname "$0")"

# Stop existing Celery workers
echo "[1/4] Stopping Celery workers..."
pkill -f -9 celery || true
pkill -f -9 celery-worker || true
echo "✓ Celery workers stopped"

# Stop existing Redis server
echo "[2/4] Stopping Redis server..."
redis-cli shutdown 2>/dev/null || pkill -f -9 redis-server || true
echo "✓ Redis server stopped"

# Wait for processes to fully stop
sleep 2

# Start Redis server
echo "[3/4] Starting Redis server..."
if command -v redis-server &> /dev/null;
    then
        redis-server --daemonize yes
    elif command -v redis-server &> /dev/null;
        then
            redis-server --daemonize yes
    else
        echo "Warning: redis-server not found, skipping..."
    fi
echo "✓ Redis server started"

# Start Celery worker in background
echo "[4/4] Starting Celery worker..."
if [ -d venv ];
    then
        ./venv/bin/celery -A app.workers.celery_app worker --loglevel=info --detach
    else
        celery -A app.workers.celery_app worker --loglevel=info --detach
    fi
echo "✓ Celery worker started"

# Wait for services to initialize
sleep 2

# Verify services are running
echo "========================================"
echo "Verifying services..."
echo "========================================"

if command -v redis-cli &> /dev/null;
    then
        if redis-cli ping &> /dev/null;
            then
                echo "✓ Redis: Running"
            else
                echo "✗ Redis: Not running"
        fi
    else
        echo "⚠ Redis: Cannot verify (redis-cli not found)"
    fi

if pgrep -f "celery.*worker" &> /dev/null;
    then
        echo "✓ Celery: Running"
    else
        echo "✗ Celery: Not running"
    fi

echo "========================================"
echo "Restart complete!"
echo "========================================"