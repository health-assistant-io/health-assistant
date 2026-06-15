#!/bin/bash

docker compose -f docker-compose.db.yml down
docker volume rm docker_postgres_data-dev1
docker compose -f docker-compose.db.yml up -d postgres-dev1
