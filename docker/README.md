# Health Assistant - Docker Utilities & Cheat Sheet

This directory contains the Docker configuration files for Health Assistant. 

For full installation instructions (Production or Development), please refer to:
- [Installation Guide](../docs/INSTALL.md)
- [Development Guide](../docs/DEVELOPMENT.md)

---

## Dev Infrastructure (Host-based development)

When you run the app on the host (via `../scripts/run-dev.sh`) rather than entirely inside Docker, use these lightweight stacks to spin up just the required infrastructure dependencies.

### Database + Redis — `docker-compose.dev-db.yml`

```bash
docker compose -f docker-compose.dev-db.yml up -d
```

- **Postgres (TimescaleDB)** on host port **5432** (configurable via `POSTGRES_PORT` in the root `.env`). The init scripts create both `health_assistant` and `health_assistant_test` (the latter is needed by pytest).
- **Redis** on host port **6379** (Celery broker + Stage 2 OAuth state store).

If you already run Redis on the host (port 6379 busy), start Postgres only:

```bash
docker compose -f docker-compose.dev-db.yml up -d postgres-dev1
```

First run creates the test DB automatically. For an existing volume, create it once manually:

```bash
docker compose -f docker-compose.dev-db.yml exec -T postgres-dev1 \
  psql -U admin -d health_assistant <<'SQL'
SELECT 'CREATE DATABASE health_assistant_test OWNER admin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'health_assistant_test')\gexec
SQL
```

### Local FHIR server (Stage 2 testing) — `fhir-test-server/docker-compose.yml`

```bash
docker compose -f fhir-test-server/docker-compose.yml up -d
```

A local **HAPI FHIR R4** server for offline testing of the FHIR pull path (`fhir_search` + `fhir_observation_to_create`) against real FHIR search, pagination, and `OperationOutcome`. 

Verify it's up: `curl http://localhost:${HAPI_PORT:-8080}/fhir/metadata | head`

---

## Docker CLI Cheat Sheet

Useful commands when working with the full Docker environment (`docker-compose.dev.yml` or the production flavors). *Ensure you add `--env-file .env` if your environment variables aren't already loaded.*

### Accessing Internal Services

```bash
# Access backend bash shell
docker compose -f docker-compose.dev.yml exec backend bash

# Access PostgreSQL prompt
docker compose -f docker-compose.dev.yml exec postgres psql -U admin -d health_assistant

# Access Redis prompt
docker compose -f docker-compose.dev.yml exec redis redis-cli
```

### Migrations (Alembic)

```bash
# Run database migrations
docker compose -f docker-compose.dev.yml exec backend alembic upgrade head

# Create a new migration script
docker compose -f docker-compose.dev.yml exec backend alembic revision --autogenerate -m "migration_name"
```

### Logs & Troubleshooting

```bash
# Follow logs for all services
docker compose -f docker-compose.dev.yml logs -f

# Follow logs for a specific service (e.g., worker or backend)
docker compose -f docker-compose.dev.yml logs -f worker
```

### Cleanup

```bash
# Stop all services
docker compose -f docker-compose.dev.yml down

# Nuke everything (Stops services and DELETES ALL VOLUMES/DATA)
docker compose -f docker-compose.dev.yml down -v
```