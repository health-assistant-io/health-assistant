"""Typed bridge error contract (plan bridge-r7 WS8, 2026-10-12).

`BridgeError(ValueError)` carries `status_code` + a stable `code`; the
integration API proxy maps it to
`HTTPException(status, detail={"code": ..., "message": ...})` while a bare
`ValueError` keeps the legacy plain-string 400. These tests exercise the
REAL proxy path (DB-backed, HMAC-signed — the
`test_bridge_ai_rate_limit.py` fixture pattern) so the wire shapes are
pinned end-to-end."""

import hashlib
import hmac
import time
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException


def _sign(secret: str, method: str, path: str, body: bytes, timestamp: int) -> str:
    parts = [method.upper().encode(), b"\n", path.encode(), b"\n"]
    parts.append(f"{timestamp}".encode() + b"\n")
    parts.append(body)
    return hmac.new(secret.encode(), b"".join(parts), hashlib.sha256).hexdigest()


def _signed_request(
    secret: str, method: str, path: str, body: bytes = b"", json_payload: dict | None = None
):
    ts = int(time.time())
    req = MagicMock()
    req.method = method
    req.headers = {
        "X-Api-Signature": _sign(secret, method, path, body, timestamp=ts),
        "X-Api-Timestamp": str(ts),
    }
    req.url = types.SimpleNamespace(query="")
    req.query_params = {}

    async def _body():
        return body

    req.body = _body
    if json_payload is not None:

        async def _json():
            return json_payload

        req.json = _json
    return req


@pytest.fixture
def ai_rate_redis(monkeypatch):
    """Hermetic AI-bucket store (the /ai/* paths enforce 30/5min)."""
    from app.core import rate_limit as rl_mod
    from tests.test_bridge_ai_rate_limit import FakeRedis

    fake = FakeRedis()
    monkeypatch.setattr(rl_mod, "redis_client", fake)
    return fake


@pytest.fixture
def turn_redis(monkeypatch):
    """Hermetic turn lock (set/eval fake — see test_turn_lock)."""
    from app.ai.assistance import turns as turns_mod
    from tests.test_turn_lock import FakeRedis

    fake = FakeRedis()
    monkeypatch.setattr(turns_mod, "redis_client", fake)
    return fake


