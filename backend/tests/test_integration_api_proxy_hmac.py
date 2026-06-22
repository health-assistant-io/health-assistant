"""Regression tests for audit item B8 — integration API proxy HMAC.

Pre-fix contract: ``integration_api_proxy`` had no auth at all and no HMAC
verification — the integration UUID was the only credential. UUIDs leak
into logs, browser history, JSON responses — anyone who has seen one had
full bidirectional API access for the lifetime of the integration.

Post-fix contract pinned here:
1. When ``api_secret`` is set in ``user_config``, the route requires a
   valid ``X-Api-Signature`` header (HMAC-SHA256 of
   ``METHOD\\n<path>\\n[<timestamp>\\n]<raw_body>``).
2. Without the secret, the legacy UUID-only behaviour is preserved but a
   warning is logged.
3. The signature verifier accepts a timestamp header (replay protection,
   ±5 min skew window) — if the timestamp is present it's folded into
   the signed payload.
4. ``_verify_api_signature`` returns False on tampering, missing fields,
   malformed timestamp, or out-of-window skew.
"""
import hashlib
import hmac
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.endpoints import integrations as integrations_endpoint
from app.api.v1.endpoints.integrations import _verify_api_signature


def _sign(secret: str, method: str, path: str, body: bytes = b"", timestamp: int | None = None) -> str:
    """Mirror the canonical scheme implemented in the route."""
    parts = [method.upper().encode(), b"\n", path.encode(), b"\n"]
    if timestamp is not None:
        parts.append(f"{timestamp}".encode() + b"\n")
    parts.append(body)
    canonical = b"".join(parts)
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# _verify_api_signature unit tests
# ---------------------------------------------------------------------------


def test_verify_api_signature_accepts_valid_signature():
    secret = "topsecret"
    sig = _sign(secret, "POST", "/data", b'{"hello":"world"}')
    assert _verify_api_signature(
        secret=secret,
        method="POST",
        path="/data",
        raw_body=b'{"hello":"world"}',
        provided_signature=sig,
    ) is True


def test_verify_api_signature_rejects_tampered_body():
    secret = "topsecret"
    sig = _sign(secret, "POST", "/data", b'{"hello":"world"}')
    assert _verify_api_signature(
        secret=secret,
        method="POST",
        path="/data",
        raw_body=b'{"hello":"attacker"}',
        provided_signature=sig,
    ) is False


def test_verify_api_signature_rejects_tampered_path():
    secret = "topsecret"
    sig = _sign(secret, "POST", "/legit", b"")
    assert _verify_api_signature(
        secret=secret,
        method="POST",
        path="/admin",
        raw_body=b"",
        provided_signature=sig,
    ) is False


def test_verify_api_signature_rejects_tampered_method():
    secret = "topsecret"
    sig = _sign(secret, "GET", "/data", b"")
    assert _verify_api_signature(
        secret=secret,
        method="DELETE",
        path="/data",
        raw_body=b"",
        provided_signature=sig,
    ) is False


def test_verify_api_signature_rejects_missing_inputs():
    assert _verify_api_signature("", "POST", "/", b"x", "sig") is False
    assert _verify_api_signature("s", "POST", "/", b"x", "") is False
    assert _verify_api_signature("s", "POST", "/", b"x", None) is False


def test_verify_api_signature_accepts_valid_timestamp():
    secret = "topsecret"
    ts = int(time.time())
    sig = _sign(secret, "POST", "/data", b"body", timestamp=ts)
    assert _verify_api_signature(
        secret=secret,
        method="POST",
        path="/data",
        raw_body=b"body",
        provided_signature=sig,
        provided_timestamp=str(ts),
    ) is True


def test_verify_api_signature_rejects_replay_outside_skew_window():
    """A signature whose timestamp is older than max_skew_seconds is rejected."""
    secret = "topsecret"
    old_ts = int(time.time()) - 600  # 10 min ago, > 5 min default skew
    sig = _sign(secret, "POST", "/data", b"body", timestamp=old_ts)
    assert _verify_api_signature(
        secret=secret,
        method="POST",
        path="/data",
        raw_body=b"body",
        provided_signature=sig,
        provided_timestamp=str(old_ts),
    ) is False


def test_verify_api_signature_rejects_future_timestamp():
    """Far-future timestamps (clock skew attack) are rejected."""
    secret = "topsecret"
    future_ts = int(time.time()) + 600
    sig = _sign(secret, "POST", "/data", b"body", timestamp=future_ts)
    assert _verify_api_signature(
        secret=secret,
        method="POST",
        path="/data",
        raw_body=b"body",
        provided_signature=sig,
        provided_timestamp=str(future_ts),
    ) is False


def test_verify_api_signature_rejects_malformed_timestamp():
    """A non-integer timestamp string is rejected (no 500)."""
    secret = "topsecret"
    sig = _sign(secret, "POST", "/data", b"body")
    assert _verify_api_signature(
        secret=secret,
        method="POST",
        path="/data",
        raw_body=b"body",
        provided_signature=sig,
        provided_timestamp="not-a-number",
    ) is False


def test_verify_api_signature_timestamp_folded_into_payload():
    """The timestamp must be part of the signed payload — a valid signature
    without timestamp folded in must NOT verify when timestamp is provided."""
    secret = "topsecret"
    # Sign WITHOUT timestamp, then try to verify WITH timestamp provided.
    sig_no_ts = _sign(secret, "POST", "/data", b"body")
    ts = int(time.time())
    assert _verify_api_signature(
        secret=secret,
        method="POST",
        path="/data",
        raw_body=b"body",
        provided_signature=sig_no_ts,
        provided_timestamp=str(ts),
    ) is False


