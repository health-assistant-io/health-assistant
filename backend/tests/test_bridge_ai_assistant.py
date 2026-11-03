"""Bridge AI-assistant paths (R7 Phase 1, 2026-10-11) — DB-backed tests for
`GET /ai/sessions`, `GET /ai/sessions/{id}/messages`, `POST /ai/chat`
(non-streaming), `DELETE /ai/sessions/{id}`, and the HITL resolve path.
The LLM is patched out (`AIAssistanceService.assist`) — these tests pin the
routing, the owner/patient isolation envelope, and the resolve contract."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.ai.assistance import turns as turns_mod
from app.core.database import AsyncSessionLocal
from app.extensions.builtin.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from app.models.chat_model import ChatMessage, ChatSession
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from tests.test_turn_lock import FakeRedis


@pytest.fixture
def turn_redis(monkeypatch):
    """Swap the turn lock's Redis client for the in-memory fake.

    The shared async redis client pools connections per event loop; pytest
    gives every test a fresh loop, so a connection pooled by an earlier
    test (e.g. through the rate-limiter dependency) poisons later real-
    Redis calls ("attached to a different loop") — the turn lock would
    silently degrade open and can leave an orphaned key behind. The fake
    keeps these tests hermetic and deterministic.
    """
    fake = FakeRedis()
    monkeypatch.setattr(turns_mod, "redis_client", fake)
    return fake


def _post_request(payload: dict):
    from unittest.mock import MagicMock

    req = MagicMock()
    req.query_params = {}

    async def _json():
        return payload

    req.json = _json
    return req


def _get_request(query_params: dict | None = None):
    from unittest.mock import MagicMock

    req = MagicMock()
    req.query_params = query_params or {}
    return req


@pytest_asyncio.fixture
async def ai_ctx():
    """Tenant + owner user + PatientA/PatientB + integration bound to A +
    one chat session per patient (A's carrying a PROPOSED HITL card)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="AI bridge", slug=f"ai-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"ai-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_a,
                tenant_id=tenant_id,
                name={"family": "A", "given": ["Bound"]},
                gender="UNKNOWN",
            )
        )
        db.add(
            Patient(
                id=patient_b,
                tenant_id=tenant_id,
                name={"family": "B", "given": ["Other"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_a,
                provider="health_assistant_bridge",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.flush()
        session_a = ChatSession(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            patient_id=patient_a,
            title="Lipids questions",
        )
        session_b = ChatSession(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            patient_id=patient_b,
            title="Other patient",
        )
        db.add_all([session_a, session_b])
        await db.flush()
        message_a = uuid.uuid4()
        db.add(
            ChatMessage(
                id=message_a,
                session_id=session_a.id,
                role="assistant",
                content={"text": "Shall I log this as a clinical event?"},
                tasks=[
                    {
                        "proposal_id": "prop-1",
                        "task_type": "create_clinical_event",
                        "title": "Log hyperlipidemia follow-up",
                        "status": "proposed",
                        "proposed_payload": {"title": "Hyperlipidemia follow-up"},
                    }
                ],
                citations=[{"source": "biomarker:ldl", "label": "LDL trend"}],
            )
        )
        await db.commit()
        return {
            "integration_id": integration_id,
            "session_a": session_a.id,
            "session_b": session_b.id,
            "message_a": message_a,
        }


async def _load_integration(integration_id) -> UserIntegration:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        return res.scalar_one()


@pytest.mark.asyncio
async def test_ai_sessions_are_patient_scoped(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="ai/sessions",
        method="GET",
        request=_get_request(),
    )

    ids = [s["id"] for s in result["data"]]
    assert str(ctx["session_a"]) in ids
    assert str(ctx["session_b"]) not in ids
    assert result["data"][0]["title"] == "Lipids questions"


@pytest.mark.asyncio
async def test_ai_messages_return_tasks_and_isolation_holds(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path=f"ai/sessions/{ctx['session_a']}/messages",
        method="GET",
        request=_get_request(),
    )
    assert result["data"][0]["tasks"][0]["proposal_id"] == "prop-1"

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_b']}/messages",
            method="GET",
            request=_get_request(),
        )


@pytest.mark.asyncio
async def test_ai_chat_pins_the_bound_patient_and_threads_the_session(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    captured = {}

    async def fake_assist(self, **kwargs):
        captured.update(kwargs)
        session_id = kwargs["context"].get("session_id") or str(uuid.uuid4())
        return {"message": "Sure — here's the plan.", "session_id": session_id, "success": True}

    with patch("app.ai.assistance.service.AIAssistanceService.assist", new=fake_assist):
        result = await provider.handle_api_request(
            integration=integration,
            path="ai/chat",
            method="POST",
            request=_post_request(
                {"message": "Summarize my lipids", "session_id": str(ctx["session_a"])}
            ),
        )

    assert result["success"] is True
    assert result["message"] == "Sure — here's the plan."
    assert result["session_id"] == str(ctx["session_a"])
    assert captured["task_type"] == "chat"
    assert captured["stream"] is False
    assert captured["context"]["session_id"] == str(ctx["session_a"])

    # A fresh send (no session_id) still pins the patient context.
    with patch("app.ai.assistance.service.AIAssistanceService.assist", new=fake_assist):
        await provider.handle_api_request(
            integration=integration,
            path="ai/chat",
            method="POST",
            request=_post_request({"message": "New question"}),
        )
    assert captured["context"]["patient_id"] is not None
    assert "session_id" not in captured["context"]

    # The other patient's session must be rejected.
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="ai/chat",
            method="POST",
            request=_post_request({"message": "hi", "session_id": str(ctx["session_b"])}),
        )


@pytest.mark.asyncio
async def test_ai_chat_rejects_blank_and_oversized_messages(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="ai/chat",
            method="POST",
            request=_post_request({"message": "   "}),
        )
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="ai/chat",
            method="POST",
            request=_post_request({"message": "x" * 8001}),
        )
    # The shared _chat_turn_payload validator also fronts the stream twin.
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="ai/chat/stream",
            method="POST",
            request=_post_request({"message": "x" * 8001}),
        )


@pytest.mark.asyncio
async def test_ai_chat_returns_tasks_and_citations_of_latest_assistant_row(ai_ctx):
    """The rich turn: message_id/tasks/citations come from the session's
    latest assistant row — the engine saves it before `assist` returns, so
    the post-assist fetch (same db session, pre-commit) sees it."""
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async def fake_assist(self, **kwargs):
        return {
            "message": "Shall I log this as a clinical event?",
            "session_id": kwargs["context"]["session_id"],
            "success": True,
        }

    with patch("app.ai.assistance.service.AIAssistanceService.assist", new=fake_assist):
        result = await provider.handle_api_request(
            integration=integration,
            path="ai/chat",
            method="POST",
            request=_post_request(
                {"message": "Log my lipids", "session_id": str(ctx["session_a"])}
            ),
        )

    assert result["success"] is True
    assert result["session_id"] == str(ctx["session_a"])
    assert result["message_id"] == str(ctx["message_a"])
    assert result["tasks"][0]["proposal_id"] == "prop-1"
    assert result["tasks"][0]["status"] == "proposed"
    assert result["citations"] == [{"source": "biomarker:ldl", "label": "LDL trend"}]


@pytest.mark.asyncio
async def test_ai_chat_pins_the_engine_saves_before_assist_returns_ordering(ai_ctx):
    """Ordering contract: the engine persists the assistant message on the
    caller's db session BEFORE `assist` returns (mirrored here by a fake
    that saves through ChatSessionService) — the provider's fetch must see
    this turn's row, tasks and citations included."""
    from app.services.chat_session_service import ChatSessionService

    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    saved: dict = {}

    async def engine_like_assist(self, **kwargs):
        svc = ChatSessionService(self.db)
        row = await svc.save_message(
            session_id=ctx["session_a"],
            role="assistant",
            content={"text": "Confirm and I'll log it."},
            tasks=[
                {
                    "proposal_id": "prop-2",
                    "task_type": "create_clinical_event",
                    "title": "Log follow-up",
                    "status": "proposed",
                    "proposed_payload": {},
                }
            ],
            citations=[{"source": "observation:abc", "label": "LDL 3.9"}],
        )
        saved["id"] = row.id
        return {
            "message": "Confirm and I'll log it.",
            "session_id": str(row.session_id),
            "success": True,
        }

    with patch("app.ai.assistance.service.AIAssistanceService.assist", new=engine_like_assist):
        result = await provider.handle_api_request(
            integration=integration,
            path="ai/chat",
            method="POST",
            request=_post_request(
                {"message": "Log my lipids", "session_id": str(ctx["session_a"])}
            ),
        )

    assert result["message_id"] == str(saved["id"])
    assert result["tasks"][0]["proposal_id"] == "prop-2"
    assert result["citations"] == [{"source": "observation:abc", "label": "LDL 3.9"}]


@pytest.mark.asyncio
async def test_ai_chat_max_iterations_returns_empty_tasks_and_null_message_id(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async def max_iterations_assist(self, **kwargs):
        return {
            "message": "I'm sorry, I reached my maximum reasoning limit.",
            "success": False,
        }

    with patch(
        "app.ai.assistance.service.AIAssistanceService.assist",
        new=max_iterations_assist,
    ):
        result = await provider.handle_api_request(
            integration=integration,
            path="ai/chat",
            method="POST",
            request=_post_request(
                {"message": "Log my lipids", "session_id": str(ctx["session_a"])}
            ),
        )

    assert result["success"] is False
    assert result["message_id"] is None
    assert result["tasks"] == []
    assert result["citations"] == []


@pytest.mark.asyncio
async def test_ai_messages_limit_is_honored_newest_first(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    async with AsyncSessionLocal() as db:
        for i in range(5):
            db.add(
                ChatMessage(
                    id=uuid.uuid4(),
                    session_id=ctx["session_a"],
                    role="user",
                    content={"text": f"older-{i}"},
                    created_at=base + timedelta(minutes=i),
                )
            )
        await db.commit()

    limited = await provider.handle_api_request(
        integration=integration,
        path=f"ai/sessions/{ctx['session_a']}/messages",
        method="GET",
        request=_get_request({"limit": "3"}),
    )
    assert len(limited["data"]) == 3
    assert limited["data"][0]["id"] == str(ctx["message_a"])
    assert limited["data"][0]["tasks"][0]["proposal_id"] == "prop-1"
    assert limited["data"][1]["content"]["text"] == "older-4"
    assert limited["data"][2]["content"]["text"] == "older-3"

    default = await provider.handle_api_request(
        integration=integration,
        path=f"ai/sessions/{ctx['session_a']}/messages",
        method="GET",
        request=_get_request(),
    )
    assert len(default["data"]) == 6
    assert default["data"][0]["id"] == str(ctx["message_a"])

    garbage = await provider.handle_api_request(
        integration=integration,
        path=f"ai/sessions/{ctx['session_a']}/messages",
        method="GET",
        request=_get_request({"limit": "not-a-number"}),
    )
    assert len(garbage["data"]) == 6


@pytest.mark.asyncio
async def test_ai_resolve_records_dismissal_and_appends_note(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path=f"ai/sessions/{ctx['session_a']}/tasks/prop-1/resolve",
        method="POST",
        request=_post_request({"status": "dismissed"}),
    )

    assert result["success"] is True
    assert result["task"]["status"] == "dismissed"
    assert result["task"]["resolved"]["at"]

    async with AsyncSessionLocal() as db:
        msgs = (
            (
                await db.execute(
                    select(ChatMessage).where(ChatMessage.session_id == ctx["session_a"])
                )
            )
            .scalars()
            .all()
        )
        notes = [m for m in msgs if "dismissed" in (m.content or {}).get("text", "")]
        assert len(notes) == 1
        assert notes[0].role == "assistant"

    # Second resolve → conflict (BridgeError 409 'hitl_conflict' through the
    # proxy; still a ValueError subclass for direct provider callers).
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_a']}/tasks/prop-1/resolve",
            method="POST",
            request=_post_request({"status": "dismissed"}),
        )


@pytest.mark.asyncio
async def test_ai_rename_session_round_trips_the_new_title(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path=f"ai/sessions/{ctx['session_a']}",
        method="PATCH",
        request=_post_request({"title": "Lipids Q3 follow-up"}),
    )

    assert result["id"] == str(ctx["session_a"])
    assert result["title"] == "Lipids Q3 follow-up"
    assert result["updated_at"]

    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, ctx["session_a"])
        assert session.title == "Lipids Q3 follow-up"

    listing = await provider.handle_api_request(
        integration=integration,
        path="ai/sessions",
        method="GET",
        request=_get_request(),
    )
    assert listing["data"][0]["title"] == "Lipids Q3 follow-up"


@pytest.mark.asyncio
async def test_ai_rename_session_is_scoped_and_validated(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_b']}",
            method="PATCH",
            request=_post_request({"title": "Not mine"}),
        )
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_a']}",
            method="PATCH",
            request=_post_request({"title": "   "}),
        )
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_a']}",
            method="PATCH",
            request=_post_request({"title": "x" * 201}),
        )


