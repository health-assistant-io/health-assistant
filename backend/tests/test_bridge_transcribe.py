"""Bridge voice input (plan bridge-r7 WS10, 2026-10-12): POST /ai/transcribe.

Mirrors the PWA `/ai-assistance/transcribe` contract over the bridge
identity: base64 audio + optional mime (normalized, allowlisted against the
shared `ALLOWED_AUDIO_MIME`), the `AI_STT_MAX_AUDIO_BYTES` cap, the STT
target resolved for the integration's owner via the same gateway service,
and the non-blocking prompt guard on the transcribed text. The STT layer is
patched out; these tests pin the routing, the typed guards, ephemerality
(no rows in any table the fixtures touch), and the `/ai/*` budget. The
proxy-backed tests reuse the HMAC-signed request pattern from
`test_bridge_ai_rate_limit.py` / `test_bridge_typed_errors.py`."""

import base64
import hashlib
import hmac
import json
import time
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from integrations.sdk.exceptions import BridgeError
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.chat_model import ChatMessage, ChatSession
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import IntegrationDebugLog, IntegrationSyncLog, UserIntegration
from app.models.user_model import UserModel


@pytest.fixture
def fake_stt():
    """Patch the gateway STT layer: target resolution + transcription.

    The mocks replace an unbound class method and a module function, so
    their side-effect hooks receive exactly the provider's call args.
    """
    target = types.SimpleNamespace(
        api_key="sk-test", api_base="https://stt.example.test/v1", model_name="whisper-1"
    )

    async def _resolve(tenant_id=None, user_id=None):
        return target

    resolve = MagicMock(side_effect=_resolve)
    transcribe = AsyncMock(side_effect=lambda *args, **kwargs: "Detected words.")
    with (
        patch(
            "app.ai.providers.service.AIProviderService.get_stt_target",
            new=resolve,
        ),
        patch("app.ai.assistance.stt.transcribe_audio", new=transcribe),
    ):
        yield {"target": target, "transcribe": transcribe, "resolve": resolve}


@pytest.fixture
def guard_spy(monkeypatch):
    """Spy on the prompt guard while keeping it non-blocking."""
    from app.utils import prompt_guard

    spy = MagicMock(return_value={"safe": True, "risk": "low", "matches": [], "snippets": []})
    monkeypatch.setattr(prompt_guard, "check_user_input_safety", spy)
    return spy


@pytest.fixture
def fake_redis(monkeypatch):
    """Hermetic AI-bucket store (the /ai/* paths enforce 30/5 min)."""
    from app.core import rate_limit as rl_mod
    from tests.test_bridge_ai_rate_limit import FakeRedis

    fake = FakeRedis()
    monkeypatch.setattr(rl_mod, "redis_client", fake)
    return fake


def _post_request(payload: dict):
    req = MagicMock()
    req.query_params = {}

    async def _json():
        return payload

    req.json = _json
    return req


def _sign(secret: str, method: str, path: str, body: bytes, timestamp: int) -> str:
    parts = [method.upper().encode(), b"\n", path.encode(), b"\n"]
    parts.append(f"{timestamp}".encode() + b"\n")
    parts.append(body)
    return hmac.new(secret.encode(), b"".join(parts), hashlib.sha256).hexdigest()


def _signed_request(secret: str, method: str, path: str, body: bytes, json_payload: dict):
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

    async def _json():
        return json_payload

    req.json = _json
    return req


def _audio_b64(audio: bytes) -> str:
    return base64.b64encode(audio).decode()


