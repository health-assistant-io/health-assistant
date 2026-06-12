# Health Assistant - Docker Setup

## Quick Start

```bash
cd docker
cp .env.example .env
# Edit .env with your configuration
docker-compose up -d
```


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
docker-compose build

# Build specific service
docker-compose build backend
docker-compose build frontend
```

### Start Services

```bash
# Start all services
docker-compose up -d

# Start with rebuild
docker-compose up -d --build

# View logs
docker-compose logs -f

# View logs for specific service
docker-compose logs -f backend
docker-compose logs -f worker
```

### Stop Services

```bash
# Stop all services
docker-compose down

# Stop and remove volumes
docker-compose down -v
```

### Run Migrations

```bash
# Run database migrations
docker-compose exec backend alembic upgrade head

# Create new migration
docker-compose exec backend alembic revision -m "migration name"
```

### Execute Commands

```bash
# Access backend shell
docker-compose exec backend bash

# Access PostgreSQL
docker-compose exec postgres psql -U admin -d health_assistant

# Access Redis
docker-compose exec redis redis-cli

# Create admin user
python backend/scripts/create_system_admin.py --email admin@example.local --password securepassword
```

### Update Services

```bash
# Pull latest images
docker-compose pull

# Update and restart
docker-compose up -d --build
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
docker-compose ps

# Check logs
docker-compose logs postgres

# Connect to PostgreSQL
docker-compose exec postgres psql -U admin -d health_assistant
```

### Redis Connection Issues

```bash
# Check if Redis is running
docker-compose ps

# Test Redis connection
docker-compose exec redis redis-cli ping
```

### Application Logs

```bash
# View backend logs
docker-compose logs backend

# Follow logs
docker-compose logs -f backend
```

### Restart Services

```bash
# Restart specific service
docker-compose restart backend

# Restart all services
docker-compose restart
```