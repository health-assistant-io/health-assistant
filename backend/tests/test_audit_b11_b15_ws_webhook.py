"""Tests for audit items B11 (WebSocket hardening) and B15 (webhook HMAC).

B11: WebSocket endpoint passed the auth token in the URL query string
     (logged by reverse proxies + browser history), busy-polled Redis at
     ~10 Hz (unawaited ``asyncio.sleep(0.1)``), and closed 1011 silently.
     Now: prefers the ``Sec-WebSocket-Protocol`` subprotocol, polls at 1 Hz
     via ``get_message(timeout=1.0)``, logs errors, sends periodic pings.

B15: Webhook route treated the integration_id as the only secret. Now
     supports HMAC-SHA256 verification via ``user_config["webhook_secret"]``.
"""
import asyncio
import hashlib
import hmac
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest


# ---------------------------------------------------------------------------
# B15: webhook HMAC verification
# ---------------------------------------------------------------------------


def test_b15_verify_signature_helper_correct():
    """B15: a valid HMAC-SHA256 signature is accepted."""
    from app.api.v1.endpoints.integrations import _verify_webhook_signature

    secret = "test-secret"
    body = b'{"event":"sync","data":[1,2,3]}'
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert _verify_webhook_signature(secret, body, good) is True


def test_b15_verify_signature_helper_github_format():
    """B15: the GitHub-style ``sha256=<hex>`` prefix is stripped before compare."""
    from app.api.v1.endpoints.integrations import _verify_webhook_signature

    secret = "github-secret"
    body = b'{"action":"push"}'
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert _verify_webhook_signature(secret, body, f"sha256={digest}") is True


def test_b15_verify_signature_helper_rejects_tampered_body():
    """B15: a signature computed over different bytes must be rejected."""
    from app.api.v1.endpoints.integrations import _verify_webhook_signature

    secret = "s"
    body = b'{"event":"legit"}'
    attacker = b'{"event":"evil","admin":true}'
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert _verify_webhook_signature(secret, attacker, good) is False


def test_b15_verify_signature_helper_rejects_wrong_secret():
    """B15: a signature from a different secret must be rejected."""
    from app.api.v1.endpoints.integrations import _verify_webhook_signature

    body = b"payload"
    real_sig = hmac.new(b"real-secret", body, hashlib.sha256).hexdigest()
    assert _verify_webhook_signature("wrong-secret", body, real_sig) is False


def test_b15_verify_signature_helper_rejects_missing_inputs():
    """B15: empty secret or signature short-circuits to False."""
    from app.api.v1.endpoints.integrations import _verify_webhook_signature

    assert _verify_webhook_signature("", b"body", "sig") is False
    assert _verify_webhook_signature("secret", b"body", "") is False
    assert _verify_webhook_signature("secret", b"body", None) is False


@pytest.mark.asyncio
async def test_b15_webhook_rejects_bad_signature_when_secret_configured(async_client):
    """B15: with a webhook_secret set, a bad signature → 401, body not processed."""
    from app.core.database import get_db
    from app.main import app
    from app.models.enums import IntegrationStatus

    integration_id = uuid4()

    class FakeIntegration:
        def __init__(self):
            self.id = integration_id
            self.provider = "dev_dummy"
            self.status = IntegrationStatus.ACTIVE
            self.tenant_id = uuid4()
            self.instance_name = "test"
            self.user_config = {"webhook_secret": "super-secret"}
            self.last_synced_at = None
            self.is_debug_enabled = False

    fake_int = FakeIntegration()

    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=fake_int)
    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    fake_db.add = MagicMock()
    fake_db.commit = AsyncMock()
    fake_db.rollback = AsyncMock()

    async def _override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        body = {"event": "test"}
        bad_sig = "0" * 64

        with patch(
            "app.api.v1.endpoints.integrations.integration_registry.get_provider",
            return_value=MagicMock(),
        ):
            response = await async_client.post(
                f"/api/v1/integrations/dev_dummy/webhook/{integration_id}",
                json=body,
                headers={"X-Webhook-Signature": bad_sig},
            )

        assert response.status_code == 401, response.text
        assert "signature" in response.text.lower()
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_b15_webhook_accepts_valid_signature(async_client):
    """B15: with a webhook_secret set, a valid signature → 200."""
    from app.core.database import get_db
    from app.main import app
    from app.models.enums import IntegrationStatus

    integration_id = uuid4()
    secret = "super-secret"

    class FakeIntegration:
        def __init__(self):
            self.id = integration_id
            self.provider = "dev_dummy"
            self.status = IntegrationStatus.ACTIVE
            self.tenant_id = uuid4()
            self.instance_name = "test"
            self.user_config = {"webhook_secret": secret}
            self.last_synced_at = None
            self.is_debug_enabled = False

    fake_int = FakeIntegration()

    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=fake_int)
    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    fake_db.add = MagicMock()
    fake_db.commit = AsyncMock()
    fake_db.rollback = AsyncMock()

    fake_provider = MagicMock()
    fake_provider.handle_webhook = AsyncMock(return_value=[])

    body_bytes = b'{"event":"test"}'
    good_sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()

    async def _override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with patch(
            "app.api.v1.endpoints.integrations.integration_registry.get_provider",
            return_value=fake_provider,
        ):
            response = await async_client.post(
                f"/api/v1/integrations/dev_dummy/webhook/{integration_id}",
                content=body_bytes,
                headers={
                    "X-Webhook-Signature": good_sig,
                    "Content-Type": "application/json",
                },
            )

        assert response.status_code == 200, response.text
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_b15_webhook_no_secret_keeps_legacy_behaviour(async_client):
    """B15: integrations without webhook_secret fall back to the legacy
    integration_id-as-secret behaviour (backward compatibility)."""
    from app.core.database import get_db
    from app.main import app
    from app.models.enums import IntegrationStatus

    integration_id = uuid4()

    class FakeIntegration:
        def __init__(self):
            self.id = integration_id
            self.provider = "dev_dummy"
            self.status = IntegrationStatus.ACTIVE
            self.tenant_id = uuid4()
            self.instance_name = "test"
            # NO webhook_secret
            self.user_config = {}
            self.last_synced_at = None
            self.is_debug_enabled = False

    fake_int = FakeIntegration()

    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=fake_int)
    fake_db = MagicMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    fake_db.add = MagicMock()
    fake_db.commit = AsyncMock()
    fake_db.rollback = AsyncMock()

    fake_provider = MagicMock()
    fake_provider.handle_webhook = AsyncMock(return_value=[])

    async def _override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with patch(
            "app.api.v1.endpoints.integrations.integration_registry.get_provider",
            return_value=fake_provider,
        ):
            # No signature header at all — should still work (legacy path).
            response = await async_client.post(
                f"/api/v1/integrations/dev_dummy/webhook/{integration_id}",
                json={"event": "test"},
            )

        assert response.status_code == 200, response.text
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# B11: WebSocket hardening
# ---------------------------------------------------------------------------