@pytest.mark.asyncio
async def test_ai_delete_session_is_scoped(ai_ctx):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path=f"ai/sessions/{ctx['session_a']}",
        method="DELETE",
        request=_get_request(),
    )
    assert result["success"] is True

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_b']}",
            method="DELETE",
            request=_get_request(),
        )


@pytest.mark.asyncio
async def test_ai_chat_stream_yields_sse_frames_and_pins_the_patient(ai_ctx, turn_redis):
    """The streaming twin: SSE frames flow through the provider's
    StreamingResponse (consumed via its body_iterator), the service sees the
    pinned patient context, and the single-flight turn lock is released."""
    import json as _json

    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    captured = {}

    async def fake_assist(self, **kwargs):
        captured.update(kwargs)

        async def gen():
            assert kwargs["stream"] is True
            sid = kwargs["context"]["session_id"]
            yield f'data: {{"event":"session","session_id":"{sid}"}}\n\n'
            yield 'data: {"event":"delta","text":"Hi"}\n\n'
            yield 'data: {"event":"flow_finished"}\n\n'

        return gen()

    from fastapi.responses import StreamingResponse

    with patch("app.ai.assistance.service.AIAssistanceService.assist", new=fake_assist):
        response = await provider.handle_api_request(
            integration=integration,
            path="ai/chat/stream",
            method="POST",
            request=_post_request(
                {"message": "Summarize my lipids", "session_id": str(ctx["session_a"])}
            ),
        )
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        # Consume INSIDE the patch — the generator body (and the patched
        # service call) only runs when iteration starts.
        frames = [frame async for frame in response.body_iterator]

    events = [_json.loads(f.removeprefix("data: ").strip()) for f in frames]
    assert [e["event"] for e in events] == ["session", "delta", "flow_finished"]
    assert events[0]["session_id"] == str(ctx["session_a"])
    assert captured["context"]["patient_id"] is not None
    assert captured["context"]["session_id"] == str(ctx["session_a"])

    # The turn lock was released — an immediate second stream is accepted.
    with patch("app.ai.assistance.service.AIAssistanceService.assist", new=fake_assist):
        second = await provider.handle_api_request(
            integration=integration,
            path="ai/chat/stream",
            method="POST",
            request=_post_request({"message": "Again", "session_id": str(ctx["session_a"])}),
        )
        _ = [frame async for frame in second.body_iterator]


