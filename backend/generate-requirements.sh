#!/bin/bash

# Generate requirements.txt with latest versions (without pinning)
# Core dependencies only - no heavy AI/ML packages

cat > requirements.txt << 'EOF'
# FastAPI and server
fastapi
uvicorn[standard]
python-multipart
starlette

# Database
sqlalchemy
asyncpg
alembic
psycopg2-binary
sqlalchemy-utils

# Redis and task queue
redis
celery
flower

# Core AI/OCR (lightweight)
openai
pytesseract
pillow

# Authentication and security
python-jose[cryptography]
passlib[bcrypt]
python-dotenv
bcrypt
itsdangerous

# Email
fastapi-mail
aiofiles
jinja2

# File handling
python-magic

# Testing
pytest
pytest-asyncio
pytest-cov
httpx
faker

# Utilities
python-dateutil
pytz
pydantic[email]
uvloop

# Monitoring
prometheus-client
structlog

# WebSocket support
websockets

# Pydantic settings
pydantic-settings
EOF

echo "requirements.txt generated with core dependencies only"
echo "Run: pip install -r requirements.txt"
echo ""
echo "For optional local AI/OCR processing, also run:"
echo "pip install -r requirements.optional.txt"