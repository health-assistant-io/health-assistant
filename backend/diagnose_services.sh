#!/bin/bash

# Configuration
REDIS_URL=${REDIS_URL:-"redis://localhost:6379/0"}
CELERY_APP="app.workers.celery_app"

echo "=========================================="
echo "Health Assistant Service Diagnostics"
echo "=========================================="

# 1. Check Redis
echo -n "[1/4] Checking Redis... "
if command -v redis-cli >/dev/null 2>&1; then
    # Parse host and port from URL if it's a standard redis:// url
    REDIS_HOST=$(echo $REDIS_URL | sed -e 's|redis://||' -e 's|/.*||' -e 's|:.*||')
    REDIS_PORT=$(echo $REDIS_URL | sed -e 's|.*:||' -e 's|/.*||')
    
    if [ -z "$REDIS_HOST" ]; then REDIS_HOST="localhost"; fi
    if [ -z "$REDIS_PORT" ]; then REDIS_PORT="6379"; fi

    PONG=$(redis-cli -h $REDIS_HOST -p $REDIS_PORT ping 2>/dev/null)
    if [ "$PONG" == "PONG" ]; then
        echo "OK (Ping successful)"
    else
        echo "FAILED (Redis is not responding at $REDIS_HOST:$REDIS_PORT)"
    fi
else
    echo "SKIPPED (redis-cli not found, install it or check logs)"
fi

# 2. Check Celery Worker Status
echo -n "[2/4] Checking Celery Workers... "
if [ -d "venv" ]; then
    CELERY_BIN="./venv/bin/celery"
else
    CELERY_BIN="celery"
fi

# We use -A to point to the app and inspect ping to check active workers
WORKER_STATUS=$($CELERY_BIN -A $CELERY_APP inspect ping 2>&1)
if echo "$WORKER_STATUS" | grep -q "pong"; then
    echo "OK (Worker is active)"
else
    echo "FAILED (No active workers found)"
    echo "     Technical details: $WORKER_STATUS"
fi

# 3. Check for Celery Process
echo -n "[3/4] Checking for Celery processes in OS... "
CELERY_PIDS=$(ps aux | grep -v grep | grep "celery" | wc -l)
if [ "$CELERY_PIDS" -gt 0 ]; then
    echo "OK ($CELERY_PIDS processes found)"
else
    echo "FAILED (No celery processes detected via ps)"
fi

# 4. Check Database Connection (Quick Test)
echo -n "[4/4] Checking Database... "
if [ -d "venv" ]; then
    PYTHON_BIN="./venv/bin/python"
else
    PYTHON_BIN="python"
fi

DB_STATUS=$(export PYTHONPATH=$PYTHONPATH:. && $PYTHON_BIN -c "
import asyncio
from app.core.config import settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def check():
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.connect() as conn:
            await conn.execute(text('SELECT 1'))
        print('OK')
    except Exception as e:
        print(f'FAILED ({e})')
    finally:
        await engine.dispose()
asyncio.run(check())
" 2>&1)

echo "$DB_STATUS"

echo "=========================================="
if [[ "$WORKER_STATUS" != *"pong"* ]]; then
    echo "SUGGESTION: Try starting the worker with:"
    echo "cd backend && ./venv/bin/celery -A app.workers.celery_app worker --loglevel=info"
fi
echo "=========================================="
