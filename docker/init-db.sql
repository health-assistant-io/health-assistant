-- Health Assistant Database Initialization
-- Note: Most of the schema is managed by Alembic migrations.
-- This script handles environment-level setup that must exist before the app starts.

-- Enable TimescaleDB for time-series data (wearable metrics). Availability-
-- checked like the baseline migration: the image is TimescaleDB-optional per
-- deployment.md, and a hard CREATE EXTENSION here would abort the whole
-- docker-entrypoint init chain (including init-roles.sh) on images without
-- the extension.
SELECT 'CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE'
WHERE EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb')\gexec

-- Enable pgcrypto for UUID generation and encryption functions (hard dep)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Enable pg_trgm for trigram fuzzy matching (catalog search) (hard dep)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
