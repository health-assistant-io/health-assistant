#!/bin/bash

docker compose -f docker-compose-dev.yml down
docker volume rm docker_postgres_data-dev1
docker compose -f docker-compose-dev.yml up -d postgres-dev1
