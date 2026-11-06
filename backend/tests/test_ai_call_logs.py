"""Per-AI-call audit: one ``ai_call_logs`` row per model call (plan 4 W3).

The audit is attached at the ONE choke point (the LangChain model factory) and
at the decision funnel; these tests pin the chat-model seam:

* one row per model call (not per turn) — a tool-calling loop writes one row per
  invocation;
* tokens/status/route/task/provider/model are recorded, latency is measured;
* failures are classified into the closed status vocabulary with a typed
  ``error_class`` (never a message dump);
* the write is fail-soft — an unknown task or a database hiccup never breaks the
  model call;
* the ``/ai-config/usage`` surface aggregates the table per task, tenant-scoped
  and admin-only.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from sqlalchemy import delete, select

from app.ai.call_audit import (
    CallAuditContext,
    attach_call_audit,
    classify_error,
    record_call,
)
from app.core.database import AsyncSessionLocal
from app.core.security import get_current_user
from app.main import app
from app.models.ai_provider_model import AIModel, AIProviderModel, AIScope
from app.models.ai_task_model import AICallLog
from app.models.enums import AICallRoute, AICallStatus, Role
from app.models.tenant_model import TenantModel
from app.schemas.user import TokenData


class _UsageModel(BaseChatModel):
    """A chat model that reports usage and can fail on demand."""

    model_name: str = "audit-fake"
    fail: bool = False

    @property
    def _llm_type(self) -> str:
        return "audit-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.fail:
            raise TimeoutError("upstream timed out")
        return ChatResult(generations=[ChatGeneration(message=self._reply())])

    def _reply(self) -> AIMessage:
        return AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        )

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        yield ChatGenerationChunk(message=AIMessageChunk(content="o"))
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="k",
                usage_metadata={
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "total_tokens": 18,
                },
            )
        )


@pytest_asyncio.fixture
async def log_ctx():
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tenant_id, name="Audit Tenant", slug=f"au-{tenant_id}"))
        await session.flush()
        provider = AIProviderModel(
            name="Audit Provider",
            scope=AIScope.TENANT,
            tenant_id=tenant_id,
            provider_type="openai",
            api_base="https://api.openai.com/v1",
            is_active=True,
            settings={},
        )
        session.add(provider)
        await session.flush()
        model = AIModel(
            provider_id=provider.id,
            name="audit-model",
            model_name="audit-model",
            capabilities=["text"],
            is_active=True,
            settings={"cost_per_1k_input_micros": 2000, "cost_per_1k_output_micros": 4000},
        )
        session.add(model)
        await session.commit()
        await session.refresh(provider)
        await session.refresh(model)
    ctx = {"tenant_id": tenant_id, "provider": provider, "model": model}
    yield ctx
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AICallLog).where(AICallLog.tenant_id == tenant_id))
        await session.execute(delete(AIModel).where(AIModel.provider_id == provider.id))
        await session.execute(delete(AIProviderModel).where(AIProviderModel.tenant_id == tenant_id))
        await session.commit()


def _ctx(log_ctx, task_id: str = "chat") -> CallAuditContext:
    return CallAuditContext(
        task_id=task_id,
        tenant_id=log_ctx["tenant_id"],
        user_id=uuid.uuid4(),
        provider_id=log_ctx["provider"].id,
        model_id=log_ctx["model"].id,
        model_name=log_ctx["model"].model_name,
        cost_per_1k_input_micros=2000,
        cost_per_1k_output_micros=4000,
    )


async def _rows(tenant_id) -> list[AICallLog]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AICallLog).where(AICallLog.tenant_id == tenant_id).order_by(AICallLog.started_at)
        )
        return list(result.scalars().all())


async def test_one_model_call_writes_one_audited_row(log_ctx):
    model = attach_call_audit(_UsageModel(), _ctx(log_ctx))
    await model.ainvoke([HumanMessage(content="hello")])

    rows = await _rows(log_ctx["tenant_id"])
    assert len(rows) == 1
    row = rows[0]
    assert row.task_type_id is not None
    assert row.route is AICallRoute.CHAT
    assert row.status is AICallStatus.OK
    assert row.provider_id == log_ctx["provider"].id
    assert row.model_id == log_ctx["model"].id
    assert row.prompt_tokens == 11
    assert row.completion_tokens == 7
    assert row.latency_ms >= 0
    assert row.cost_micros == round(11 * 2000 / 1000) + round(7 * 4000 / 1000)
    assert row.meta_schema_version == 1
    assert row.meta["model_name"] == "audit-model"
    assert row.trace_id


async def test_a_tool_loop_writes_one_row_per_call(log_ctx):
    model = attach_call_audit(_UsageModel(), _ctx(log_ctx))
    for _ in range(3):
        await model.ainvoke([HumanMessage(content="step")])
    assert len(await _rows(log_ctx["tenant_id"])) == 3


async def test_streaming_calls_are_audited_with_the_stream_flag(log_ctx):
    model = attach_call_audit(_UsageModel(), _ctx(log_ctx))
    async for _chunk in model.astream([HumanMessage(content="stream me")]):
        pass

    rows = await _rows(log_ctx["tenant_id"])
    assert len(rows) == 1
    assert rows[0].status is AICallStatus.OK
    assert rows[0].meta["streamed"] is True


async def test_failures_are_classified_not_dumped(log_ctx):
    model = attach_call_audit(_UsageModel(fail=True), _ctx(log_ctx))
    with pytest.raises(TimeoutError):
        await model.ainvoke([HumanMessage(content="boom")])

    rows = await _rows(log_ctx["tenant_id"])
    assert len(rows) == 1
    assert rows[0].status is AICallStatus.TIMEOUT
    assert rows[0].error_class == "TimeoutError"
    assert rows[0].prompt_tokens is None


def test_classify_error_maps_the_status_vocabulary():
    assert classify_error(TimeoutError("x"))[0] is AICallStatus.TIMEOUT
    assert classify_error(ValueError("bad input"))[0] is AICallStatus.ERROR
    assert classify_error(ValueError("bad input"))[1] == "ValueError"
    from starlette.requests import ClientDisconnect

    assert classify_error(ClientDisconnect())[0] is AICallStatus.CANCELLED
    assert classify_error(asyncio.CancelledError())[0] is AICallStatus.CANCELLED


async def test_audit_is_fail_soft_for_an_unknown_task():
    ok = await record_call(
        CallAuditContext(task_id="not_a_task"),
        started_at=datetime.now(UTC),
        latency_ms=1,
        status=AICallStatus.OK,
    )
    assert ok is False


async def test_usage_endpoint_aggregates_per_task(log_ctx, async_client: AsyncClient):
    model = attach_call_audit(_UsageModel(), _ctx(log_ctx, task_id="chat"))
    await model.ainvoke([HumanMessage(content="one")])
    await model.ainvoke([HumanMessage(content="two")])
    ocr = attach_call_audit(_UsageModel(), _ctx(log_ctx, task_id="ocr"))
    await ocr.ainvoke([HumanMessage(content="three")])

    app.dependency_overrides[get_current_user] = lambda: TokenData(
        user_id=uuid.uuid4(),
        sub="admin@example.com",
        role=Role.ADMIN.value,
        tenant_id=log_ctx["tenant_id"],
    )
    try:
        response = await async_client.get("/api/v1/ai-config/usage?window_days=7")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window_days"] == 7
    assert body["total_calls"] == 3
    by_task = {row["task_type"]: row for row in body["tasks"]}
    assert set(by_task) == {"chat", "ocr"}
    assert by_task["chat"]["calls"] == 2
    assert by_task["chat"]["prompt_tokens"] == 22
    assert by_task["chat"]["cost_micros"] == 2 * (round(11 * 2000 / 1000) + round(7 * 4000 / 1000))


async def test_usage_endpoint_is_admin_only(log_ctx, async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: TokenData(
        user_id=uuid.uuid4(),
        sub="user@example.com",
        role=Role.USER.value,
        tenant_id=log_ctx["tenant_id"],
    )
    try:
        response = await async_client.get("/api/v1/ai-config/usage")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


async def test_usage_endpoint_refuses_another_tenant(log_ctx, async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: TokenData(
        user_id=uuid.uuid4(),
        sub="admin@example.com",
        role=Role.ADMIN.value,
        tenant_id=log_ctx["tenant_id"],
    )
    try:
        response = await async_client.get(f"/api/v1/ai-config/usage?tenant_id={uuid.uuid4()}")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_route_vocabulary_covers_the_task_registry():
    """Every registry route is a closed DB vocabulary value, and every
    vocabulary value has a producing task (rule 7a: CHECK ≡ enum ≡ users)."""
    from app.ai.providers.task_registry import TASKS

    routes = {AICallRoute(task.route.value) for task in TASKS}
    assert routes == set(AICallRoute)