@pytest_asyncio.fixture
async def typed_err_ctx():
    """Tenant + owner + PatientA/PatientB + an ACTIVE integration bound to A
    (carrying an api_secret) + one chat session per patient, A's holding a
    PROPOSED HITL card (the double-resolve target)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    integration_id = uuid.uuid4()
    api_secret = f"typed-err-{uuid.uuid4().hex}"

    from app.core.database import AsyncSessionLocal
    from app.models.chat_model import ChatMessage, ChatSession
    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.models.user_integration import UserIntegration
    from app.models.user_model import UserModel

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Typed err", slug=f"terr-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"terr-{user_id.hex[:6]}@test.local",
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
                user_config={"api_secret": api_secret},
            )
        )
        await db.flush()
        session_a = ChatSession(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            patient_id=patient_a,
            title="Typed errors target",
        )
        session_b = ChatSession(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            patient_id=patient_b,
            title="Foreign patient session",
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
                        "title": "Log follow-up",
                        "status": "proposed",
                        "proposed_payload": {"title": "Follow-up"},
                    }
                ],
                citations=[],
            )
        )
        await db.commit()
        return {
            "integration_id": integration_id,
            "api_secret": api_secret,
            "session_a": session_a.id,
            "session_b": session_b.id,
        }


async def _proxy(db, ctx, path: str, method: str, payload: dict | None = None, body: bytes = b""):
    from app.api.v1.endpoints import integrations as ep
    from app.extensions.builtin.health_assistant_bridge.provider import (
        HealthAssistantBridgeProvider,
    )

    if payload is not None:
        import json as _json

        body = _json.dumps(payload).encode()

    with patch.object(ep.integration_registry, "get_provider") as gp:
        gp.return_value = HealthAssistantBridgeProvider()
        req = _signed_request(ctx["api_secret"], method, path, body=body, json_payload=payload)
        return await ep.integration_api_proxy(
            domain="health_assistant_bridge",
            integration_id=str(ctx["integration_id"]),
            path=path,
            request=req,
            db=db,
        )


async def _fake_streaming_assist(self, **kwargs):
    async def gen():
        yield 'data: {"event":"delta","text":"Hi"}\n\n'
        yield 'data: {"event":"flow_finished"}\n\n'

    return gen()


class TestTypedErrorsThroughTheProxy:
    async def test_foreign_session_read_is_404_not_found(self, typed_err_ctx, ai_rate_redis):
        ctx = typed_err_ctx
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            with pytest.raises(HTTPException) as exc:
                await _proxy(db, ctx, f"ai/sessions/{ctx['session_b']}/messages", "GET")
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "not_found"
        assert "not found" in exc.value.detail["message"].lower()

    async def test_blank_chat_message_is_400_validation(self, typed_err_ctx, ai_rate_redis):
        ctx = typed_err_ctx
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            with pytest.raises(HTTPException) as exc:
                await _proxy(db, ctx, "ai/chat", "POST", payload={"message": "   "})
        assert exc.value.status_code == 400
        assert exc.value.detail == {
            "code": "validation",
            "message": "message is required",
        }

    async def test_midflight_conflicting_stream_is_409_turn_conflict(
        self, typed_err_ctx, ai_rate_redis, turn_redis
    ):
        ctx = typed_err_ctx
        from app.core.database import AsyncSessionLocal

        with patch(
            "app.ai.assistance.service.AIAssistanceService.assist",
            new=_fake_streaming_assist,
        ):
            async with AsyncSessionLocal() as db:
                first = await _proxy(
                    db,
                    ctx,
                    "ai/chat/stream",
                    "POST",
                    payload={"message": "hold the lock", "session_id": str(ctx["session_a"])},
                )
                with pytest.raises(HTTPException) as exc:
                    await _proxy(
                        db,
                        ctx,
                        "ai/chat/stream",
                        "POST",
                        payload={"message": "conflicting", "session_id": str(ctx["session_a"])},
                    )
                assert exc.value.status_code == 409
                assert exc.value.detail["code"] == "turn_conflict"
                assert "already streaming" in exc.value.detail["message"].lower()
                first_frames = [frame async for frame in first.body_iterator]
        assert first_frames  # consuming the holder releases the lock

    async def test_double_resolve_is_409_hitl_conflict(self, typed_err_ctx, ai_rate_redis):
        ctx = typed_err_ctx
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            ok = await _proxy(
                db,
                ctx,
                f"ai/sessions/{ctx['session_a']}/tasks/prop-1/resolve",
                "POST",
                payload={"status": "dismissed"},
            )
            assert ok["success"] is True
            with pytest.raises(HTTPException) as exc:
                await _proxy(
                    db,
                    ctx,
                    f"ai/sessions/{ctx['session_a']}/tasks/prop-1/resolve",
                    "POST",
                    payload={"status": "dismissed"},
                )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "hitl_conflict"

    async def test_bare_value_error_is_typed_without_raw_text(self, typed_err_ctx, ai_rate_redis):
        """F22: a bare ValueError still maps to the 400 ``validation`` class,
        but the detail is the stable typed envelope — the exception text
        (which may carry internal/provider detail) never reaches the client."""
        ctx = typed_err_ctx
        from app.core.database import AsyncSessionLocal

        async def _boom(self, **kwargs):
            raise ValueError("internal detail: /srv/secret/path?token=abc")

        with patch(
            "app.extensions.builtin.health_assistant_bridge.provider"
            ".HealthAssistantBridgeProvider.handle_api_request",
            new=_boom,
        ):
            async with AsyncSessionLocal() as db:
                with pytest.raises(HTTPException) as exc:
                    await _proxy(db, ctx, "observations", "GET")
        assert exc.value.status_code == 400
        assert exc.value.detail == {"code": "validation", "message": "Invalid request."}
        assert "token=abc" not in str(exc.value.detail)

    async def test_untyped_500_is_typed_and_capped(self, typed_err_ctx, ai_rate_redis):
        """F22: an unexpected exception answers ``{code: io, message: …}`` and
        the raw text stays in the log, never in the machine response."""
        ctx = typed_err_ctx
        from app.core.database import AsyncSessionLocal

        async def _boom(self, **kwargs):
            raise RuntimeError("db password=super-secret DSN")

        with patch(
            "app.extensions.builtin.health_assistant_bridge.provider"
            ".HealthAssistantBridgeProvider.handle_api_request",
            new=_boom,
        ):
            async with AsyncSessionLocal() as db:
                with pytest.raises(HTTPException) as exc:
                    await _proxy(db, ctx, "observations", "GET")
        assert exc.value.status_code == 500
        assert exc.value.detail == {
            "code": "io",
            "message": "Internal processing error.",
        }
        assert "super-secret" not in str(exc.value.detail)

    async def test_bridge_error_subclasses_value_error(self):
        """Direct provider callers' `pytest.raises(ValueError)` keeps passing."""
        from app.extensions.sdk import BridgeError

        err = BridgeError(404, "not_found", "Exam not found for this patient.")
        assert isinstance(err, ValueError)
        assert err.status_code == 404
        assert err.code == "not_found"
        assert str(err) == "Exam not found for this patient."