@pytest.mark.asyncio
async def test_ai_chat_stream_emits_keep_alive_comments_during_idle(
    ai_ctx, turn_redis, monkeypatch
):
    """WS7: idle stretches emit spec-mandated-ignorable ``: keep-alive``
    comment lines while every data frame passes through verbatim and in
    order — the patched service sleeps past the injected heartbeat interval
    between frames, and the turn lock still releases with the stream."""
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    monkeypatch.setattr(turns_mod, "SSE_HEARTBEAT_SECONDS", 0.05)

    async def fake_assist(self, **kwargs):
        async def gen():
            yield 'data: {"event":"delta","text":"Slow tool"}\n\n'
            await asyncio.sleep(0.13)
            yield 'data: {"event":"flow_finished"}\n\n'

        return gen()

    with patch("app.ai.assistance.service.AIAssistanceService.assist", new=fake_assist):
        response = await provider.handle_api_request(
            integration=integration,
            path="ai/chat/stream",
            method="POST",
            request=_post_request({"message": "Slow", "session_id": str(ctx["session_a"])}),
        )
        frames = [frame async for frame in response.body_iterator]

    data = [frame for frame in frames if frame.startswith("data:")]
    assert data == [
        'data: {"event":"delta","text":"Slow tool"}\n\n',
        'data: {"event":"flow_finished"}\n\n',
    ]
    comments = [frame for frame in frames if frame.startswith(":")]
    assert comments
    assert all(frame == ": keep-alive\n\n" for frame in comments)
    assert frames.index(data[0]) < frames.index(comments[0]) < frames.index(data[-1])

    # The turn lock was released despite the heartbeats.
    assert turn_redis.store == {}