# ---------------------------------------------------------------------------
# integration_api_proxy route behaviour
# ---------------------------------------------------------------------------


def _integration(api_secret: str | None):
    fake = MagicMock()
    fake.id = uuid.uuid4()
    fake.provider = "withings"
    fake.status = "ACTIVE"
    fake.tenant_id = uuid.uuid4()
    fake.user_config = {"api_secret": api_secret} if api_secret else {}
    fake.is_debug_enabled = False
    return fake


def _request(method: str, body: bytes = b"", headers: dict | None = None):
    req = MagicMock()
    req.method = method
    req.headers = headers or {}
    if body:

        async def _body():
            return body

        req.body = _body
    else:

        async def _empty_body():
            return b""

        req.body = _empty_body
    return req


@pytest.mark.asyncio
async def test_api_proxy_no_secret_legacy_path_with_warning(monkeypatch):
    """No api_secret → legacy UUID-only path is preserved, but a warning
    is logged so operators notice the gap."""
    integration = _integration(api_secret=None)
    request = _request("POST", b'{"x":1}')
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=integration))
    )
    db.commit = AsyncMock()

    provider = MagicMock()
    provider.handle_api_request = AsyncMock(return_value={"ok": True})

    warnings_seen = []

    def _warn(fmt, *a, **kw):
        warnings_seen.append(fmt)

    monkeypatch.setattr(integrations_endpoint.logger, "warning", _warn)

    with patch.object(
        integrations_endpoint, "integration_registry"
    ):
        reg = integrations_endpoint.integration_registry
        reg.get_provider.return_value = provider
        result = await integrations_endpoint.integration_api_proxy(
            domain="withings",
            integration_id=str(integration.id),
            path="data",
            request=request,
            db=db,
        )

    assert result == {"ok": True}
    assert any("api_secret" in w for w in warnings_seen), (
        "Operator must be warned when an integration has no api_secret configured"
    )


@pytest.mark.asyncio
async def test_api_proxy_with_secret_rejects_missing_signature():
    """api_secret set + no X-Api-Signature header → 401."""
    from fastapi import HTTPException

    integration = _integration(api_secret="topsecret")
    request = _request("POST", b'{"x":1}', headers={})
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=integration))
    )

    with patch.object(integrations_endpoint, "integration_registry") as reg:
        reg.get_provider.return_value = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await integrations_endpoint.integration_api_proxy(
                domain="withings",
                integration_id=str(integration.id),
                path="data",
                request=request,
                db=db,
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_api_proxy_with_secret_rejects_invalid_signature():
    """api_secret set + bad X-Api-Signature → 401."""
    from fastapi import HTTPException

    integration = _integration(api_secret="topsecret")
    request = _request(
        "POST", b'{"x":1}', headers={"X-Api-Signature": "deadbeef"}
    )
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=integration))
    )

    with patch.object(integrations_endpoint, "integration_registry") as reg:
        reg.get_provider.return_value = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await integrations_endpoint.integration_api_proxy(
                domain="withings",
                integration_id=str(integration.id),
                path="data",
                request=request,
                db=db,
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_api_proxy_with_secret_accepts_valid_signature():
    """api_secret set + correctly signed body → handler runs."""
    secret = "topsecret"
    integration = _integration(api_secret=secret)
    body = b'{"x":1}'
    sig = _sign(secret, "POST", "data", body)
    request = _request(
        "POST", body, headers={"X-Api-Signature": sig}
    )
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=integration))
    )
    db.commit = AsyncMock()

    provider = MagicMock()
    provider.handle_api_request = AsyncMock(return_value={"received": True})

    with patch.object(integrations_endpoint, "integration_registry") as reg:
        reg.get_provider.return_value = provider
        result = await integrations_endpoint.integration_api_proxy(
            domain="withings",
            integration_id=str(integration.id),
            path="data",
            request=request,
            db=db,
        )

    assert result == {"received": True}
    provider.handle_api_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_proxy_with_secret_accepts_valid_signature_with_timestamp():
    """Timestamp header folded into the signed payload; valid → ok."""
    secret = "topsecret"
    integration = _integration(api_secret=secret)
    body = b'{"x":1}'
    ts = int(time.time())
    sig = _sign(secret, "POST", "data", body, timestamp=ts)
    request = _request(
        "POST",
        body,
        headers={"X-Api-Signature": sig, "X-Api-Timestamp": str(ts)},
    )
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=integration))
    )
    db.commit = AsyncMock()

    provider = MagicMock()
    provider.handle_api_request = AsyncMock(return_value={"ok": True})

    with patch.object(integrations_endpoint, "integration_registry") as reg:
        reg.get_provider.return_value = provider
        result = await integrations_endpoint.integration_api_proxy(
            domain="withings",
            integration_id=str(integration.id),
            path="data",
            request=request,
            db=db,
        )

    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_api_proxy_invalid_uuid_returns_400():
    """Malformed integration_id → 400, not 500."""
    from fastapi import HTTPException

    request = _request("GET")
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await integrations_endpoint.integration_api_proxy(
            domain="withings",
            integration_id="not-a-uuid",
            path="data",
            request=request,
            db=db,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_api_proxy_unknown_integration_returns_404():
    """No matching active integration → 404."""
    from fastapi import HTTPException

    request = _request("GET")
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    with pytest.raises(HTTPException) as exc:
        await integrations_endpoint.integration_api_proxy(
            domain="withings",
            integration_id=str(uuid.uuid4()),
            path="data",
            request=request,
            db=db,
        )
    assert exc.value.status_code == 404
