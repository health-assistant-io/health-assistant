"""Lifespan-held LangGraph checkpoint store (``AsyncPostgresSaver``).

Phase 3.1 scaffold, per the Phase 3.0 spike rulings:

- the saver's connection pool is opened ONCE per process, in the FastAPI
  lifespan — never per request (guidelines §4);
- ``setup()`` runs at boot (idempotent ``CREATE TABLE IF NOT EXISTS``;
  Alembic-managed DDL is deliberately deferred);
- **drain-before-close**: :meth:`CheckpointStore.close` waits for in-flight
  graph runs (tracked via :meth:`CheckpointStore.run`) before closing the
  pool, so a trailing background checkpoint write is never cancelled
  (spike finding: closing right after a run can abort
  ``_checkpointer_put_after_previous``).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from contextlib import AsyncExitStack
from typing import AsyncIterator, Optional

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings

logger = logging.getLogger(__name__)

_DRAIN_POLL_SECONDS = 0.1


def get_checkpoint_conninfo() -> str:
    """Libpq conninfo for the checkpoint tables (same DB as the app)."""
    return (
        f"host={settings.POSTGRES_HOST} "
        f"port={settings.POSTGRES_PORT} "
        f"user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD} "
        f"dbname={settings.POSTGRES_DB}"
    )


class CheckpointStore:
    """Owns the process-wide ``AsyncPostgresSaver`` pool.

    Usage (FastAPI lifespan)::

        store = CheckpointStore()
        await store.open()
        app.state.checkpoint_store = store
        ...
        await store.close()

    Graph runs must execute inside :meth:`run` so shutdown can drain them.
    """

    def __init__(self, conninfo: Optional[str] = None, drain_timeout: float = 30.0):
        self._conninfo = conninfo or get_checkpoint_conninfo()
        self._drain_timeout = drain_timeout
        self._stack: Optional[AsyncExitStack] = None
        self._saver: Optional[AsyncPostgresSaver] = None
        self._in_flight_runs = 0

    @property
    def saver(self) -> AsyncPostgresSaver:
        if self._saver is None:
            raise RuntimeError("CheckpointStore is not open")
        return self._saver

    @property
    def in_flight_runs(self) -> int:
        return self._in_flight_runs

    async def open(self) -> None:
        """Open the pool and create/verify the checkpoint tables (idempotent)."""
        if self._saver is not None:
            return
        stack = AsyncExitStack()
        saver = await stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(self._conninfo)
        )
        await saver.setup()
        self._stack = stack
        self._saver = saver
        logger.info("Checkpoint store open (checkpoint tables ready)")

    async def close(self) -> None:
        """Drain in-flight graph runs, then close the pool."""
        if self._saver is None:
            return
        waited = 0.0
        while self._in_flight_runs > 0 and waited < self._drain_timeout:
            await asyncio.sleep(_DRAIN_POLL_SECONDS)
            waited += _DRAIN_POLL_SECONDS
        if self._in_flight_runs > 0:
            logger.error(
                "Checkpoint drain timeout: %d graph run(s) still in flight after "
                "%.0fs — closing the pool anyway; their final checkpoint writes "
                "may be lost.",
                self._in_flight_runs,
                self._drain_timeout,
            )
        else:
            logger.info("Checkpoint store drained (%.1fs), closing pool", waited)
        stack = self._stack
        self._stack = None
        self._saver = None
        assert stack is not None
        await stack.aclose()

    @asynccontextmanager
    async def run(self) -> AsyncIterator[AsyncPostgresSaver]:
        """Scope a graph run: refcounted while the block executes."""
        self._in_flight_runs += 1
        try:
            yield self.saver
        finally:
            self._in_flight_runs -= 1