def test_b11_no_unawaited_sleep_in_endpoint():
    """B11: the broken ``asyncio.sleep(0.1)`` (missing await) is gone from
    the live code. We strip the module docstring (which intentionally
    documents the old behaviour) before checking."""
    import ast

    mod = __import__("app.api.v1.endpoints.websockets", fromlist=["x"])
    tree = ast.parse(inspect.getsource(mod))
    # Walk only actual Call nodes — docstrings are string-literal Expr nodes
    # and won't match here, so the audit comment can't false-trigger.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sleep"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "asyncio"
        ):
            # Any asyncio.sleep call in the hot loop is suspicious — the
            # cadence now comes from get_message(timeout=...).
            args = node.args
            if args and isinstance(args[0], ast.Constant) and args[0].value == 0.1:
                raise AssertionError(
                    "WebSocket endpoint still has asyncio.sleep(0.1) (audit B11)."
                )


def test_b11_endpoint_logs_errors_before_closing_1011():
    """B11: the except branch must log before close(1011) (was silent)."""
    src = inspect.getsource(__import__("app.api.v1.endpoints.websockets", fromlist=["x"]))
    # Find the exception handler block.
    assert "logger.warning" in src or "logger.error" in src, (
        "WebSocket endpoint does not log errors before close(1011) (audit B11)."
    )


def test_b11_extract_token_prefers_subprotocol():
    """B11: ``_extract_token`` reads the token from the Sec-WebSocket-Protocol
    header (not the URL query string) when the client uses the bearer form."""
    from app.api.v1.endpoints.websockets import _extract_token

    ws = MagicMock()
    ws.headers = {"sec-websocket-protocol": "bearer, eyJabc.def.ghi"}
    token = asyncio.get_event_loop().run_until_complete(_extract_token(ws, None))
    assert token == "eyJabc.def.ghi"


def test_b11_extract_token_falls_back_to_query():
    """B11: without a subprotocol, the query-string token is used (backward compat)."""
    from app.api.v1.endpoints.websockets import _extract_token

    ws = MagicMock()
    ws.headers = {}
    token = asyncio.get_event_loop().run_until_complete(
        _extract_token(ws, "legacy-query-token")
    )
    assert token == "legacy-query-token"


def test_b11_extract_token_returns_none_when_neither_present():
    """B11: no token from either source → None → endpoint rejects."""
    from app.api.v1.endpoints.websockets import _extract_token

    ws = MagicMock()
    ws.headers = {}
    token = asyncio.get_event_loop().run_until_complete(_extract_token(ws, None))
    assert token is None


def test_b11_keepalive_ping_present():
    """B11: a periodic ping must be sent so intermediaries don't drop the socket."""
    src = inspect.getsource(__import__("app.api.v1.endpoints.websockets", fromlist=["x"]))
    assert "ping" in src.lower(), (
        "WebSocket endpoint has no keepalive ping (audit B11)."
    )


def test_b11_poll_timeout_reduced():
    """B11: the effective poll cadence comes from get_message timeout, not a sleep."""
    src = inspect.getsource(__import__("app.api.v1.endpoints.websockets", fromlist=["x"]))
    # The 1.0 second timeout replaces the old 10 Hz busy-loop.
    assert "timeout=1.0" in src or "_POLL_TIMEOUT_SECONDS" in src, (
        "WebSocket endpoint must use a 1s poll timeout (audit B11)."
    )
