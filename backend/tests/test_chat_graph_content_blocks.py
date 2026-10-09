"""Block-list chat content normalization in the graph engine.

LangChain message ``content`` has two shapes: plain ``str`` and the
block-list form (``[{"type": "text", "text": ...}, ...]``) emitted by
Anthropic-style providers and OpenAI-compatible gateways. ``agent_step``
concatenated ``chunk.content`` raw — a list-content chunk crashed the turn
with ``TypeError: can only concatenate str (not "list") to str`` (seen live
2026-10-10). These tests pin the normalization: text blocks stream through
as deltas, thinking/tool_use/image blocks contribute nothing, and the
persisted ``content.text`` is the joined visible text.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.ai.graphs.chat_agent import chat_engine_iter
from app.ai.graphs.checkpointer import CheckpointStore, bind_runtime_store

SESSION_ID = "77777777-7777-7777-7777-777777777777"
USER_ID = "55555555-5555-5555-5555-555555555555"
TENANT_ID = "66666666-6666-6666-6666-666666666666"


class BlockListLLM:
    """Streams chunks whose content is the block-list form; `ainvoke`
    returns the same shape (non-streaming path)."""

    def __init__(self, chunks):
        self.chunks = chunks

    async def astream(self, history):
        for chunk in self.chunks:
            yield chunk

    async def ainvoke(self, history):
        return AIMessage(
            content=[
                {"type": "thinking", "thinking": "internal reasoning"},
                {"type": "text", "text": "Non-streamed block answer."},
            ]
        )


def _blocks(*texts: str) -> list:
    return [{"type": "text", "text": text} for text in texts]


def _service():
    svc = MagicMock()
    svc.save_message = AsyncMock(side_effect=lambda **kw: MagicMock(id="msg-1"))
    svc.update_message_fields = AsyncMock(return_value=None)
    svc.find_message_by_proposal = AsyncMock(return_value=MagicMock(id="msg-1"))
    return svc


async def _collect(gen):
    return [event async for event in gen]


@pytest.mark.asyncio
async def test_streaming_block_list_content_deltas_and_persist():
    llm = BlockListLLM(
        [
            AIMessageChunk(
                content=[
                    {"type": "thinking", "thinking": "let me check"},
                    {"type": "text", "text": "Based on "},
                ]
            ),
            AIMessageChunk(content=_blocks("the labs, ", "all is well.")),
            AIMessageChunk(
                content=[
                    {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
                    {"type": "text", "text": ""},
                ]
            ),
        ]
    )
    svc = _service()

    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        events = await _collect(
            chat_engine_iter(
                llm,
                tools=[],
                history=[HumanMessage("how do my labs look?")],
                max_iterations=5,
                streaming=True,
                chat_session_service=svc,
                session_id=SESSION_ID,
                user_id=USER_ID,
                tenant_id=TENANT_ID,
            )
        )
    finally:
        bind_runtime_store(None)
        await store.close()

    deltas = [e["text"] for e in events if e["event"] == "delta"]
    # A chunk's blocks join into ONE delta ("the labs, " + "all is well."
    # arrive in a single chunk); thinking/tool_use blocks contribute nothing.
    assert deltas == ["Based on ", "the labs, all is well."]
    assert events[-1]["event"] == "flow_finished"

    saved = [
        call for call in svc.save_message.await_args_list if call.kwargs.get("role") == "assistant"
    ]
    assert saved, "assistant turn never persisted"
    assert saved[-1].kwargs["content"]["text"] == ("Based on the labs, all is well.")


@pytest.mark.asyncio
async def test_non_streaming_block_list_content():
    llm = BlockListLLM([])
    svc = _service()

    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        events = await _collect(
            chat_engine_iter(
                llm,
                tools=[],
                history=[HumanMessage("summarize")],
                max_iterations=5,
                streaming=False,
                chat_session_service=svc,
                session_id=SESSION_ID,
                user_id=USER_ID,
                tenant_id=TENANT_ID,
            )
        )
    finally:
        bind_runtime_store(None)
        await store.close()

    deltas = [e["text"] for e in events if e["event"] == "delta"]
    assert deltas == ["Non-streamed block answer."]
    saved = [
        call for call in svc.save_message.await_args_list if call.kwargs.get("role") == "assistant"
    ]
    assert saved, "assistant turn never persisted"
    assert saved[-1].kwargs["content"]["text"] == "Non-streamed block answer."
