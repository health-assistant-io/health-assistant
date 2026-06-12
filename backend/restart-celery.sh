#!/bin/bash

# Quick restart of Celery worker only
# Usage: ./restart-celery.sh

cd "$(dirname "$0")"

echo "Restarting Celery worker..."

# Stop existing workers
pkill -f -9 celery 2>/dev/null || true
pkill -f -9 celery-worker 2>/dev/null || true
sleep 1

# Start new worker
if [ -d venv ];
    then
        ./venv/bin/celery -A app.workers.celery_app worker --loglevel=info --detach
    else
        celery -A app.workers.celery_app worker --loglevel=info --detach
    fi

echo "✓ Celery restarted"