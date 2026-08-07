"""End-to-end test: encrypted ``api_secret`` / ``webhook_secret`` must decrypt
before HMAC verification.

Regression guard for the bug where the platform endpoint read the
Fernet-encrypted config wrapper (``{"_encrypted": "...", "_kid": "..."}``)
and passed it verbatim to the HMAC verifier. A dict has no ``.encode()``
method, so ``verify_canonical_signature`` raised ``AttributeError`` → HTTP
500, and HMAC auth was non-functional in production whenever a secret was
configured. The unit tests in ``test_integration_api_proxy_hmac.py``
bypassed the config-flow encryption by storing the plaintext secret
directly, so they never caught it.

Post-fix contract pinned here:

1. ``_resolve_secret_field`` decrypts a Fernet-wrapped secret via the
   domain's config flow and returns the plaintext.
2. A non-secret field round-trips unchanged.
3. A masked ``"***"`` / empty value returns ``None`` (no secret configured).
4. Decryption failure (key mismatch) returns ``None`` rather than raising.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.endpoints import integrations as integrations_endpoint
from app.api.v1.endpoints.integrations import _resolve_secret_field


PLAINTEXT_SECRET = "topsecret-topsecret"  # >= 16 chars (config-flow minimum)


def _hmac_hex(secret: str, method: str, path: str, body: bytes, ts: int) -> str:
    canonical = (
        method.upper().encode() + b"\n"
        + path.encode() + b"\n"
        + f"{ts}".encode() + b"\n"
        + body
    )
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


class _FakeFlow:
    """Minimal stand-in for a ``BaseConfigFlow`` with one secret field."""

    def __init__(self, secret_fields: list[str], encrypted_config: dict):
        self._secret_fields = secret_fields
        self._encrypted_config = encrypted_config
        self.decrypt_calls = 0

    def get_secret_fields(self) -> list[str]:
        return list(self._secret_fields)

    def decrypt_for_use(self, config: dict) -> dict:
        self.decrypt_calls += 1
        out = dict(config)
        for f in self._secret_fields:
            if f in out and isinstance(out[f], dict) and "_encrypted" in out[f]:
                # Simulate Fernet decrypt: plaintext round-trips from our
                # pre-baked wrapper. In production this calls SecretCipher.
                out[f] = PLAINTEXT_SECRET
        return out


def test_resolve_secret_field_decrypts_encrypted_wrapper():
    """Encrypted ``{"_encrypted": ...}`` → plaintext via the config flow."""
    cfg = {"api_secret": {"_encrypted": "fake-token", "_kid": "abcd1234"}}
    flow = _FakeFlow(["api_secret"], cfg)
    with patch.object(integrations_endpoint.integration_registry, "get_config_flow", return_value=flow):
        result = _resolve_secret_field("health_assistant_bridge", cfg, "api_secret")
    assert result == PLAINTEXT_SECRET
    assert flow.decrypt_calls == 1


def test_resolve_secret_field_passthrough_for_non_secret_field():
    """A field NOT in ``get_secret_fields()`` is returned as-is (no decrypt)."""
    cfg = {"instance_name": "my-bridge", "api_secret": "plaintext-string"}
    flow = _FakeFlow([], cfg)  # no secret fields declared
    with patch.object(integrations_endpoint.integration_registry, "get_config_flow", return_value=flow):
        assert _resolve_secret_field("d", cfg, "instance_name") == "my-bridge"
        assert _resolve_secret_field("d", cfg, "api_secret") == "plaintext-string"
    assert flow.decrypt_calls == 0


def test_resolve_secret_field_returns_none_for_masked_or_empty():
    """``"***"`` / empty / missing → ``None`` (treated as 'no secret')."""
    flow = _FakeFlow(["api_secret"], {})
    with patch.object(integrations_endpoint.integration_registry, "get_config_flow", return_value=flow):
        assert _resolve_secret_field("d", {"api_secret": "***"}, "api_secret") is None
        assert _resolve_secret_field("d", {"api_secret": ""}, "api_secret") is None
        assert _resolve_secret_field("d", {}, "api_secret") is None
        assert _resolve_secret_field("d", None, "api_secret") is None  # type: ignore[arg-type]
    assert flow.decrypt_calls == 0  # never attempts decrypt on empties


def test_resolve_secret_field_returns_none_on_decrypt_failure():
    """A decrypt error (key rotation mismatch) → ``None``, not an exception."""
    cfg = {"api_secret": {"_encrypted": "bad-token", "_kid": "old"}}
    flow = _FakeFlow(["api_secret"], cfg)

    def _fail(config):
        raise ValueError("Encrypted config value could not be decrypted")

    flow.decrypt_for_use = _fail  # type: ignore[method-assign]
    with patch.object(integrations_endpoint.integration_registry, "get_config_flow", return_value=flow):
        assert _resolve_secret_field("d", cfg, "api_secret") is None


def test_resolve_secret_field_handles_missing_config_flow():
    """No registered config flow → treat field as non-secret (return raw str)."""
    with patch.object(integrations_endpoint.integration_registry, "get_config_flow", return_value=None):
        assert _resolve_secret_field("d", {"api_secret": "raw"}, "api_secret") == "raw"


@pytest.mark.asyncio
async def test_api_proxy_verifies_signature_against_decrypted_secret():
    """The full api-proxy path: encrypted secret in config → decrypt → verify.

    This is the production scenario the bug broke: a real bridge instance
    stores ``api_secret`` Fernet-encrypted, the client signs with the
    plaintext, and the endpoint must decrypt before HMAC comparison.
    """
    from uuid import uuid4

    integration_id = uuid4()
    encrypted_wrapper = {"_encrypted": "fake-token", "_kid": "abcd1234"}
    integration = MagicMock()
    integration.id = integration_id
    integration.provider = "health_assistant_bridge"
    integration.tenant_id = uuid4()
    integration.user_config = {"api_secret": encrypted_wrapper, "instance_name": "ext"}

    flow = _FakeFlow(["api_secret"], integration.user_config)

    body = b'{"records":[]}'
    ts = int(time.time())
    sig = _hmac_hex(PLAINTEXT_SECRET, "POST", "sync", body, ts)

    request = MagicMock()
    request.headers = {
        "X-Api-Signature": sig,
        "X-Api-Timestamp": str(ts),
    }
    request.method = "POST"
    request.body = AsyncMock(return_value=body)

    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=integration))
    )
    db.commit = AsyncMock()

    provider = MagicMock()
    provider.handle_api_request = AsyncMock(return_value={"success": True})

    with patch.object(integrations_endpoint.integration_registry, "get_provider", return_value=provider), \
         patch.object(integrations_endpoint.integration_registry, "get_config_flow", return_value=flow):
        result = await integrations_endpoint.integration_api_proxy(
            domain="health_assistant_bridge",
            integration_id=str(integration_id),
            path="sync",
            request=request,
            db=db,
        )

    # Reached the provider → auth succeeded against the decrypted secret.
    assert result == {"success": True}
    provider.handle_api_request.assert_awaited_once()
    assert flow.decrypt_calls >= 1


# --- helpers ---------------------------------------------------------------
