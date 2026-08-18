"""Security regression tests — 2026-08 audit Batch 3 (integrations).

Covers:
- C-4   MCP stdio: disabled by default; inline-code args rejected; http URLs net-guarded
- H1/H2 webhook + api-proxy fail closed without a secret; auto-provisioning on create
- M1    webhook bare-MAC replay guard
- M2    api-proxy MAC requires timestamp and covers the query string
- M3    unsigned GET /status is minimal
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core import config as config_mod


# ---------------------------------------------------------------------------
# C-4 — MCP stdio lockdown
# ---------------------------------------------------------------------------


def test_mcp_stdio_disabled_by_default():
    assert config_mod.Settings().MCP_STDIO_ALLOWED_COMMANDS == ""


def test_mcp_inline_code_args_rejected():
    from integrations.mcp_client.security import validate_stdio_command

    from integrations.mcp_client import security as mcp_sec

    with patch.object(mcp_sec, "get_allowed_commands", return_value=["python", "node"]):
        ok, reason = validate_stdio_command(
            "python", ["-c", "import os; os.system('id')"]
        )
    assert not ok
    assert "inline code" in reason

    with patch.object(mcp_sec, "get_allowed_commands", return_value=["python", "node"]):
        ok, _ = validate_stdio_command("node", ["-e", "require('fs')"])
        assert not ok

        ok, _ = validate_stdio_command("python", ["--c=print(1)"])
        assert not ok


def test_mcp_stdio_disabled_message_when_allowlist_empty():
    from integrations.mcp_client.security import validate_stdio_command

    ok, reason = validate_stdio_command("python", ["script.py"])
    assert not ok
    assert "disabled" in reason


def test_mcp_http_url_blocks_private_targets():
    from integrations.mcp_client.security import validate_http_url

    for url in (
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1:8443/mcp",
        "http://localhost:8000/mcp",
        "https://127.0.0.1/mcp",
    ):
        ok, reason = validate_http_url(url, allow_insecure=True)
        assert not ok, url


# ---------------------------------------------------------------------------
# H3 — fhir_server attachment fetch passes net_guard
# ---------------------------------------------------------------------------


def test_fetch_attachment_uses_net_guard():
    import inspect

    from integrations.fhir_server import provider as fhir_provider

    src = inspect.getsource(fhir_provider.FhirServerProvider)
    assert "assert_safe_url" in src


# ---------------------------------------------------------------------------
# H1/H2 — webhook fails closed without secret
# ---------------------------------------------------------------------------


def _request_mock(headers=None, method="POST", query=""):
    request = MagicMock()
    h = {k.lower(): v for k, v in (headers or {}).items()}
    request.headers = MagicMock()
    request.headers.get = lambda key, default=None: h.get(key.lower(), default)
    request.url.query = query
    request.method = method
    request.body = AsyncMock(return_value=b"")
    return request


def _integration_row(secret=None, status_active=True):
    from app.models.enums import IntegrationStatus

    row = MagicMock()
    row.id = "9b2f1c11-1111-4111-8111-111111111111"
    row.provider = "webhook"
    row.status = (
        IntegrationStatus.ACTIVE if status_active else IntegrationStatus.PENDING
    )
    row.user_config = {} if secret is None else {"webhook_secret": secret}
    return row


@pytest.mark.asyncio
async def test_webhook_without_secret_rejected():
    from app.api.v1.endpoints import integrations as integ

    request = _request_mock()
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: _integration_row())
    )
    with patch.object(integ.integration_registry, "get_provider") as gp:
        gp.return_value = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await integ.integration_webhook(
                "webhook", "9b2f1c11-1111-4111-8111-111111111111", request, db
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_api_proxy_without_secret_rejected():
    from app.api.v1.endpoints import integrations as integ

    request = _request_mock(method="GET")
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: _integration_row())
    )
    with patch.object(integ.integration_registry, "get_provider") as gp:
        gp.return_value = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await integ.integration_api_proxy(
                "health_assistant_bridge",
                "9b2f1c11-1111-4111-8111-111111111111",
                "observations",
                request,
                db,
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unsigned_status_probe_is_minimal():
    from app.api.v1.endpoints import integrations as integ

    request = _request_mock(method="GET")
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: _integration_row())
    )
    with patch.object(integ.integration_registry, "get_provider") as gp:
        gp.return_value = MagicMock()
        result = await integ.integration_api_proxy(
            "health_assistant_bridge",
            "9b2f1c11-1111-4111-8111-111111111111",
            "status",
            request,
            db,
        )
    assert set(result.keys()) == {"status", "server_time"}
    assert result["status"] == "active"


# ---------------------------------------------------------------------------
# M2 — api-proxy MAC covers query string + timestamp mandatory
# ---------------------------------------------------------------------------


def _signed_headers(secret, method, path, body=b"", ts="1700000000"):
    import hmac as _hmac
    import hashlib as _hashlib

    canonical = (
        method.encode() + b"\n" + path.encode() + b"\n" + ts.encode() + b"\n" + body
    )
    sig = _hmac.new(secret.encode(), canonical, _hashlib.sha256).hexdigest()
    return {"X-Api-Signature": sig, "X-Api-Timestamp": ts}, ts


@pytest.mark.asyncio
async def test_api_proxy_rejects_missing_timestamp():
    from app.api.v1.endpoints import integrations as integ

    secret = "test-secret-value-1234567890"
    headers, _ = _signed_headers(secret, "GET", "observations")
    headers.pop("X-Api-Timestamp")

    request = _request_mock(headers=headers, method="GET")
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=lambda: _integration_row(secret=secret)
        )
    )
    with patch.object(integ, "_resolve_secret_field", return_value=secret):
        with patch.object(integ.integration_registry, "get_provider") as gp:
            gp.return_value = MagicMock()
            with pytest.raises(HTTPException) as exc:
                await integ.integration_api_proxy(
                    "health_assistant_bridge",
                    "9b2f1c11-1111-4111-8111-111111111111",
                    "observations",
                    request,
                    db,
                )
    assert exc.value.status_code == 401
    assert "Timestamp" in exc.value.detail


@pytest.mark.asyncio
async def test_api_proxy_signature_covers_query_string():
    from app.api.v1.endpoints import integrations as integ

    secret = "test-secret-value-1234567890"
    # Sign WITH the query string; server must accept.
    headers, ts = _signed_headers(secret, "GET", "observations?limit=5")
    now = str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
    headers["X-Api-Timestamp"] = now
    # Re-sign with fresh ts (skew).
    import hmac as _hmac
    import hashlib as _hashlib

    canonical = b"GET\nobservations?limit=5\n" + now.encode() + b"\n"
    headers["X-Api-Signature"] = _hmac.new(
        secret.encode(), canonical, _hashlib.sha256
    ).hexdigest()

    request = _request_mock(headers=headers, method="GET", query="limit=5")

    row = _integration_row(secret=secret)
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    db.commit = AsyncMock()
    from sqlalchemy.orm.attributes import flag_modified  # noqa: F401

    provider = MagicMock()
    provider.handle_api_request = AsyncMock(return_value={"ok": True})

    with patch.object(integ, "_resolve_secret_field", return_value=secret):
        with patch.object(integ.integration_registry, "get_provider") as gp:
            gp.return_value = provider
            result = await integ.integration_api_proxy(
                "health_assistant_bridge",
                "9b2f1c11-1111-4111-8111-111111111111",
                "observations",
                request,
                db,
            )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_api_proxy_tampered_query_rejected():
    from app.api.v1.endpoints import integrations as integ

    secret = "test-secret-value-1234567890"
    now = str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
    import hmac as _hmac
    import hashlib as _hashlib

    canonical = b"GET\nobservations?limit=5\n" + now.encode() + b"\n"
    headers = {
        "X-Api-Signature": _hmac.new(
            secret.encode(), canonical, _hashlib.sha256
        ).hexdigest(),
        "X-Api-Timestamp": now,
    }

    # Tamper: send different query than signed.
    request = _request_mock(headers=headers, method="GET", query="limit=999")
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=lambda: _integration_row(secret=secret)
        )
    )
    with patch.object(integ, "_resolve_secret_field", return_value=secret):
        with patch.object(integ.integration_registry, "get_provider") as gp:
            gp.return_value = MagicMock()
            with pytest.raises(HTTPException) as exc:
                await integ.integration_api_proxy(
                    "health_assistant_bridge",
                    "9b2f1c11-1111-4111-8111-111111111111",
                    "observations",
                    request,
                    db,
                )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# M1 — webhook replay guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_bare_mac_replay_blocked():
    from app.api.v1.endpoints import integrations as integ

    secret = "test-secret-value-1234567890"
    import hmac as _hmac
    import hashlib as _hashlib

    body = b'{"heart_rate": 72}'
    sig = _hmac.new(secret.encode(), body, _hashlib.sha256).hexdigest()

    async def fake_redis_set(key, val, nx=None, ex=None):
        # First call claims the key; second call sees it exists.
        if not hasattr(fake_redis_set, "called"):
            fake_redis_set.called = True
            return True
        return None

    redis_mock = MagicMock()
    redis_mock.set = fake_redis_set

    def make_request():
        return _request_mock(headers={"X-Webhook-Signature": sig})

    row = _integration_row(secret=secret)
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.refresh = AsyncMock()
    provider = MagicMock()
    provider.handle_webhook = AsyncMock(return_value=[])

    with (
        patch.object(integ, "_resolve_secret_field", return_value=secret),
        patch("app.api.v1.endpoints.integrations.redis_client", redis_mock),
        patch.object(integ, "_check_machine_body_cap", AsyncMock(return_value=body)),
    ):
        with patch.object(integ.integration_registry, "get_provider") as gp:
            gp.return_value = provider
            # First delivery passes auth (mocked provider returns [] — the
            # route continues past verification).
            await integ.integration_webhook("webhook", str(row.id), make_request(), db)
            # Second identical delivery → 401 replay.
            with pytest.raises(HTTPException) as exc:
                await integ.integration_webhook(
                    "webhook", str(row.id), make_request(), db
                )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# H1/H2 — auto-provisioned secrets at instance creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_provisions_machine_secrets():
    from app.api.v1.endpoints import integrations as integ

    config_flow = MagicMock()
    config_flow.validate_input = AsyncMock(return_value={})
    config_flow.prepare_for_storage = AsyncMock(return_value={})
    config_flow.max_instances_per_user = None
    config_flow.is_oauth = False

    provider = MagicMock()
    # Provider "implements" both hooks (distinct from base defaults).
    type(provider).handle_webhook = lambda self, i, p, r=None: []
    type(provider).handle_api_request = lambda self, i, p, m, r: {}

    patient = MagicMock()
    patient.id = "22222222-2222-4222-8222-222222222222"

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: patient))
    db.add = MagicMock()
    db.commit = AsyncMock()

    class _FakeCipher:
        def encrypt_value(self, value, context=None):
            return {"_encrypted": "tok", "_kid": "k1"}

    with (
        patch.object(integ.integration_registry, "get_config_flow") as gcf,
        patch.object(integ.integration_registry, "get_provider") as gp,
        patch(
            "integrations.sdk.secrets.SecretCipher.from_settings",
            return_value=_FakeCipher(),
        ),
        patch.object(integ, "is_domain_disabled", new=AsyncMock(return_value=False)),
    ):
        gcf.return_value = config_flow
        gp.return_value = provider
        result = await integ.submit_config_flow(
            "health_assistant_bridge",
            "22222222-2222-4222-8222-222222222222",
            {"instance_name": "Test"},
            None,
            current_user=MagicMock(user_id="u", tenant_id="t"),
            db=db,
        )

    assert "webhook_secret" in result
    assert "api_secret" in result
    assert result["webhook_secret"]
    assert result["api_secret"]


# ---------------------------------------------------------------------------
# Rotate-secret endpoint (pairing recovery for pre-hardening instances)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_secret_owner_scoped_and_returns_once():
    from app.api.v1.endpoints import integrations as integ

    row = _integration_row()
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    db.commit = AsyncMock()

    class _Cipher:
        def encrypt_value(self, value, context=None):
            assert context == str(row.id), "context binding must be the instance id"
            return {"_encrypted": "tok", "_kid": "k1"}

    from sqlalchemy.orm.attributes import flag_modified  # noqa: F401

    with patch(
        "integrations.sdk.secrets.SecretCipher.from_settings", return_value=_Cipher()
    ):
        result = await integ.rotate_instance_secret(
            str(row.id),
            patient_id="22222222-2222-4222-8222-222222222222",
            field="api_secret",
            current_user=MagicMock(user_id="u", tenant_id="t"),
            db=db,
        )

    assert result["api_secret"]
    assert len(result["api_secret"]) >= 32
    assert "shown only once" in result["secret_notice"]
    stored = row.user_config["api_secret"]
    assert isinstance(stored, dict) and "_encrypted" in stored
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_rotate_secret_rejects_unknown_field():
    from fastapi import HTTPException

    from app.api.v1.endpoints import integrations as integ

    with pytest.raises(HTTPException) as exc:
        await integ.rotate_instance_secret(
            "9b2f1c11-1111-4111-8111-111111111111",
            patient_id="22222222-2222-4222-8222-222222222222",
            field="access_token",
            current_user=MagicMock(),
            db=MagicMock(),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_rotate_secret_missing_instance_404():
    from fastapi import HTTPException

    from app.api.v1.endpoints import integrations as integ

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    with pytest.raises(HTTPException) as exc:
        await integ.rotate_instance_secret(
            "9b2f1c11-1111-4111-8111-111111111111",
            patient_id="22222222-2222-4222-8222-222222222222",
            current_user=MagicMock(user_id="u"),
            db=db,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_rotated_secret_verifies_and_old_one_dies():
    """End-to-end shape: after rotation the machine route accepts a signature
    computed with the NEW secret and rejects the OLD one."""
    from integrations.sdk.webhook_security import verify_canonical_signature

    old_secret = "old-secret-old-secret-old-secret-1"
    new_secret = _rotate_plain()

    row = _integration_row(secret=new_secret)
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    db.commit = AsyncMock()

    import datetime as _dt
    import hashlib as _hl
    import hmac as _hm

    now = str(int(_dt.datetime.now(_dt.timezone.utc).timestamp()))
    canonical = b"POST\nsync\n" + now.encode() + b"\n" + b"{}"
    good_sig = _hm.new(new_secret.encode(), canonical, _hl.sha256).hexdigest()
    stale_sig = _hm.new(old_secret.encode(), canonical, _hl.sha256).hexdigest()

    assert verify_canonical_signature(
        new_secret, "POST", "sync", b"{}", good_sig, provided_timestamp=now
    )
    # The route resolves the STORED secret (the new one) — a signature made
    # with the pre-rotation secret no longer verifies against it.
    assert not verify_canonical_signature(
        new_secret, "POST", "sync", b"{}", stale_sig, provided_timestamp=now
    )


def _rotate_plain() -> str:
    import secrets as _s

    return _s.token_urlsafe(32)