@pytest.mark.asyncio
async def test_ai_chat_stream_isolates_foreign_sessions_and_blanks(ai_ctx, turn_redis):
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="ai/chat/stream",
            method="POST",
            request=_post_request({"message": "hi", "session_id": str(ctx["session_b"])}),
        )
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="ai/chat/stream",
            method="POST",
            request=_post_request({"message": "   "}),
        )


# ---------------------------------------------------------------------------
# POST /ai/sessions/{id}/resume — HITL continuation streaming (WS4)
# ---------------------------------------------------------------------------


def _patch_resume(fake_resume):
    """Patch ``resume_after_hitl`` with a plain function returning the async
    generator — the REAL call shape (it is an async-generator function, so
    the provider must iterate it directly and never await it; an ``await``
    regression makes the tests below fail, mirroring test_hitl_resume)."""
    return patch(
        "app.ai.assistance.service.AIAssistanceService.resume_after_hitl",
        new=lambda self, **kwargs: fake_resume(**kwargs),
    )


@pytest.mark.asyncio
async def test_ai_resume_stream_yields_frames_threads_kwargs_and_releases(ai_ctx, turn_redis):
    """Frames flow through the provider's StreamingResponse (consumed via
    its body_iterator), the service receives the isolation envelope
    (session/tenant/owner + message_id selector), and the turn lock is
    released with the stream."""
    import json as _json

    from fastapi.responses import StreamingResponse

    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    captured = {}
    message_id = uuid.uuid4()

    async def fake_resume(**kwargs):
        captured.update(kwargs)
        yield 'data: {"event":"delta","text":"Acknowledged."}\n\n'
        yield 'data: {"event":"flow_finished"}\n\n'

    with _patch_resume(fake_resume):
        response = await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_a']}/resume",
            method="POST",
            request=_post_request({"message_id": str(message_id)}),
        )
        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        frames = [frame async for frame in response.body_iterator]

    events = [_json.loads(f.removeprefix("data: ").strip()) for f in frames]
    assert [e["event"] for e in events] == ["delta", "flow_finished"]
    assert captured["session_id"] == ctx["session_a"]
    assert captured["message_id"] == message_id
    assert captured["tenant_id"] == integration.tenant_id
    assert captured["user_id"] == integration.user_id

    # The turn lock was released — an immediate chat stream is accepted.
    async def fake_assist(self, **kwargs):
        async def gen():
            yield 'data: {"event":"flow_finished"}\n\n'

        return gen()

    with patch("app.ai.assistance.service.AIAssistanceService.assist", new=fake_assist):
        second = await provider.handle_api_request(
            integration=integration,
            path="ai/chat/stream",
            method="POST",
            request=_post_request({"message": "Next", "session_id": str(ctx["session_a"])}),
        )
        _ = [frame async for frame in second.body_iterator]


