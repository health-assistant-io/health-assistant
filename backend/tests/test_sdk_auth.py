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
    """Minimal async fake of the slice of redis we use (get/set/delete + TTL)."""

    def __init__(self):
        self._store = {}

    async def set(self, key, value, ex=None):
        expiry = datetime.now(timezone.utc).timestamp() + ex if ex is not None else None
        self._store[key] = (value, expiry)

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
