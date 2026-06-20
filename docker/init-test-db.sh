#!/bin/bash
# Creates the test database alongside the app database.
# Postgres docker-entrypoint runs *.sh files with env substitution (unlike *.sql),
# so we can use $POSTGRES_USER here. Idempotent via \gexec guard.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  SELECT 'CREATE DATABASE health_assistant_test OWNER $POSTGRES_USER'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'health_assistant_test')\gexec
  GRANT ALL PRIVILEGES ON DATABASE health_assistant_test TO $POSTGRES_USER;
EOSQL

echo "Test database 'health_assistant_test' is ready."