@pytest.mark.asyncio
async def test_ai_resume_stream_rejects_foreign_sessions_and_bad_selectors(ai_ctx, turn_redis):
    """Isolation first: a foreign-patient session (or a malformed
    message_id) raises ValueError before any stream starts — no lock taken."""
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_b']}/resume",
            method="POST",
            request=_post_request({}),
        )
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_a']}/resume",
            method="POST",
            request=_post_request({"message_id": "not-a-uuid"}),
        )
    assert turn_redis.store == {}


@pytest.mark.asyncio
async def test_ai_resume_stream_maps_guard_rejection_to_guard_frame(ai_ctx, turn_redis):
    """A ValueError raised by the continuation (ownership / no-tasks /
    pending-task guards) surfaces as the terminal flow_failed frame with
    code 'guard' — and the lock still releases."""
    import json as _json

    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async def rejecting_resume(**kwargs):
        raise ValueError("Tasks on the target message are not all resolved.")
        yield

    with _patch_resume(rejecting_resume):
        response = await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_a']}/resume",
            method="POST",
            request=_post_request({}),
        )
        frames = [frame async for frame in response.body_iterator]

    assert len(frames) == 1
    payload = _json.loads(frames[0].removeprefix("data: ").strip())
    assert payload["event"] == "flow_failed"
    assert payload["code"] == "guard"
    assert "not all resolved" in payload["message"]
    assert payload["retryable"] is False
    assert turn_redis.store == {}


