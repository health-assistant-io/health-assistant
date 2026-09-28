#!/bin/bash
# Recreate the dev-db Postgres from scratch (run from the repo root, or the
# compose --env-file below resolves relative to the caller's CWD).
docker compose --env-file .env -f docker/docker-compose.dev-db.yml down
docker volume rm docker_postgres_data-dev1 || true
docker compose --env-file .env -f docker/docker-compose.dev-db.yml up -d postgres-dev1
