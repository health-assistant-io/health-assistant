"""Bridge AI-assistant paths (R7 Phase 1, 2026-10-11) — DB-backed tests for
`GET /ai/sessions`, `GET /ai/sessions/{id}/messages`, `POST /ai/chat`
(non-streaming), `DELETE /ai/sessions/{id}`, and the HITL resolve path.
The LLM is patched out (`AIAssistanceService.assist`) — these tests pin the
routing, the owner/patient isolation envelope, and the resolve contract."""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.chat_model import ChatMessage, ChatSession
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel


def _post_request(payload: dict):
    from unittest.mock import MagicMock

    req = MagicMock()
    req.query_params = {}

    async def _json():
        return payload

    req.json = _json
    return req


def _get_request():
    from unittest.mock import MagicMock

    req = MagicMock()
    req.query_params = {}
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
        db.add(
            ChatMessage(
                id=uuid.uuid4(),
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
            )
        )
        await db.commit()
        return {
            "integration_id": integration_id,
            "session_a": session_a.id,
            "session_b": session_b.id,
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

    # Second resolve → conflict mapped to ValueError (bridge 400).
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path=f"ai/sessions/{ctx['session_a']}/tasks/prop-1/resolve",
            method="POST",
            request=_post_request({"status": "dismissed"}),
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
async def test_ai_chat_stream_yields_sse_frames_and_pins_the_patient(ai_ctx):
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
async def test_ai_chat_stream_isolates_foreign_sessions_and_blanks(ai_ctx):
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