@pytest_asyncio.fixture
async def stt_ctx():
    """Tenant + owner user + patient + an ACTIVE integration carrying an
    api_secret + one chat session with one message (so the nothing-persisted
    assertions compare non-zero row counts). All ids are random so
    concurrent runs against the shared test DB never collide."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    api_secret = f"stt-{uuid.uuid4().hex}"

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="STT bridge", slug=f"stt-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"stt-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Voice", "given": ["Bound"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="health_assistant_bridge",
                status="ACTIVE",
                user_config={"api_secret": api_secret},
            )
        )
        await db.flush()
        session = ChatSession(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            patient_id=patient_id,
            title="Voice notes",
        )
        db.add(session)
        await db.flush()
        db.add(
            ChatMessage(
                id=uuid.uuid4(),
                session_id=session.id,
                role="user",
                content={"text": "Existing note"},
            )
        )
        await db.commit()
        return {
            "tenant_id": tenant_id,
            "integration_id": integration_id,
            "api_secret": api_secret,
        }


async def _load_integration(integration_id) -> UserIntegration:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        return res.scalar_one()


async def _proxy_transcribe(db, ctx: dict, payload: dict):
    from app.api.v1.endpoints import integrations as ep

    body = json.dumps(payload).encode()
    return await ep.integration_api_proxy(
        domain="health_assistant_bridge",
        integration_id=str(ctx["integration_id"]),
        path="ai/transcribe",
        request=_signed_request(ctx["api_secret"], "POST", "ai/transcribe", body, payload),
        db=db,
    )


# ---------------------------------------------------------------------------
# Direct handler: routing, the STT call shape, the guard spy, the guards
# ---------------------------------------------------------------------------


class TestTranscribeHandler:
    async def test_happy_path_transcribes_and_runs_the_guard(self, stt_ctx, fake_stt, guard_spy):
        integration = await _load_integration(stt_ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        result = await provider.handle_api_request(
            integration=integration,
            path="ai/transcribe",
            method="POST",
            request=_post_request(
                {"data": _audio_b64(b"\x01\x02\x03"), "mime": "audio/webm;codecs=opus"}
            ),
        )

        assert result == {"text": "Detected words.", "success": True}
        fake_stt["transcribe"].assert_awaited_once_with(
            b"\x01\x02\x03",
            filename="recording.webm",
            mime_type="audio/webm",
            target=fake_stt["target"],
        )
        fake_stt["resolve"].assert_called_once_with(
            tenant_id=stt_ctx["tenant_id"], user_id=integration.user_id
        )
        guard_spy.assert_called_once_with("Detected words.", context="bridge_transcription")

    async def test_no_text_answers_success_false_without_guard(self, stt_ctx, fake_stt, guard_spy):
        fake_stt["transcribe"].side_effect = lambda *args, **kwargs: ""
        integration = await _load_integration(stt_ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        result = await provider.handle_api_request(
            integration=integration,
            path="ai/transcribe",
            method="POST",
            request=_post_request({"data": _audio_b64(b"\x01"), "mime": "audio/ogg"}),
        )

        assert result == {"text": "", "success": False}
        guard_spy.assert_not_called()

    async def test_missing_data_field_is_typed_400(self, stt_ctx, fake_stt):
        integration = await _load_integration(stt_ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        with pytest.raises(BridgeError) as exc:
            await provider.handle_api_request(
                integration=integration,
                path="ai/transcribe",
                method="POST",
                request=_post_request({"mime": "audio/webm"}),
            )
        assert exc.value.status_code == 400
        assert exc.value.code == "validation"
        assert "Missing base64 'data'" in str(exc.value)
        fake_stt["transcribe"].assert_not_called()

    async def test_invalid_base64_is_typed_400(self, stt_ctx, fake_stt):
        integration = await _load_integration(stt_ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        with pytest.raises(BridgeError) as exc:
            await provider.handle_api_request(
                integration=integration,
                path="ai/transcribe",
                method="POST",
                request=_post_request({"data": "!!not-base64!!", "mime": "audio/webm"}),
            )
        assert exc.value.status_code == 400
        assert exc.value.code == "validation"
        assert "Invalid base64" in str(exc.value)
        fake_stt["transcribe"].assert_not_called()

    async def test_empty_audio_is_typed_400(self, stt_ctx, fake_stt):
        integration = await _load_integration(stt_ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        with pytest.raises(BridgeError) as exc:
            await provider.handle_api_request(
                integration=integration,
                path="ai/transcribe",
                method="POST",
                request=_post_request({"data": "", "mime": "audio/webm"}),
            )
        assert exc.value.status_code == 400
        assert exc.value.code == "validation"
        assert "Empty audio recording" in str(exc.value)
        fake_stt["transcribe"].assert_not_called()

    async def test_disallowed_mime_is_typed_400(self, stt_ctx, fake_stt):
        integration = await _load_integration(stt_ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        with pytest.raises(BridgeError) as exc:
            await provider.handle_api_request(
                integration=integration,
                path="ai/transcribe",
                method="POST",
                request=_post_request(
                    {"data": _audio_b64(b"\x01\x02"), "mime": "video/webm;codecs=vp9"}
                ),
            )
        assert exc.value.status_code == 400
        assert exc.value.code == "validation"
        assert "Unsupported audio format" in str(exc.value)
        fake_stt["transcribe"].assert_not_called()

    async def test_oversize_audio_is_typed_400(self, stt_ctx, fake_stt, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "AI_STT_MAX_AUDIO_BYTES", 4)
        integration = await _load_integration(stt_ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        with pytest.raises(BridgeError) as exc:
            await provider.handle_api_request(
                integration=integration,
                path="ai/transcribe",
                method="POST",
                request=_post_request({"data": _audio_b64(b"\x01" * 5)}),
            )
        assert exc.value.status_code == 400
        assert exc.value.code == "validation"
        assert "exceeds the" in str(exc.value)
        fake_stt["transcribe"].assert_not_called()

    async def test_unresolvable_stt_target_is_typed_400(self, stt_ctx, fake_stt):
        from app.ai.assistance.stt import TranscriptionError

        async def _broken(tenant_id=None, user_id=None):
            raise TranscriptionError("No API key configured for speech-to-text.")

        fake_stt["resolve"].side_effect = _broken
        integration = await _load_integration(stt_ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        with pytest.raises(BridgeError) as exc:
            await provider.handle_api_request(
                integration=integration,
                path="ai/transcribe",
                method="POST",
                request=_post_request({"data": _audio_b64(b"\x01"), "mime": "audio/webm"}),
            )
        assert exc.value.status_code == 400
        assert exc.value.code == "validation"
        assert "No API key configured" in str(exc.value)
        fake_stt["transcribe"].assert_not_called()

    async def test_provider_failure_is_typed_502(self, stt_ctx, fake_stt):
        from app.ai.assistance.stt import TranscriptionError

        fake_stt["transcribe"].side_effect = TranscriptionError("Speech-to-text timed out.")
        integration = await _load_integration(stt_ctx["integration_id"])
        provider = HealthAssistantBridgeProvider()

        with pytest.raises(BridgeError) as exc:
            await provider.handle_api_request(
                integration=integration,
                path="ai/transcribe",
                method="POST",
                request=_post_request({"data": _audio_b64(b"\x01"), "mime": "audio/webm"}),
            )
        assert exc.value.status_code == 502
        assert exc.value.code == "stt_failed"
        assert "timed out" in str(exc.value)


# ---------------------------------------------------------------------------
# Through the real proxy: typed envelope, ephemerality, the /ai/* budget
# ---------------------------------------------------------------------------


class TestTranscribeThroughTheProxy:
    async def test_empty_audio_maps_to_the_typed_detail_envelope(
        self, stt_ctx, fake_redis, fake_stt
    ):
        from app.api.v1.endpoints import integrations as ep

        with patch.object(ep.integration_registry, "get_provider") as gp:
            gp.return_value = HealthAssistantBridgeProvider()
            async with AsyncSessionLocal() as db:
                with pytest.raises(HTTPException) as exc:
                    await _proxy_transcribe(db, stt_ctx, {"data": "", "mime": "audio/webm"})
        assert exc.value.status_code == 400
        assert exc.value.detail == {
            "code": "validation",
            "message": "Empty audio recording.",
        }
        fake_stt["transcribe"].assert_not_called()

    async def test_happy_path_persists_nothing(self, stt_ctx, fake_redis, fake_stt):
        from app.api.v1.endpoints import integrations as ep

        async with AsyncSessionLocal() as db:
            sync_logs_before = await db.scalar(
                select(func.count())
                .select_from(IntegrationSyncLog)
                .where(IntegrationSyncLog.integration_id == stt_ctx["integration_id"])
            )
            debug_logs_before = await db.scalar(
                select(func.count())
                .select_from(IntegrationDebugLog)
                .where(IntegrationDebugLog.integration_id == stt_ctx["integration_id"])
            )
            sessions_before = await db.scalar(
                select(func.count())
                .select_from(ChatSession)
                .where(ChatSession.tenant_id == stt_ctx["tenant_id"])
            )
            messages_before = await db.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                .where(ChatSession.tenant_id == stt_ctx["tenant_id"])
            )

        with patch.object(ep.integration_registry, "get_provider") as gp:
            gp.return_value = HealthAssistantBridgeProvider()
            async with AsyncSessionLocal() as db:
                result = await _proxy_transcribe(
                    db,
                    stt_ctx,
                    {"data": _audio_b64(b"\x01\x02\x03"), "mime": "audio/webm;codecs=opus"},
                )
        assert result == {"text": "Detected words.", "success": True}

        async with AsyncSessionLocal() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(IntegrationSyncLog)
                    .where(IntegrationSyncLog.integration_id == stt_ctx["integration_id"])
                )
                == sync_logs_before
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(IntegrationDebugLog)
                    .where(IntegrationDebugLog.integration_id == stt_ctx["integration_id"])
                )
                == debug_logs_before
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ChatSession)
                    .where(ChatSession.tenant_id == stt_ctx["tenant_id"])
                )
                == sessions_before
                > 0
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ChatMessage)
                    .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                    .where(ChatSession.tenant_id == stt_ctx["tenant_id"])
                )
                == messages_before
                > 0
            )

    async def test_31st_transcribe_in_window_is_throttled(self, stt_ctx, fake_redis, fake_stt):
        """`ai/transcribe` starts with `ai/` — it draws from the shared
        30-per-5-min budget (WS2): 30 transcriptions pass, the 31st is a
        429 with Retry-After."""
        from app.api.v1.endpoints import integrations as ep

        payload = {"data": _audio_b64(b"\x01\x02\x03"), "mime": "audio/webm"}
        with patch.object(ep.integration_registry, "get_provider") as gp:
            gp.return_value = HealthAssistantBridgeProvider()
            async with AsyncSessionLocal() as db:
                for _ in range(30):
                    result = await _proxy_transcribe(db, stt_ctx, payload)
                    assert result["success"] is True
                with pytest.raises(HTTPException) as exc:
                    await _proxy_transcribe(db, stt_ctx, payload)
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
        assert any(
            key.startswith(f"rl:integration_api_proxy_ai:integration:{stt_ctx['integration_id']}:")
            for key in fake_redis.store
        )
        assert fake_stt["transcribe"].await_count == 30
