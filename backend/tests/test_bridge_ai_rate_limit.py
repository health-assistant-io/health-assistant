"""Bridge AI rate-limit bucket (plan bridge-r7 WS2 / F4, 2026-10-11).

`/ai/*` proxy paths share the PWA's AI budget: 30 requests / 5 min per
integration under the §16 `HA_RATELIMIT_AI` knob (0 disables). Unit tests
pin the helper's contract (FakeRedis pattern from
`test_audit_a2_rate_limit.py`); DB-backed tests exercise the enforcement
inside `integration_api_proxy` end-to-end (HMAC signed requests, LLM
patched out — fixture patterns from `test_bridge_ai_assistant.py`)."""

import hashlib
import hmac
import inspect
import time
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.core import rate_limit as rl_mod


class FakeRedis:
    """Deterministic in-memory stand-in for the async redis client."""

    def __init__(self):
        self.store: dict[str, int] = {}
        self.fail = False

    async def incr(self, key: str) -> int:
        if self.fail:
            raise RuntimeError("redis down")
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> None:
        return


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rl_mod, "redis_client", fake)
    return fake


@pytest.fixture
def turn_redis(monkeypatch):
    """Hermetic turn lock for the streaming resume path: the real client's
    pooled connections are loop-bound and a connection created by an
    earlier test poisons this one's release ("attached to a different
    loop"), leaking the key into later assertions — the FakeRedis pattern
    from tests/test_turn_lock.py (set/eval compare-and-delete included)."""
    from app.ai.assistance import turns as turns_mod
    from tests.test_turn_lock import FakeRedis as TurnFakeRedis

    fake = TurnFakeRedis()
    monkeypatch.setattr(turns_mod, "redis_client", fake)
    return fake


class TestEnforceIntegrationRateLimitUnit:
    """The imperative helper: same key shape + §16 ceiling resolution as
    the dependency form, delegating to `_consume` (429 + Retry-After)."""

    async def test_allows_30_rejects_31st_with_429_and_retry_after(self, fake_redis):
        integration_id = str(uuid.uuid4())
        for _ in range(30):
            await rl_mod.enforce_integration_rate_limit(
                "integration_api_proxy_ai",
                integration_id,
                max_requests=30,
                window=300,
                bucket="ai",
            )
        with pytest.raises(HTTPException) as exc:
            await rl_mod.enforce_integration_rate_limit(
                "integration_api_proxy_ai",
                integration_id,
                max_requests=30,
                window=300,
                bucket="ai",
            )
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
        window_bucket = int(time.time()) // 300
        assert fake_redis.store == {
            f"rl:integration_api_proxy_ai:integration:{integration_id}:{window_bucket}": 31
        }

    async def test_distinct_integration_ids_get_independent_buckets(self, fake_redis):
        int_a, int_b = str(uuid.uuid4()), str(uuid.uuid4())
        for _ in range(30):
            await rl_mod.enforce_integration_rate_limit(
                "integration_api_proxy_ai", int_a, max_requests=30, window=300, bucket="ai"
            )
        with pytest.raises(HTTPException):
            await rl_mod.enforce_integration_rate_limit(
                "integration_api_proxy_ai", int_a, max_requests=30, window=300, bucket="ai"
            )
        await rl_mod.enforce_integration_rate_limit(
            "integration_api_proxy_ai", int_b, max_requests=30, window=300, bucket="ai"
        )

    async def test_zero_ai_ceiling_disables_bucket(self, fake_redis, monkeypatch):
        from tests.settings_factory import settings_stub

        monkeypatch.setattr(
            rl_mod,
            "get_settings",
            lambda: settings_stub(HA_RATELIMIT_ENABLED=True, HA_RATELIMIT_AI=0),
        )
        integration_id = str(uuid.uuid4())
        for _ in range(50):
            await rl_mod.enforce_integration_rate_limit(
                "integration_api_proxy_ai", integration_id, max_requests=30, window=300, bucket="ai"
            )


def test_api_proxy_wires_the_ai_bucket():
    """Static guard: the proxy enforces the AI bucket after signature
    verification (WS2 wiring — the knob is useless if unplugged). It also
    covers provider-declared off-prefix AI paths (F19 — bridge ``/map``)."""
    from app.api.v1.endpoints import integrations as ep

    src = inspect.getsource(ep.integration_api_proxy)
    assert 'path.startswith("ai/")' in src
    assert 'getattr(provider, "ai_api_paths", ())' in src
    assert "enforce_integration_rate_limit" in src
    assert 'bucket="ai"' in src


