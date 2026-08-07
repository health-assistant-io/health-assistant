"""Unit tests for integrations.sdk.auth (Stage 2 Pair A).

HTTP is mocked via ``httpx.MockTransport``; the OAuth state store uses a tiny
in-memory async fake (no fakeredis dependency). The token-store cipher is
injected (a throwaway Fernet key) so tests don't depend on settings.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from integrations.sdk.auth import (
    DEFAULT_SCOPES,
    OAuthStateStore,
    OAuthTokenStore,
    SmartOAuth,
    build_authorize_url,
    discover_smart,
    exchange_code,
    generate_pkce,
    generate_state,
    register_client,
    refresh_token,
    _normalize_token,
)
from integrations.sdk.exceptions import IntegrationAuthError, IntegrationDataError
from integrations.sdk.secrets import SecretCipher


# ---------- fixtures ----------

def _mock_client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _cipher():
    return SecretCipher(Fernet.generate_key())


def _integration(user_config=None):
    return SimpleNamespace(id="int-1", user_config=user_config or {})


class _FakeRedis:
    """Minimal async fake of the slice of redis we use (get/set/delete + TTL,
    ``nx`` for SET-not-exists, and ``eval`` for token-checked release)."""

    def __init__(self):
        self._store = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self._store:
            existing = self._store[key]
            # Still live (not expired)?
            if not (existing[1] and datetime.now(timezone.utc).timestamp() >= existing[1]):
                return None
        expiry = datetime.now(timezone.utc).timestamp() + ex if ex is not None else None
        self._store[key] = (value, expiry)
        return True

    async def get(self, key):
        if key not in self._store:
            return None
        value, expiry = self._store[key]
        if expiry and datetime.now(timezone.utc).timestamp() >= expiry:
            self._store.pop(key, None)
            return None
        return value

    async def delete(self, key):
        self._store.pop(key, None)

    async def execute_command(self, command, key, *args):
        """Subset we use: ``GETDEL`` (atomic one-shot consume)."""
        if command.upper() == "GETDEL":
            value = await self.get(key)
            if value is not None:
                await self.delete(key)
            return value
        raise NotImplementedError(command)

    async def eval(self, script, numkeys, key, *args):
        """Token-checked compare-and-delete (the refresh-lock release path)."""
        if script.strip().startswith("if redis.call('GET'"):
            current = await self.get(key)
            if current is not None and current == (args[0] if args else None):
                await self.delete(key)
                return 1
            return 0
        # The GETDEL Lua polyfill (consume): get-then-delete, return value.
        if "redis.call('GET'" in script and "DEL" in script:
            value = await self.get(key)
            if value is not None:
                await self.delete(key)
            return value
        return None


# ---------- pure functions ----------

def test_pkce_shape():
    verifier, challenge, method = generate_pkce()
    assert method == "S256"
    assert 43 <= len(verifier) <= 128
    assert "=" not in challenge and challenge.replace("-", "").replace("_", "").isalnum()


def test_state_is_unique_long():
    assert len(generate_state()) > 20
    assert generate_state() != generate_state()


def test_build_authorize_url_contains_required_params():
    url = build_authorize_url(
        "https://ehr/authorize", "CID", "https://app/cb", "ST", "CH", aud="https://ehr/fhir"
    )
    for expected in (
        "response_type=code", "client_id=CID", "redirect_uri=", "state=ST",
        "code_challenge=CH", "code_challenge_method=S256", "aud=",
    ):
        assert expected in url, expected


def test_normalize_token_adds_expires_at_and_carries_patient():
    tok = _normalize_token(
        {"access_token": "A", "expires_in": 3600, "patient": "999", "scope": "patient/*.read"}
    )
    assert "expires_at" in tok
    assert tok["patient"] == "999"
    # expires_at is a real, near-future ISO timestamp
    assert "T" in tok["expires_at"]


# ---------- discover_smart ----------

@pytest.mark.asyncio
async def test_discover_smart_ok():
    def handler(request):
        assert request.url.path.endswith("/.well-known/smart-configuration")
        return httpx.Response(200, json={
            "authorization_endpoint": "https://ehr/authorize",
            "token_endpoint": "https://ehr/token",
            "registration_endpoint": "https://ehr/register",
            "scopes_supported": ["patient/*.read"],
        })
    async with _mock_client(handler) as http:
        cfg = await discover_smart("https://ehr/fhir/", http)
    assert cfg["token_endpoint"] == "https://ehr/token"


@pytest.mark.asyncio
async def test_discover_smart_missing_endpoints_raises_data_error():
    async with _mock_client(lambda r: httpx.Response(200, json={"foo": "bar"})) as http:
        with pytest.raises(IntegrationDataError):
            await discover_smart("https://ehr/fhir", http)


@pytest.mark.asyncio
async def test_discover_smart_404_raises_data_error():
    async with _mock_client(lambda r: httpx.Response(404, text="nope")) as http:
        with pytest.raises(IntegrationDataError):
            await discover_smart("https://ehr/fhir", http)


@pytest.mark.asyncio
async def test_discover_smart_retries_5xx_via_shared_helper(monkeypatch):
    """OAuth/DCR/token HTTP calls used to be single-shot — a transient 5xx
    on a hospital IdP's load balancer would surface as
    ``IntegrationDataError`` immediately. After routing ``_request_json``
    through ``http._retry_request``, the same call retries with the same
    full-jitter backoff every other SDK HTTP call uses.

    This test calls ``discover_smart`` (the simplest OAuth endpoint) against
    a server that always returns 503, and asserts the handler is hit
    multiple times before the eventual raise.
    """
    # Squat the jittered sleeps so the test doesn't actually wait.
    async def _no_sleep(_):
        return None
    monkeypatch.setattr("integrations.sdk.http.asyncio.sleep", _no_sleep)

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="IdP down")

    async with _mock_client(handler) as http:
        with pytest.raises(IntegrationDataError):
            await discover_smart("https://ehr/fhir", http)

    assert calls["n"] > 1, (
        f"discover_smart should have retried the 503 (was single-shot pre-refactor); "
        f"handler was only hit {calls['n']} time(s)."
    )


@pytest.mark.asyncio
async def test_discover_smart_429_retries_then_raises_rate_limit(monkeypatch):
    """A 429 from the OAuth server must surface as
    :class:`IntegrationRateLimitError` after retries exhaust — the prior
    single-shot implementation raised it on the first 429 without retrying.
    """
    async def _no_sleep(_):
        return None
    monkeypatch.setattr("integrations.sdk.http.asyncio.sleep", _no_sleep)

    from integrations.sdk.exceptions import IntegrationRateLimitError

    async with _mock_client(lambda r: httpx.Response(429)) as http:
        with pytest.raises(IntegrationRateLimitError):
            await discover_smart("https://ehr/fhir", http)


# ---------- DCR ----------

@pytest.mark.asyncio
async def test_register_client_ok():
    def handler(request):
        body = json.loads(request.content)
        assert body["token_endpoint_auth_method"] == "none"
        assert body["redirect_uris"] == ["https://app/cb"]
        return httpx.Response(200, json={"client_id": "CID-123", "client_name": body["client_name"]})
    async with _mock_client(handler) as http:
        reg = await register_client(
            "https://ehr/register", ["https://app/cb"], "Health Assistant", http=http
        )
    assert reg["client_id"] == "CID-123"


@pytest.mark.asyncio
async def test_register_client_missing_client_id_raises():
    async with _mock_client(lambda r: httpx.Response(200, json={"client_name": "x"})) as http:
        with pytest.raises(IntegrationDataError):
            await register_client("https://ehr/register", ["https://app/cb"], "x", http=http)


# ---------- exchange / refresh ----------

@pytest.mark.asyncio
async def test_exchange_code_ok_normalizes():
    def handler(request):
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        return httpx.Response(200, json={
            "access_token": "AT", "refresh_token": "RT",
            "expires_in": 3600, "patient": "pat-1", "scope": "patient/*.read",
        })
    async with _mock_client(handler) as http:
        token = await exchange_code(
            "https://ehr/token", "CODE", "VER", "https://app/cb", "CID", http=http
        )
    assert token["access_token"] == "AT"
    assert token["patient"] == "pat-1"
    assert "expires_at" in token


@pytest.mark.asyncio
async def test_exchange_code_401_raises_auth_error():
    async with _mock_client(lambda r: httpx.Response(401, text="bad code")) as http:
        with pytest.raises(IntegrationAuthError):
            await exchange_code("https://ehr/token", "X", "V", "https://app/cb", "CID", http=http)


@pytest.mark.asyncio
async def test_refresh_token_ok():
    async with _mock_client(
        lambda r: httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600})
    ) as http:
        token = await refresh_token("https://ehr/token", "RT", "CID", http=http)
    assert token["access_token"] == "AT2"


# ---------- OAuthTokenStore ----------

@pytest.mark.asyncio
async def test_token_store_encrypts_and_roundtrips():
    store = OAuthTokenStore(cipher=_cipher())
    integ = _integration()
    token = _normalize_token({
        "access_token": "AT", "refresh_token": "RT", "expires_in": 3600,
        "patient": "pat-1", "token_endpoint": "https://ehr/token", "client_id": "CID",
    })
    store.store(integ, token)
    # access_token is encrypted at rest
    at_rest = integ.user_config["_oauth"]["access_token"]
    assert isinstance(at_rest, dict) and "_encrypted" in at_rest
    # decrypts on read
    assert store.get_access_token(integ) == "AT"
    assert store.get_refresh_token(integ) == "RT"
    assert store.get_patient(integ) == "pat-1"
    assert not store.is_expired(integ)


def test_token_store_expired_when_no_expires_at():
    store = OAuthTokenStore(cipher=_cipher())
    integ = _integration()
    store.store(integ, {"access_token": "AT"})
    assert store.is_expired(integ)


def test_token_store_expired_when_past():
    store = OAuthTokenStore(cipher=_cipher())
    integ = _integration()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.store(integ, {"access_token": "AT", "expires_at": past})
    assert store.is_expired(integ)


@pytest.mark.asyncio
async def test_token_store_refresh_if_needed_refreshes():
    store = OAuthTokenStore(cipher=_cipher())
    integ = _integration()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.store(integ, {
        "access_token": "AT", "refresh_token": "RT", "expires_at": past,
        "token_endpoint": "https://ehr/token", "client_id": "CID",
    })
    async with _mock_client(
        lambda r: httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600})
    ) as http:
        live = await store.refresh_if_needed(integ, http, token_endpoint="https://ehr/token", client_id="CID")
    assert live == "AT2"
    assert store.get_access_token(integ) == "AT2"


@pytest.mark.asyncio
async def test_token_store_refresh_without_token_raises_auth():
    store = OAuthTokenStore(cipher=_cipher())
    integ = _integration()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.store(integ, {"access_token": "AT", "expires_at": past})  # no refresh_token
    async with _mock_client(lambda r: httpx.Response(200, json={})) as http:
        with pytest.raises(IntegrationAuthError):
            await store.refresh_if_needed(integ, http, token_endpoint="https://ehr/token", client_id="CID")


# ---------- OAuthStateStore ----------

@pytest.mark.asyncio
async def test_state_store_issue_and_consume_one_shot():
    store = OAuthStateStore(redis_client=_FakeRedis())
    await store.issue("s1", {"code_verifier": "V", "client_id": "C"})
    payload = await store.consume("s1")
    assert payload == {"code_verifier": "V", "client_id": "C"}
    # second consume returns None (one-shot)
    assert await store.consume("s1") is None


@pytest.mark.asyncio
async def test_state_store_consume_unknown_returns_none():
    store = OAuthStateStore(redis_client=_FakeRedis())
    assert await store.consume("never-issued") is None


@pytest.mark.asyncio
async def test_state_store_forwards_ttl_to_redis():
    fake = _FakeRedis()
    store = OAuthStateStore(redis_client=fake, ttl_seconds=600)
    await store.issue("s2", {"x": 1})
    # the TTL was forwarded: the fake recorded a finite expiry for the key
    _, expiry = fake._store["oauth:state:s2"]
    assert expiry is not None


@pytest.mark.asyncio
async def test_state_store_expires_after_ttl():
    fake = _FakeRedis()
    store = OAuthStateStore(redis_client=fake, ttl_seconds=1)
    await store.issue("s3", {"x": 1})
    await asyncio.sleep(1.1)  # advance past the TTL
    assert await store.consume("s3") is None


# ---------- SmartOAuth end-to-end ----------

@pytest.mark.asyncio
async def test_smart_oauth_begin_and_complete_connect():
    """Full discover -> DCR -> authorize -> callback -> token round-trip, mocked."""
    calls = {"register": 0, "token": 0}

    def handler(request):
        if request.url.path.endswith("/.well-known/smart-configuration"):
            return httpx.Response(200, json={
                "authorization_endpoint": "https://ehr/authorize",
                "token_endpoint": "https://ehr/token",
                "registration_endpoint": "https://ehr/register",
            })
        if request.url.path == "/register":
            calls["register"] += 1
            return httpx.Response(200, json={"client_id": "DCR-CID"})
        if request.url.path == "/token":
            calls["token"] += 1
            return httpx.Response(200, json={
                "access_token": "AT", "refresh_token": "RT",
                "expires_in": 3600, "patient": "pat-42", "scope": DEFAULT_SCOPES,
            })
        return httpx.Response(404)

    fake_redis = _FakeRedis()
    async with _mock_client(handler) as http:
        oauth = SmartOAuth(
            http, token_store=OAuthTokenStore(cipher=_cipher()),
            state_store=OAuthStateStore(redis_client=fake_redis),
        )
        authorize_url, state = await oauth.begin_connect(
            "https://ehr/fhir", "https://app/cb", "Health Assistant",
            extra_state={"integration_id": "int-1", "tenant_id": "t-1"},
        )
        assert "client_id=DCR-CID" in authorize_url and f"state={state}" in authorize_url
        integ = _integration()
        pending = await oauth.states.consume(state)
        assert pending["integration_id"] == "int-1"  # extra_state merged in
        token = await oauth.complete_connect(integ, pending, "THE_CODE")

    assert calls == {"register": 1, "token": 1}
    assert token["patient"] == "pat-42"
    assert integ.user_config["_oauth"]["patient"] == "pat-42"
    # connection metadata persisted so refresh works later
    decrypted = oauth.tokens._read(integ)
    assert decrypted["token_endpoint"] == "https://ehr/token"
    assert decrypted["client_id"] == "DCR-CID"


@pytest.mark.asyncio
async def test_smart_oauth_complete_with_unknown_state_raises_auth():
    async with _mock_client(lambda r: httpx.Response(200, json={})) as http:
        oauth = SmartOAuth(
            http, token_store=OAuthTokenStore(cipher=_cipher()),
            state_store=OAuthStateStore(redis_client=_FakeRedis()),
        )
        with pytest.raises(IntegrationAuthError):
            await oauth.complete_connect(_integration(), {}, "CODE")  # empty pending


# ---------------------------------------------------------------------------
# Phase 2 hardening: PKCE bounds, OAuth 400 mapping, secret redaction
# ---------------------------------------------------------------------------


def test_pkce_rejects_out_of_range_bytes():
    """A caller asking for too few/many random bytes must fail loudly rather
    than produce an out-of-spec verifier (RFC 7636 §4.1: 43-128 chars)."""
    with pytest.raises(ValueError):
        generate_pkce(verifier_bytes=8)   # too short
    with pytest.raises(ValueError):
        generate_pkce(verifier_bytes=200)  # would exceed 128 chars


def test_pkce_default_is_in_spec():
    v, c, m = generate_pkce()
    assert m == "S256"
    assert 43 <= len(v) <= 128
    assert "=" not in c


@pytest.mark.asyncio
async def test_request_json_maps_oauth_400_to_auth_error():
    """A token-endpoint 400 with an RFC 6749 ``error`` field (invalid_grant,
    invalid_client, …) must surface as IntegrationAuthError so the worker
    flips the instance to ERROR + prompts reconnect — not a silent
    IntegrationDataError that leaves it ACTIVE and failing every sync."""
    from integrations.sdk.auth import _request_json

    def handler(request):
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "bad code"},
        )

    async with _mock_client(handler) as http:
        with pytest.raises(IntegrationAuthError, match="invalid_grant"):
            await _request_json(http, "POST", "https://ehr/token", data={"x": 1})


@pytest.mark.asyncio
async def test_request_json_non_oauth_400_still_data_error():
    """A 4xx without an OAuth ``error`` field stays IntegrationDataError."""
    from integrations.sdk.auth import _request_json

    async with _mock_client(lambda r: httpx.Response(422, text="bad shape")) as http:
        with pytest.raises(IntegrationDataError):
            await _request_json(http, "POST", "https://ehr/register", json_body={})


@pytest.mark.asyncio
async def test_exchange_code_redacts_token_in_error():
    """A token response missing access_token must not leak a partial token
    value into the exception string (logs/exc-info)."""
    def handler(request):
        # No access_token key, but a refresh_token that must be redacted.
        return httpx.Response(200, json={"refresh_token": "rt-secret", "scope": "x"})

    async with _mock_client(handler) as http:
        with pytest.raises(IntegrationAuthError) as ei:
            await exchange_code(
                "https://ehr/token", "CODE", "VVV", "https://app/cb", "CID", http=http
            )
    msg = str(ei.value)
    assert "rt-secret" not in msg
    assert "redacted" in msg  # structure present, value masked


@pytest.mark.asyncio
async def test_register_client_redacts_secret_in_error():
    """A DCR response missing client_id must not leak a client_secret."""
    def handler(request):
        return httpx.Response(
            200, json={"client_secret": "DCR-SECRET-XYZ", "client_name": "ha"}
        )

    async with _mock_client(handler) as http:
        with pytest.raises(IntegrationDataError) as ei:
            await register_client(
                "https://ehr/register", ["https://app/cb"], "ha", http=http
            )
    msg = str(ei.value)
    assert "DCR-SECRET-XYZ" not in msg
    assert "client_secret" in msg or "redacted" in msg


# ---------------------------------------------------------------------------
# revoke() routes through the shared _retry_request (SSRF + retry chokepoint)
# ---------------------------------------------------------------------------


def _smart_with_stored_oauth(http, cipher, oauth_blob):
    """Build a SmartOAuth whose token store returns ``oauth_blob`` for any
    integration (the cipher is injected so no settings are read)."""
    from unittest.mock import MagicMock

    store = OAuthTokenStore(cipher=cipher)
    # Bypass encryption: plant the plaintext blob directly as ``_read`` output.
    store._read = lambda integration: dict(oauth_blob)
    smart = SmartOAuth(http, token_store=store, cipher=cipher)
    return smart


@pytest.mark.asyncio
async def test_revoke_routes_through_retry_request_not_raw_post(monkeypatch):
    """Phase 3.1: ``SmartOAuth.revoke`` must call the shared ``_retry_request``
    (which runs ``net_guard.assert_safe_url`` + retries transient failures),
    not ``self.http.post`` directly. Pre-fix revoke was the only SDK HTTP call
    that bypassed both SSRF defense and the retry/backoff contract.
    """
    cipher = SecretCipher(Fernet.generate_key())
    calls = {"retry": 0, "raw_post": 0}

    async with _mock_client(lambda r: httpx.Response(200)) as http:
        # Wrap http.post so we can detect a raw (unguarded) call.
        original_post = http.post

        async def _spy_post(*a, **kw):
            calls["raw_post"] += 1
            return await original_post(*a, **kw)

        http.post = _spy_post  # type: ignore[method-assign]

        import integrations.sdk.http as http_module

        real_retry = http_module._retry_request

        async def _spy_retry(do_request, *, url, method, max_retries=3):
            calls["retry"] += 1
            return await real_retry(do_request, url=url, method=method, max_retries=max_retries)

        monkeypatch.setattr(http_module, "_retry_request", _spy_retry)

        smart = _smart_with_stored_oauth(http, cipher, {
            "revocation_endpoint": "https://ehr/revoke",
            "refresh_token": "rt-xyz",
            "access_token": "at-xyz",
            "client_id": "cid",
        })
        await smart.revoke(SimpleNamespace(id="00000000-0000-0000-0000-000000000001"))

    assert calls["retry"] == 1, "revoke must go through _retry_request"
    assert calls["raw_post"] == 1, "the retry wrapper still ends up calling http.post once"


@pytest.mark.asyncio
async def test_revoke_blocks_ssrf_cloud_metadata_url(monkeypatch):
    """A malicious ``revocation_endpoint`` pointing at a cloud-metadata IP is
    blocked by ``net_guard`` (the SSRF gate inside ``_retry_request``). The
    best-effort ``except`` swallows it — the integration is still deleted."""
    from integrations.sdk.exceptions import IntegrationDataError

    cipher = SecretCipher(Fernet.generate_key())

    async with _mock_client(lambda r: httpx.Response(200)) as http:
        smart = _smart_with_stored_oauth(http, cipher, {
            "revocation_endpoint": "http://169.254.169.254/latest/meta-data/",
            "refresh_token": "rt-xyz",
        })
        # Must not raise — revoke is best-effort.
        await smart.revoke(SimpleNamespace(id="00000000-0000-0000-0000-000000000002"))
        # No assertion on response: the SSRF block is swallowed; the win is
        # that no request reached the metadata IP. (assert_safe_url resolves
        # 169.254.169.254 to a link-local address and rejects it.)


@pytest.mark.asyncio
async def test_revoke_no_revocation_endpoint_is_noop():
    """No ``revocation_endpoint`` advertised → revoke returns immediately
    without any HTTP traffic."""
    cipher = SecretCipher(Fernet.generate_key())
    posted = {"n": 0}

    class _Client:
        async def post(self, *a, **kw):
            posted["n"] += 1
            return httpx.Response(200)

    smart = _smart_with_stored_oauth(_Client(), cipher, {
        "refresh_token": "rt-xyz",
        # no revocation_endpoint
    })
    await smart.revoke(SimpleNamespace(id="00000000-0000-0000-0000-000000000003"))
    assert posted["n"] == 0


@pytest.mark.asyncio
async def test_revoke_no_tokens_is_noop():
    """``_oauth`` blob with neither refresh nor access token → no POST."""
    cipher = SecretCipher(Fernet.generate_key())
    posted = {"n": 0}

    class _Client:
        async def post(self, *a, **kw):
            posted["n"] += 1
            return httpx.Response(200)

    smart = _smart_with_stored_oauth(_Client(), cipher, {
        "revocation_endpoint": "https://ehr/revoke",
    })
    await smart.revoke(SimpleNamespace(id="00000000-0000-0000-0000-000000000004"))
    assert posted["n"] == 0


@pytest.mark.asyncio
async def test_revoke_swallows_failure_best_effort():
    """A network failure during revoke must not propagate — the caller
    (integration delete) proceeds regardless."""
    cipher = SecretCipher(Fernet.generate_key())

    def handler(request):
        raise httpx.ConnectError("network down")

    async with _mock_client(handler) as http:
        smart = _smart_with_stored_oauth(http, cipher, {
            "revocation_endpoint": "https://ehr/revoke",
            "refresh_token": "rt-xyz",
        })
        # Must not raise.
        await smart.revoke(SimpleNamespace(id="00000000-0000-0000-0000-000000000005"))


# ---------------------------------------------------------------------------
# Per-integration refresh lock (Phase 3.2)
# ---------------------------------------------------------------------------


def _smart_with_state_store(http, cipher, fake_redis, oauth_blob):
    """SmartOAuth wired to ``fake_redis`` via the state store (shared client
    so the refresh lock and state store see the same keyspace)."""
    store = OAuthTokenStore(cipher=cipher)
    # ``_read``/``store``/``is_expired`` all consult the same mutable blob so
    # a refresh performed by one caller is visible to concurrent waiters.
    stored = {"blob": dict(oauth_blob)}
    store._read = lambda integration: dict(stored["blob"])

    def _store(integration, token):
        stored["blob"] = dict(token)

    store.store = _store  # type: ignore[method-assign]
    smart = SmartOAuth(
        http,
        token_store=store,
        state_store=OAuthStateStore(redis_client=fake_redis),
        cipher=cipher,
    )
    # ``is_expired`` reads the blob from ``_read`` (our fixed plant); patch it
    # to consult the latest stored blob so concurrent refreshes are visible.
    def _is_expired(integration):
        blob = stored["blob"]
        exp = blob.get("expires_at")
        if not exp:
            return True
        if isinstance(exp, str):
            try:
                exp = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            except ValueError:
                return True
        return datetime.now(timezone.utc) >= exp

    store.is_expired = _is_expired  # type: ignore[method-assign]
    smart._test_blob = stored  # type: ignore[attr-defined]
    return smart


@pytest.mark.asyncio
async def test_concurrent_get_live_token_refreshes_once(monkeypatch):
    """Two concurrent ``get_live_token`` calls on one expired integration must
    trigger exactly ONE ``/token`` POST. Pre-fix, both saw ``is_expired=True``
    and both POSTed — against a single-use refresh_token server the loser's
    token was consumed → next refresh ``invalid_grant`` → spurious reconnect.
    """
    # Squat the poll interval so the waiter doesn't sleep long.
    monkeypatch.setattr("integrations.sdk.auth.REFRESH_LOCK_POLL_INTERVAL", 0.05)

    cipher = SecretCipher(Fernet.generate_key())
    fake_redis = _FakeRedis()

    refresh_calls = {"n": 0}

    def handler(request):
        refresh_calls["n"] += 1
        # Simulate the refresh taking a moment so the waiter actually polls.
        return httpx.Response(200, json={
            "access_token": f"at-{refresh_calls['n']}",
            "refresh_token": f"rt-{refresh_calls['n']}",
            "expires_in": 3600,
            "token_type": "Bearer",
        })

    async with _mock_client(handler) as http:
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        smart = _smart_with_state_store(http, cipher, fake_redis, {
            "access_token": "at-old",
            "refresh_token": "rt-old",
            "token_endpoint": "https://ehr/token",
            "client_id": "cid",
            "expires_at": past,  # expired → triggers refresh
        })
        integration = SimpleNamespace(id="00000000-0000-0000-0000-000000000010")

        # Two concurrent refreshers.
        results = await asyncio.gather(
            smart.get_live_token(integration),
            smart.get_live_token(integration),
        )

    assert refresh_calls["n"] == 1, (
        f"concurrent refresh must hit /token exactly once (single-use token "
        f"safety); got {refresh_calls['n']}"
    )
    # Both callers got a valid token (the refreshed one).
    assert all(r == "at-1" for r in results), results


@pytest.mark.asyncio
async def test_refresh_lock_fails_open_when_redis_down(monkeypatch):
    """Redis unavailable → lock acquisition fails open (returns True), so the
    refresh proceeds unguarded rather than deadlocking. This is the
    pre-lock behaviour — strictly better than blocking the on-use refresh."""
    cipher = SecretCipher(Fernet.generate_key())

    class _BrokenRedis:
        async def set(self, *a, **kw):
            raise ConnectionError("redis down")
        async def eval(self, *a, **kw):
            raise ConnectionError("redis down")
        async def get(self, *a, **kw):
            raise ConnectionError("redis down")
        async def delete(self, *a, **kw):
            raise ConnectionError("redis down")

    refresh_calls = {"n": 0}

    def handler(request):
        refresh_calls["n"] += 1
        return httpx.Response(200, json={
            "access_token": "at-fresh", "refresh_token": "rt-fresh",
            "expires_in": 3600, "token_type": "Bearer",
        })

    async with _mock_client(handler) as http:
        smart = _smart_with_state_store(http, cipher, _BrokenRedis(), {
            "access_token": "at-old", "refresh_token": "rt-old",
            "token_endpoint": "https://ehr/token", "client_id": "cid",
            "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
        })
        result = await smart.get_live_token(SimpleNamespace(id="00000000-0000-0000-0000-000000000011"))

    assert result == "at-fresh"
    assert refresh_calls["n"] == 1


@pytest.mark.asyncio
async def test_get_live_token_returns_cached_when_not_expired():
    """A still-valid token short-circuits before any lock/refresh work."""
    cipher = SecretCipher(Fernet.generate_key())
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    refresh_calls = {"n": 0}

    def handler(request):
        refresh_calls["n"] += 1
        return httpx.Response(200, json={"access_token": "x"})

    async with _mock_client(handler) as http:
        smart = _smart_with_state_store(http, cipher, _FakeRedis(), {
            "access_token": "at-live", "refresh_token": "rt-live",
            "token_endpoint": "https://ehr/token", "client_id": "cid",
            "expires_at": future,
        })
        result = await smart.get_live_token(SimpleNamespace(id="00000000-0000-0000-0000-000000000012"))

    assert result == "at-live"
    assert refresh_calls["n"] == 0


# ---------------------------------------------------------------------------
# force_refresh lock coordination (Phase 3.2 cleanup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_refresh_acquires_lock_and_refreshes(monkeypatch):
    """force_refresh on a free lock: acquires, refreshes, releases. Exactly
    one /token POST."""
    monkeypatch.setattr("integrations.sdk.auth.REFRESH_LOCK_POLL_INTERVAL", 0.05)
    cipher = SecretCipher(Fernet.generate_key())
    fake_redis = _FakeRedis()
    refresh_calls = {"n": 0}

    def handler(request):
        refresh_calls["n"] += 1
        return httpx.Response(200, json={
            "access_token": "at-force", "refresh_token": "rt-force",
            "expires_in": 3600, "token_type": "Bearer",
        })

    async with _mock_client(handler) as http:
        smart = _smart_with_state_store(http, cipher, fake_redis, {
            "access_token": "at-old", "refresh_token": "rt-old",
            "token_endpoint": "https://ehr/token", "client_id": "cid",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        result = await smart.force_refresh(
            SimpleNamespace(id="00000000-0000-0000-0000-000000000020")
        )

    assert result == "at-force"
    assert refresh_calls["n"] == 1


@pytest.mark.asyncio
async def test_force_refresh_lock_held_waits_for_leader(monkeypatch):
    """force_refresh when another caller holds the lock: waits for the
    leader's refresh rather than burning a second single-use refresh_token.
    Exactly one /token POST total (no double-refresh)."""
    monkeypatch.setattr("integrations.sdk.auth.REFRESH_LOCK_POLL_INTERVAL", 0.05)
    cipher = SecretCipher(Fernet.generate_key())
    fake_redis = _FakeRedis()
    refresh_calls = {"n": 0}

    def handler(request):
        refresh_calls["n"] += 1
        return httpx.Response(200, json={
            "access_token": "at-shared", "refresh_token": "rt-shared",
            "expires_in": 3600, "token_type": "Bearer",
        })

    async with _mock_client(handler) as http:
        smart = _smart_with_state_store(http, cipher, fake_redis, {
            "access_token": "at-old", "refresh_token": "rt-old",
            "token_endpoint": "https://ehr/token", "client_id": "cid",
            "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
        })
        integration = SimpleNamespace(id="00000000-0000-0000-0000-000000000021")

        # Pre-acquire the lock so force_refresh hits the held branch. Run the
        # "leader" refresh concurrently so the waiter has something to observe.
        async def _leader():
            acquired = await smart._acquire_refresh_lock(integration)
            assert acquired
            try:
                await smart._do_refresh(integration)
            finally:
                await smart._release_refresh_lock(integration)

        await asyncio.gather(_leader(), smart.force_refresh(integration))

    # One refresh from the leader; the waiter must NOT have refreshed again.
    assert refresh_calls["n"] == 1, (
        f"force_refresh with a held lock must reuse the leader's refresh "
        f"(single-use-token safety); got {refresh_calls['n']} /token calls"
    )


@pytest.mark.asyncio
async def test_force_refresh_no_refresh_token_raises():
    cipher = SecretCipher(Fernet.generate_key())

    async with _mock_client(lambda r: httpx.Response(200, json={})) as http:
        smart = _smart_with_state_store(http, cipher, _FakeRedis(), {
            "access_token": "at-old",
            "token_endpoint": "https://ehr/token", "client_id": "cid",
            # no refresh_token
        })
        with pytest.raises(IntegrationAuthError, match="no refresh_token"):
            await smart.force_refresh(
                SimpleNamespace(id="00000000-0000-0000-0000-000000000022")
            )
