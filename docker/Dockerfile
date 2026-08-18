FROM python:3.12-slim AS base

WORKDIR /app

# Runtime system dependencies (OCR/PDF tooling; no compilers in the final image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    tesseract-ocr \
    libleptonica-dev \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

FROM base AS deps
# Build stage: compilers live here only, never in the runtime layer.
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS runtime
# Copy only the installed packages from the deps stage.
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Application code — allowlisted paths only (.dockerignore guards the rest).
COPY backend/app /app/backend/app
COPY backend/alembic /app/backend/alembic
COPY backend/scripts /app/backend/scripts
COPY backend/requirements.txt backend/pytest.ini /app/backend/
COPY integrations /app/integrations

# Non-root runtime user (audit 2026-08 CFG-M5).
RUN useradd --create-home --uid 10001 ha \
    && mkdir -p /app/uploads \
    && chown -R ha:ha /app
USER ha

ENV PYTHONPATH=/app/backend:/app
WORKDIR /app/backend

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
