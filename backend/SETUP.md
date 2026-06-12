# Health Assistant - Backend Setup

## Installation

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

```bash
cp .env.example .env
# Edit .env with your configuration
```

## Database Setup

```bash
# Run migrations
alembic upgrade head

# Create a system administrator user
python scripts/create_system_admin.py --email admin@example.local --password securepassword
```

## Development Server

```bash
# Start backend
uvicorn app.main:app --reload

# Start worker
celery -A app.workers.celery_app worker --loglevel=info

# Start flower (monitoring)
celery -A app.workers.celery_app flower --port=5555
```

## Testing

```bash
pytest tests/
```