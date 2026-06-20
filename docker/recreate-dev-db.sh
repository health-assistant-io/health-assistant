#!/bin/bash

docker compose -f docker-compose.dev-db.yml down
docker volume rm docker_postgres_data-dev1 || true
docker compose -f docker-compose.dev-db.yml up -d postgres-dev1
