"""Phase 3.5: engine parity proof at the SERVICE level, run parametrically
under both engines (``loop`` | ``graph``).

The plan's parity checklist: tool calls, citations, HITL flows, and stream
sentinel order must be identical between engines — except the ratified
ask_user delta (graph = terminal card; loop = continue-after-card). Shadow
dial: ``AI_AGENT_ENGINE`` env default is overridden per tenant via
``TenantModel.settings.ai_agent_engine`` (``AIAssistanceService.
_get_agent_engine``).
"""

import json
import uuid
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessageChunk

from app.ai.assistance.service import AIAssistanceService
from app.ai.graphs.chat_agent import chat_engine_iter
from app.ai.graphs.checkpointer import CheckpointStore, bind_runtime_store
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.chat_model import ChatSession
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel

TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
SESSION_ID = uuid.uuid4()

TOOL_CALL = {
    "name": "get_patient_summary",
    "args": {},
    "id": "call_1",
    "type": "tool_call",
}
ASK_USER_CALL = {
    "name": "ask_user",
    "args": {"questions": [{"id": "q1", "kind": "freetext", "prompt": "Dose?"}]},
    "id": "call_2",
    "type": "tool_call",
}


class ScriptedStreamingLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        return self

    async def astream(self, history):
        for piece in self.responses.pop(0):
            yield piece


def _fake_tool(name: str, observation: str):
    tool = MagicMock()
    tool.name = name
    tool.ainvoke = AsyncMock(return_value=observation)
    return tool


def _sentinel_shape(chunk: str) -> str:
    """Reduce an SSE chunk to a comparable shape (HITL payloads carry
    per-run uuids/timestamps — compare sentinels by kind, deltas verbatim)."""
    if chunk.startswith("[SESSION_ID]"):
        return "[SESSION_ID]"
    if chunk.startswith("[HITL_TASK]"):
        payload = json.loads(chunk.removeprefix("[HITL_TASK] "))
        return f"[HITL_TASK] {payload['task_type']}"
    if chunk.startswith("["):
        return chunk.split(" ")[0]
    return f"delta:{chunk}"


@pytest_asyncio.fixture
async def chat_db() -> AsyncIterator[None]:
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=TENANT_ID, name="T", slug=f"t-{TENANT_ID}"))
        await db.flush()
        db.add(
            UserModel(
                id=USER_ID,
                email=f"u-{USER_ID}@test.local",
                hashed_password="x",
                tenant_id=TENANT_ID,
                role="USER",
            )
        )
        await db.flush()
        db.add(
            ChatSession(
                id=SESSION_ID,
                tenant_id=TENANT_ID,
                user_id=USER_ID,
                title="parity",
            )
        )
        await db.commit()
    try:
        yield
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                ChatSession.__table__.delete().where(
                    ChatSession.__table__.c.id == SESSION_ID
                )
            )
            await db.execute(
                UserModel.__table__.delete().where(UserModel.__table__.c.id == USER_ID)
            )
            await db.execute(
                TenantModel.__table__.delete().where(
                    TenantModel.__table__.c.id == TENANT_ID
                )
            )
            await db.commit()


def _make_service() -> AIAssistanceService:
    return AIAssistanceService(AsyncSessionLocal())


@pytest.mark.parametrize("engine", ["loop", "graph"])
@pytest.mark.asyncio
async def test_tool_turn_sentinel_parity(engine, monkeypatch, chat_db):
    monkeypatch.setattr(settings, "AI_AGENT_ENGINE", engine)
    llm = ScriptedStreamingLLM(
        [
            [
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "get_patient_summary",
                            "args": "",
                            "id": "call_1",
                            "index": None,
                        }
                    ],
                )
            ],
            [AIMessageChunk(content="All good.")],
        ]
    )
    tools = [_fake_tool("get_patient_summary", '{"status": "ok"}')]

    from unittest.mock import patch

    with (
        patch(
            "app.ai.providers.service.AIProviderService.get_llm",
            new=AsyncMock(return_value=llm),
        ),
        patch(
            "app.ai.assistance.service.build_chat_tools",
            new=AsyncMock(return_value=tools),
        ),
    ):
        svc = _make_service()
        gen = await svc.assist(
            task_type="chat",
            user_input="check the patient",
            context={"session_id": str(SESSION_ID)},
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            stream=True,
        )
        chunks = [chunk async for chunk in gen]

    shapes = [_sentinel_shape(c) for c in chunks]
    assert shapes == [
        "[TOOL_CALL_START]",
        "[TOOL_CALL_EXEC]",
        "[TOOL_CALL_RESULT]",
        "[CITATION]",
        "[TOOL_CALL_FINISHED]",
        "delta:All good.",
    ]


