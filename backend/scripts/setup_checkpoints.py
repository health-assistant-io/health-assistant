#!/usr/bin/env python3
"""Create the langgraph checkpoint tables (owner-side, one-shot).

Run by the migrate service right after ``alembic upgrade head`` (see
docker-compose.prod.yml / docker-compose.standalone.yml). The runtime role
(neuronection_health_app) is DML-only and cannot execute the DDL that
``CheckpointStore.open()`` / ``saver.setup()`` performs at boot — and PG15+
no longer grants CREATE on the public schema to non-owners — so the
checkpoint tables are prepared here, as the owner, once per deploy.

Idempotent (CREATE TABLE IF NOT EXISTS under the hood); the runtime role's
DML on the created tables is covered by init-roles.sh's
ALTER DEFAULT PRIVILEGES.

Usage (from backend/, with the owner POSTGRES_* env):
    python scripts/setup_checkpoints.py
"""
import asyncio

from app.ai.graphs.checkpointer import CheckpointStore


async def _main() -> None:
    store = CheckpointStore()
    await store.open()
    await store.close()


if __name__ == "__main__":
    asyncio.run(_main())
    print("checkpoint tables ready (owner)")
