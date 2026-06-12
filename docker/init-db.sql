-- Health Assistant Database Initialization
-- Note: Most of the schema is managed by Alembic migrations.
-- This script handles environment-level setup that must exist before the app starts.

-- Enable TimescaleDB extension for time-series data (wearable metrics)
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Enable pgcrypto for UUID generation and encryption functions
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Optional: Create additional schemas or roles if required in the future
-- CREATE SCHEMA IF NOT EXISTS clinical;