@pytest.mark.asyncio
async def test_ai_resume_stream_interlocks_with_inflight_chat_turn(ai_ctx, turn_redis):
    """The single-flight lock spans both streaming surfaces: a resume on a
    session with a mid-flight chat turn (and the reverse) is rejected before
    the stream starts, and the lock frees when the holder's stream ends."""
    ctx = ai_ctx
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    async def fake_assist(self, **kwargs):
        async def gen():
            yield 'data: {"event":"delta","text":"…"}\n\n'
            yield 'data: {"event":"flow_finished"}\n\n'

        return gen()

    with patch("app.ai.assistance.service.AIAssistanceService.assist", new=fake_assist):
        chat = await provider.handle_api_request(
            integration=integration,
            path="ai/chat/stream",
            method="POST",
            request=_post_request({"message": "hi", "session_id": str(ctx["session_a"])}),
        )
        # The handler acquired the lock before returning the response.
        assert turn_redis._live(f"turn:{ctx['session_a']}") is not None

        with pytest.raises(ValueError, match="already streaming"):
            await provider.handle_api_request(
                integration=integration,
                path=f"ai/sessions/{ctx['session_a']}/resume",
                method="POST",
                request=_post_request({}),
            )

        # Consuming the holder's stream runs its finally → lock released.
        _ = [frame async for frame in chat.body_iterator]
        assert turn_redis._live(f"turn:{ctx['session_a']}") is None

    captured = {}

    async def fake_resume(**kwargs):
        captured.update(kwargs)
        yield 'data: {"event":"flow_finished"}\n\n'

    with _patch_resume(fake_resume):
        held = await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_a']}/resume",
            method="POST",
            request=_post_request({}),
        )
        assert turn_redis._live(f"turn:{ctx['session_a']}") is not None

        with (
            patch("app.ai.assistance.service.AIAssistanceService.assist", new=fake_assist),
            pytest.raises(ValueError, match="already streaming"),
        ):
            await provider.handle_api_request(
                integration=integration,
                path="ai/chat/stream",
                method="POST",
                request=_post_request({"message": "again", "session_id": str(ctx["session_a"])}),
            )

        _ = [frame async for frame in held.body_iterator]

    assert turn_redis._live(f"turn:{ctx['session_a']}") is None
    assert captured["message_id"] is None
