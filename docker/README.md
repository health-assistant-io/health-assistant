# Health Assistant - Docker Setup

## Quick Start

```bash
cp .env.example .env
# Edit .env with your configuration
docker compose --env-file .env -f docker/docker-compose.yml up -d
```


## Dev infrastructure (host-based development)

When you run the app on the host (via `./scripts/run-dev.sh`) rather than in
Docker, use these lightweight stacks instead of the full `docker-compose.yml`:

### Database + Redis — `docker-compose.dev-db.yml`

```bash
docker compose -f docker/docker-compose.dev-db.yml up -d
```

- **Postgres (TimescaleDB)** on host port **5433** (pinned to match `backend/.env`;
  avoids clashing with a host Postgres on 5432). The init scripts create both
  `health_assistant` and `health_assistant_test` (the latter is needed by pytest).
- **Redis** on host port **6379** (Celery broker + Stage 2 OAuth state store).

If you already run Redis on the host (port 6379 busy), start Postgres only:

```bash
docker compose -f docker/docker-compose.dev-db.yml up -d postgres-dev1
```

First run creates the test DB automatically. For an existing volume, create it
once manually:

```bash
docker compose -f docker/docker-compose.dev-db.yml exec -T postgres-dev1 \
  psql -U admin -d health_assistant <<'SQL'
SELECT 'CREATE DATABASE health_assistant_test OWNER admin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'health_assistant_test')\gexec
SQL
```

### Local FHIR server (Stage 2 testing) — `docker-compose.fhir.yml`

```bash
docker compose -f docker/docker-compose.fhir.yml up -d
# FHIR base URL -> http://localhost:${HAPI_PORT:-8080}/fhir
# (8095 in the default docker/.env)
```

A local **HAPI FHIR R4** server for offline testing of the FHIR pull path
(`fhir_search` + `fhir_observation_to_create`) against real FHIR search,
pagination, and `OperationOutcome`. It does **not** serve SMART-on-FHIR OAuth —
for the full connect round-trip use the hosted SMART Health IT sandbox
(`https://r4.smarthealthit.org`); see `integrations/fhir_server/README.md`.

Verify it's up: `curl http://localhost:${HAPI_PORT:-8080}/fhir/metadata | head`


## Dockerfile (Backend)

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create uploads directory
RUN mkdir -p /app/uploads

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Dockerfile (Worker)

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create uploads directory
RUN mkdir -p /app/uploads

# Run worker
CMD ["celery", "-A", "app.workers.celery_app", "worker", "--loglevel=info"]
```

## Environment Variables (.env)

```env
# Application
APP_ENV=development
DEBUG=true
SECRET_KEY=your-secret-key-here

# PostgreSQL
POSTGRES_DB=health_assistant
POSTGRES_USER=admin
POSTGRES_PASSWORD=your-password
POSTGRES_PORT=5432

# Redis
REDIS_PORT=6379

# Backend
BACKEND_PORT=8000

# Frontend
FRONTEND_PORT=3000

# Flower (Celery monitoring)
FLOWER_PORT=5555

# AI/OCR
OPENAI_API_KEY=your-openai-key-here
OCR_PROVIDER=openai

# Email (optional)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=noreply@health-assistant.local

# Security
JWT_EXPIRATION_HOURS=24
```

## Docker Commands

### Build Images

```bash
# Build all services
docker compose --env-file .env -f docker/docker-compose.yml build

# Build specific service
docker compose --env-file .env -f docker/docker-compose.yml build backend
docker compose --env-file .env -f docker/docker-compose.yml build frontend
```

### Start Services

```bash
# Start all services
docker compose --env-file .env -f docker/docker-compose.yml up -d

# Start with rebuild
docker compose --env-file .env -f docker/docker-compose.yml up -d --build

# View logs
docker compose --env-file .env -f docker/docker-compose.yml logs -f

# View logs for specific service
docker compose --env-file .env -f docker/docker-compose.yml logs -f backend
docker compose --env-file .env -f docker/docker-compose.yml logs -f worker
```

### Stop Services

```bash
# Stop all services
docker compose --env-file .env -f docker/docker-compose.yml down

# Stop and remove volumes
docker compose --env-file .env -f docker/docker-compose.yml down -v
```

### Run Migrations

```bash
# Run database migrations
docker compose --env-file .env -f docker/docker-compose.yml exec backend alembic upgrade head

# Create new migration
docker compose --env-file .env -f docker/docker-compose.yml exec backend alembic revision -m "migration name"
```

### Execute Commands

```bash
# Access backend shell
docker compose --env-file .env -f docker/docker-compose.yml exec backend bash

# Access PostgreSQL
docker compose --env-file .env -f docker/docker-compose.yml exec postgres psql -U admin -d health_assistant

# Access Redis
docker compose --env-file .env -f docker/docker-compose.yml exec redis redis-cli


# Create admin user
python backend/scripts/create_system_admin.py --email admin@example.local --password securepassword
```

### Update Services

```bash
# Pull latest images
docker compose --env-file .env -f docker/docker-compose.yml pull

# Update and restart
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
```

## Production Deployment

### Docker Compose Production

```yaml
version: '3.8'

services:
  postgres:
    # ... (same as development)
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./production-init.sql:/docker-entrypoint-initdb.d/init.sql
    restart: unless-stopped

  redis:
    # ... (same as development)
    restart: unless-stopped

  backend:
    # ... (same as development)
    environment:
      APP_ENV: production
      DEBUG: "false"
    restart: unless-stopped

  frontend:
    # ... (same as development)
    restart: unless-stopped

  worker:
    # ... (same as development)
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  uploads:
```

### Docker Swarm

```bash
# Initialize swarm
docker swarm init

# Deploy stack
docker stack deploy -c docker-compose.yml health_assistant

# View services
docker service ls

# View logs
docker service logs health_assistant_backend
```

## Troubleshooting

### Database Connection Issues

```bash
# Check if PostgreSQL is running
docker compose --env-file .env -f docker/docker-compose.yml ps

# Check logs
docker compose --env-file .env -f docker/docker-compose.yml logs postgres

# Connect to PostgreSQL
docker compose --env-file .env -f docker/docker-compose.yml exec postgres psql -U admin -d health_assistant
```

### Redis Connection Issues

```bash
# Check if Redis is running
docker compose --env-file .env -f docker/docker-compose.yml ps

# Test Redis connection
docker compose --env-file .env -f docker/docker-compose.yml exec redis redis-cli ping
```

### Application Logs

```bash
# View backend logs
docker compose --env-file .env -f docker/docker-compose.yml logs backend

# Follow logs
docker compose --env-file .env -f docker/docker-compose.yml logs -f backend
```

### Restart Services

```bash
# Restart specific service
docker compose --env-file .env -f docker/docker-compose.yml restart backend

# Restart all services
docker compose --env-file .env -f docker/docker-compose.yml restart
```