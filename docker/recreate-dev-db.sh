#!/bin/bash
# Recreate the dev-db Postgres from scratch (run from the repo root, or the
# compose --env-file below resolves relative to the caller's CWD).
docker compose --env-file .env -f docker/docker-compose.dev-db.yml down
# Current project name (docker-compose.dev-db.yml pins `name:`) + the legacy
# dir-derived name from before that pin.
for VOL in health-assistant-dev_postgres_data-dev1 docker_postgres_data-dev1; do
    docker volume rm "$VOL" 2>/dev/null || true
done
docker compose --env-file .env -f docker/docker-compose.dev-db.yml up -d postgres-dev1