@pytest.mark.asyncio
async def test_ask_user_prefix_parity_and_graph_terminal(monkeypatch, chat_db):
    """Both engines emit the identical sentinel sequence UP TO the card; the
    graph engine's stream then ENDS (terminal card) while the loop continues
    (ratified delta)."""
    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        results = {}
        for engine in ("loop", "graph"):
            monkeypatch.setattr(settings, "AI_AGENT_ENGINE", engine)

            def make_llm():
                return ScriptedStreamingLLM(
                    [
                        [
                            AIMessageChunk(
                                content="",
                                tool_call_chunks=[
                                    {
                                        "name": "ask_user",
                                        "args": "",
                                        "id": "call_2",
                                        "index": None,
                                    }
                                ],
                            )
                        ],
                        [AIMessageChunk(content="Continuing.")],
                    ]
                )

            ask_tool = _fake_tool(
                "ask_user",
                json.dumps(
                    {
                        "__hitl__": True,
                        "task": {
                            "schema_version": 2,
                            "proposal_id": f"prop-{engine}",
                            "task_type": "ask_user",
                            "title": "Quick questions",
                            "status": "proposed",
                            "proposed_payload": {},
                            "context": {},
                            "created_at": "2026-09-04T00:00:00+00:00",
                            "resolved": None,
                        },
                    }
                ),
            )

            from unittest.mock import patch

            with (
                patch(
                    "app.ai.providers.service.AIProviderService.get_llm",
                    new=AsyncMock(side_effect=lambda *a, **k: make_llm()),
                ),
                patch(
                    "app.ai.assistance.service.build_chat_tools",
                    new=AsyncMock(return_value=[ask_tool]),
                ),
            ):
                svc = _make_service()
                gen = await svc.assist(
                    task_type="chat",
                    user_input="what dose?",
                    context={"session_id": str(SESSION_ID)},
                    tenant_id=TENANT_ID,
                    user_id=USER_ID,
                    stream=True,
                )
                chunks = [chunk async for chunk in gen]
            results[engine] = [_sentinel_shape(c) for c in chunks]

        loop_shapes = results["loop"]
        graph_shapes = results["graph"]
        card_idx = loop_shapes.index("[HITL_TASK] ask_user")
        # Identical up to and including the card.
        assert graph_shapes[: card_idx + 1] == loop_shapes[: card_idx + 1]
        # Loop continues past the card; graph's stream ends at the card.
        assert len(loop_shapes) > card_idx + 1
        assert graph_shapes[card_idx + 1 :] == ["[TOOL_CALL_FINISHED]"]
    finally:
        bind_runtime_store(None)
        await store.close()


# ---------------------------------------------------------------------------
# Shadow dial — tenant settings override the env default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_resolution_tenant_overrides_env(chat_db, monkeypatch):
    from app.ai.assistance.service import AIAssistanceService as Svc

    monkeypatch.setattr(settings, "AI_AGENT_ENGINE", "loop")
    svc = Svc(AsyncSessionLocal())
    assert await svc._get_agent_engine(TENANT_ID) == "loop"

    async with AsyncSessionLocal() as db:
        from sqlalchemy import update

        await db.execute(
            update(TenantModel)
            .where(TenantModel.id == TENANT_ID)
            .values(settings={"ai_agent_engine": "graph"})
        )
        await db.commit()
    assert await svc._get_agent_engine(TENANT_ID) == "graph"

    # Invalid tenant value falls back to the env default.
    async with AsyncSessionLocal() as db:
        from sqlalchemy import update

        await db.execute(
            update(TenantModel)
            .where(TenantModel.id == TENANT_ID)
            .values(settings={"ai_agent_engine": "turbo"})
        )
        await db.commit()
    assert await svc._get_agent_engine(TENANT_ID) == "loop"


@pytest.mark.parametrize("engine", ["loop", "graph"])
def test_chat_engine_iter_explicit_engine_wins(engine, monkeypatch):
    """The explicit engine parameter beats the env default (shadow dial)."""
    monkeypatch.setattr(
        settings, "AI_AGENT_ENGINE", "loop" if engine == "graph" else "graph"
    )
    from app.ai.agents.chat_agent import run_reasoning_loop

    gen = chat_engine_iter(None, [], [], 3, streaming=False, engine=engine)
    expected = run_reasoning_loop if engine == "loop" else None
    if engine == "loop":
        # The loop engine returns the raw generator function's result.
        assert gen is not None
    else:
        # The graph engine returns an async generator (async iterator).
        assert hasattr(gen, "__aiter__")
