"""Tests for the webhook config-flow security model (Phase 5.1).

The generic webhook ingest route is unauthenticated by design (third-party
services POST to it). Historically the UUID-in-URL was the only credential,
which is too weak. The ``webhook_secret`` is now **required**: the config
flow rejects instances without one, and the platform endpoint verifies an
HMAC-SHA256 signature over the raw body before dispatch.
"""
import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.webhook.config_flow import WebhookConfigFlow


# ---------------------------------------------------------------------------
# Config-flow validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_input_rejects_missing_secret():
    flow = WebhookConfigFlow()
    with pytest.raises(ValueError, match="signing secret is required"):
        await flow.validate_input({"instance_name": "x", "parser_type": "basic"})


@pytest.mark.asyncio
async def test_validate_input_rejects_short_secret():
    flow = WebhookConfigFlow()
    with pytest.raises(ValueError, match="at least 16 characters"):
        await flow.validate_input({
            "instance_name": "x", "parser_type": "basic",
            "webhook_secret": "short",
        })


@pytest.mark.asyncio
async def test_validate_input_accepts_strong_secret():
    flow = WebhookConfigFlow()
    out = await flow.validate_input({
        "instance_name": "x", "parser_type": "basic",
        "webhook_secret": "a-very-strong-secret-1234",
    })
    assert out["webhook_secret"] == "a-very-strong-secret-1234"


@pytest.mark.asyncio
async def test_validate_input_strips_whitespace():
    flow = WebhookConfigFlow()
    out = await flow.validate_input({
        "instance_name": "x", "parser_type": "basic",
        "webhook_secret": "  a-very-strong-secret-1234  ",
    })
    assert out["webhook_secret"] == "a-very-strong-secret-1234"


@pytest.mark.asyncio
async def test_schema_marks_secret_required():
    flow = WebhookConfigFlow()
    schema = await flow.get_schema()
    assert "webhook_secret" in schema["data_schema"]["required"]
    props = schema["data_schema"]["properties"]["webhook_secret"]
    assert props["format"] == "password"
    assert props["type"] == "string"


def test_secret_field_declared_for_encryption():
    """``webhook_secret`` must be in get_secret_fields so the platform
    Fernet-encrypts it at rest and masks it on read."""
    assert "webhook_secret" in WebhookConfigFlow().get_secret_fields()


# ---------------------------------------------------------------------------
# Route-level HMAC enforcement (the secret is decryptable via the flow)
# ---------------------------------------------------------------------------


def _hmac(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_webhook_route_accepts_valid_signature():
    """A correctly-signed webhook payload reaches the provider."""
    from uuid import uuid4

    from app.api.v1.endpoints import integrations as endpoint

    secret = "a-very-strong-secret-1234"
    integration_id = uuid4()
    integration = MagicMock()
    integration.id = integration_id
    integration.provider = "webhook"
    integration.tenant_id = uuid4()
    integration.user_config = {"webhook_secret": secret}  # plaintext (test bypass)

    body = b'{"type":"heart_rate","value":72}'
    request = MagicMock()
    request.headers = {"X-Webhook-Signature": _hmac(secret, body)}
    request.body = AsyncMock(return_value=body)

    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=integration))
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    provider = MagicMock()
    provider.handle_webhook = AsyncMock(return_value=[])

    # No secret-field decryption needed in the test (plaintext stored), so
    # a passthrough config-flow stub keeps _resolve_secret_field transparent.
    flow = MagicMock()
    flow.get_secret_fields.return_value = []  # treat as already-plaintext
    flow.decrypt_for_use = lambda cfg: dict(cfg)

    with patch.object(endpoint.integration_registry, "get_provider", return_value=provider), \
         patch.object(endpoint.integration_registry, "get_config_flow", return_value=flow):
        response = await endpoint.integration_webhook(
            domain="webhook",
            integration_id=str(integration_id),
            request=request,
            db=db,
        )

    provider.handle_webhook.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_route_rejects_tampered_signature():
    """A signature that doesn't match the body is rejected with 401."""
    from uuid import uuid4

    from fastapi import HTTPException

    from app.api.v1.endpoints import integrations as endpoint

    secret = "a-very-strong-secret-1234"
    integration_id = uuid4()
    integration = MagicMock()
    integration.id = integration_id
    integration.provider = "webhook"
    integration.tenant_id = uuid4()
    integration.user_config = {"webhook_secret": secret}

    body = b'{"type":"heart_rate","value":72}'
    # Sign a DIFFERENT body → must not verify.
    bad_sig = _hmac(secret, b'{"type":"heart_rate","value":999}')
    request = MagicMock()
    request.headers = {"X-Webhook-Signature": bad_sig}
    request.body = AsyncMock(return_value=body)

    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=integration))
    )

    provider = MagicMock()
    provider.handle_webhook = AsyncMock(return_value=[])

    flow = MagicMock()
    flow.get_secret_fields.return_value = []
    flow.decrypt_for_use = lambda cfg: dict(cfg)

    with patch.object(endpoint.integration_registry, "get_provider", return_value=provider), \
         patch.object(endpoint.integration_registry, "get_config_flow", return_value=flow):
        with pytest.raises(HTTPException) as ei:
            await endpoint.integration_webhook(
                domain="webhook",
                integration_id=str(integration_id),
                request=request,
                db=db,
            )
    assert ei.value.status_code == 401
    provider.handle_webhook.assert_not_awaited()
