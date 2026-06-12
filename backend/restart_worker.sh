#!/bin/bash

echo "Stopping existing Celery workers..."
pkill -f "celery -A app.workers.celery_app"

sleep 2

echo "Starting Celery worker in background..."
# We use the same command structure detected earlier
nohup ./venv/bin/python3 -m celery -A app.workers.celery_app worker --loglevel=info > celery.log 2>&1 &

echo "Celery worker started. PID: $!"
echo "Check celery.log for output."