def test_bridge_map_path_is_declared_ai():
    """F19: ``POST /map`` runs an LLM and must draw from the AI bucket."""
    from app.extensions.builtin.health_assistant_bridge.provider import (
        HealthAssistantBridgeProvider,
    )

    assert "map" in HealthAssistantBridgeProvider.ai_api_paths


# ---------------------------------------------------------------------------
# DB-backed: the 30/5-min budget enforced inside integration_api_proxy
# ---------------------------------------------------------------------------


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


@pytest_asyncio.fixture
async def ai_rl_ctx():
    """Tenant + owner user + patient + ACTIVE integration carrying an
    api_secret. All ids are random so concurrent test runs against the
    shared dev test DB (and the FakeRedis budget) never collide."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    api_secret = f"test-secret-{uuid.uuid4().hex}"

    from app.core.database import AsyncSessionLocal
    from app.models.chat_model import ChatSession
    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.models.user_integration import UserIntegration
    from app.models.user_model import UserModel

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="AI rl", slug=f"airl-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"airl-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Rl", "given": ["Bound"]},
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
            title="Rate limit target",
        )
        db.add(session)
        await db.commit()
        return {
            "integration_id": integration_id,
            "api_secret": api_secret,
            "session_id": session.id,
        }


async def _fake_assist(self, **kwargs):
    return {"message": "ok", "session_id": str(uuid.uuid4()), "success": True}


async def _proxy_ai_chat(db, integration_id: str, api_secret: str):
    from app.api.v1.endpoints import integrations as ep

    return await ep.integration_api_proxy(
        domain="health_assistant_bridge",
        integration_id=str(integration_id),
        path="ai/chat",
        request=_signed_request(
            api_secret,
            "POST",
            "ai/chat",
            body=b'{"message": "ping"}',
            json_payload={"message": "ping"},
        ),
        db=db,
    )


async def _proxy_map(db, integration_id: str, api_secret: str):
    from app.api.v1.endpoints import integrations as ep

    body = b'{"unmapped_metrics": []}'
    return await ep.integration_api_proxy(
        domain="health_assistant_bridge",
        integration_id=str(integration_id),
        path="map",
        request=_signed_request(
            api_secret,
            "POST",
            "map",
            body=body,
            json_payload={"unmapped_metrics": []},
        ),
        db=db,
    )


async def _proxy_observations(db, integration_id: str, api_secret: str):
    from app.api.v1.endpoints import integrations as ep

    return await ep.integration_api_proxy(
        domain="health_assistant_bridge",
        integration_id=str(integration_id),
        path="observations",
        request=_signed_request(api_secret, "GET", "observations"),
        db=db,
    )


async def _proxy_ai_resume(db, integration_id: str, api_secret: str, session_id):
    from app.api.v1.endpoints import integrations as ep

    path = f"ai/sessions/{session_id}/resume"
    return await ep.integration_api_proxy(
        domain="health_assistant_bridge",
        integration_id=str(integration_id),
        path=path,
        request=_signed_request(api_secret, "POST", path, body=b"{}", json_payload={}),
        db=db,
    )


class TestAiBucketThroughTheProxy:
    async def test_31st_ai_chat_in_window_is_throttled(self, ai_rl_ctx, fake_redis):
        from app.api.v1.endpoints import integrations as ep
        from app.core.database import AsyncSessionLocal
        from app.extensions.builtin.health_assistant_bridge.provider import (
            HealthAssistantBridgeProvider,
        )

        with (
            patch("app.ai.assistance.service.AIAssistanceService.assist", new=_fake_assist),
            patch.object(ep.integration_registry, "get_provider") as gp,
        ):
            gp.return_value = HealthAssistantBridgeProvider()
            async with AsyncSessionLocal() as db:
                for _ in range(30):
                    result = await _proxy_ai_chat(
                        db, ai_rl_ctx["integration_id"], ai_rl_ctx["api_secret"]
                    )
                    assert result["success"] is True
                with pytest.raises(HTTPException) as exc:
                    await _proxy_ai_chat(db, ai_rl_ctx["integration_id"], ai_rl_ctx["api_secret"])
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
        assert any(
            key.startswith(
                f"rl:integration_api_proxy_ai:integration:{ai_rl_ctx['integration_id']}:"
            )
            for key in fake_redis.store
        )

    async def test_31st_observations_still_succeeds_ai_bucket_only(self, ai_rl_ctx, fake_redis):
        from app.api.v1.endpoints import integrations as ep
        from app.core.database import AsyncSessionLocal
        from app.extensions.builtin.health_assistant_bridge.provider import (
            HealthAssistantBridgeProvider,
        )

        with (
            patch("app.ai.assistance.service.AIAssistanceService.assist", new=_fake_assist),
            patch.object(ep.integration_registry, "get_provider") as gp,
        ):
            gp.return_value = HealthAssistantBridgeProvider()
            async with AsyncSessionLocal() as db:
                for _ in range(30):
                    await _proxy_ai_chat(db, ai_rl_ctx["integration_id"], ai_rl_ctx["api_secret"])
                with pytest.raises(HTTPException) as exc:
                    await _proxy_ai_chat(db, ai_rl_ctx["integration_id"], ai_rl_ctx["api_secret"])
                assert exc.value.status_code == 429
                for _ in range(31):
                    result = await _proxy_observations(
                        db, ai_rl_ctx["integration_id"], ai_rl_ctx["api_secret"]
                    )
                    assert isinstance(result, dict)

    async def test_map_path_counts_toward_the_ai_bucket(self, ai_rl_ctx, fake_redis):
        """F19: ``POST /map`` is LLM-backed but has no ``ai/`` prefix. It
        must draw from the same 30/5-min per-instance AI budget, not the
        generic proxy budget (30 maps stream through, the 31st is 429)."""
        from app.api.v1.endpoints import integrations as ep
        from app.core.database import AsyncSessionLocal
        from app.extensions.builtin.health_assistant_bridge.provider import (
            HealthAssistantBridgeProvider,
        )

        async def _fake_map(self, integration, map_request):
            return {"mappings": []}

        with (
            patch.object(HealthAssistantBridgeProvider, "_handle_map_request", new=_fake_map),
            patch.object(ep.integration_registry, "get_provider") as gp,
        ):
            gp.return_value = HealthAssistantBridgeProvider()
            async with AsyncSessionLocal() as db:
                for _ in range(30):
                    result = await _proxy_map(
                        db, ai_rl_ctx["integration_id"], ai_rl_ctx["api_secret"]
                    )
                    assert result == {"mappings": []}
                with pytest.raises(HTTPException) as exc:
                    await _proxy_map(db, ai_rl_ctx["integration_id"], ai_rl_ctx["api_secret"])
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
        assert any(
            key.startswith(
                f"rl:integration_api_proxy_ai:integration:{ai_rl_ctx['integration_id']}:"
            )
            for key in fake_redis.store
        )

    async def test_resume_path_counts_toward_the_ai_bucket(self, ai_rl_ctx, fake_redis, turn_redis):
        """WS4: `POST /ai/sessions/{id}/resume` is an `ai/*` path — it draws
        from the same 30/5-min budget as chat (30 resumes stream through,
        the 31st is a 429 with Retry-After)."""
        from app.api.v1.endpoints import integrations as ep
        from app.core.database import AsyncSessionLocal
        from app.extensions.builtin.health_assistant_bridge.provider import (
            HealthAssistantBridgeProvider,
        )

        async def _fake_resume(self, **kwargs):
            yield 'data: {"event":"flow_finished"}\n\n'

        with (
            patch(
                "app.ai.assistance.service.AIAssistanceService.resume_after_hitl",
                new=lambda self, **kw: _fake_resume(self, **kw),
            ),
            patch.object(ep.integration_registry, "get_provider") as gp,
        ):
            gp.return_value = HealthAssistantBridgeProvider()
            async with AsyncSessionLocal() as db:
                for _ in range(30):
                    response = await _proxy_ai_resume(
                        db,
                        ai_rl_ctx["integration_id"],
                        ai_rl_ctx["api_secret"],
                        ai_rl_ctx["session_id"],
                    )
                    _ = [frame async for frame in response.body_iterator]
                with pytest.raises(HTTPException) as exc:
                    await _proxy_ai_resume(
                        db,
                        ai_rl_ctx["integration_id"],
                        ai_rl_ctx["api_secret"],
                        ai_rl_ctx["session_id"],
                    )
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
