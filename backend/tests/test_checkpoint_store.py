"""Tests for the LangGraph checkpoint store (Phase 3.1 scaffold).

Pins the spike ruling (b) mechanics: one lifespan-held pool, boot-time
idempotent ``setup()``, and **drain-before-close** — ``close()`` must wait
for in-flight graph runs so a trailing background checkpoint write is never
cancelled when the process shuts down.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.ai.graphs.checkpointer import CheckpointStore


@pytest_asyncio.fixture
async def store():
    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    try:
        yield store
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_open_creates_checkpoint_tables(store):
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename LIKE 'checkpoint%' ORDER BY 1"
                )
            )
        ).scalars()
        names = set(rows)
    assert {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    } <= names


@pytest.mark.asyncio
async def test_open_is_idempotent(store):
    saver_before = store.saver
    await store.open()
    assert store.saver is saver_before


@pytest.mark.asyncio
async def test_close_without_open_is_noop():
    store = CheckpointStore()
    await store.close()
    with pytest.raises(RuntimeError):
        _ = store.saver


@pytest.mark.asyncio
async def test_run_tracks_in_flight_runs(store):
    async with store.run():
        assert store.in_flight_runs == 1
        async with store.run():
            assert store.in_flight_runs == 2
        assert store.in_flight_runs == 1
    assert store.in_flight_runs == 0


@pytest.mark.asyncio
async def test_close_drains_in_flight_run_before_closing():
    store = CheckpointStore(drain_timeout=5.0)
    await store.open()
    try:
        run_cm = store.run()
        await run_cm.__aenter__()
        assert store.in_flight_runs == 1

        closer = asyncio.create_task(store.close())
        await asyncio.sleep(0.2)
        # Drain-before-close: the pool must still be open while a run is live.
        assert not closer.done()
        assert store.saver is not None

        await run_cm.__aexit__(None, None, None)
        await asyncio.wait_for(closer, timeout=2.0)
        assert store.in_flight_runs == 0
        with pytest.raises(RuntimeError):
            _ = store.saver
    finally:
        if store._saver is not None:
            await store.close()


@pytest.mark.asyncio
async def test_checkpoint_roundtrip_via_store(store):
    """A checkpoint written through the store's saver is readable back."""
    config = {"configurable": {"thread_id": f"store-test-{uuid.uuid4()}"}}
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import RetryPolicy  # noqa: F401
    from typing import TypedDict

    class S(TypedDict):
        n: int

    def node(state: S):
        return {"n": state["n"] + 1}

    graph = (
        StateGraph(S)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile(checkpointer=store.saver)
    )
    out = await graph.ainvoke({"n": 0}, config)
    assert out == {"n": 1}
    snap = await graph.aget_state(config)
    assert snap.values["n"] == 1
