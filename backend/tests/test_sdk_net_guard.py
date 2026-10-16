"""Tests for the polite transport runtime (``app/extensions.runtime.http``).

The vendored SSRF guard itself is fixture-walled by
``tests/test_extensions_contract.py`` (the 49-case hostile wall — D23);
what THIS file pins is health's app-side routing onto it:

* the guard raising the charter ``net-denied`` typed error —
  non-retryable, config-class (the historical SSRFBlock semantics:
  logged, instance stays ACTIVE);
* the Settings escape hatches (``INTEGRATION_ALLOWED_HOSTS`` /
  ``INTEGRATION_BLOCK_PRIVATE_RANGES`` / ``INTEGRATION_ALLOW_PRIVATE``)
  reaching the injectable guard config — the module reads no env itself;
* ``guarded_request``'s status policy: 401/403 immediate typed ``auth``,
  429 exhaustion ``rate-limited`` with the surfaced Retry-After hint,
  5xx exhaustion ``provider``, network exhaustion ``unreachable``, other
  statuses returned to the caller;
* per-hop redirect revalidation (STRICTER than health's history): a
  redirect to a private target raises ``net-denied`` instead of being
  followed.

The resolver is injected so the tests never touch the network.
"""

from __future__ import annotations

import httpx
import pytest

from app.extensions.errors import ExtensionError
from app.extensions.runtime.http import guarded_request, net_guard_config


def _resolver(table: dict[str, list[str]]):
    def resolver(host: str) -> list[str]:
        if host in table:
            return list(table[host])
        raise OSError(f"NXDOMAIN: {host}")

    return resolver


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


RESOLVER = _resolver(
    {"metadata.internal": ["169.254.169.254"], "api.example.com": ["93.184.216.34"]}
)


# ---------------------------------------------------------------------------
# guard routing (settings → injectable config)
# ---------------------------------------------------------------------------


async def test_guard_raises_net_denied_for_private_target(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)
    with pytest.raises(ExtensionError) as exc_info:
        await runtime_http.assert_safe_url("https://169.254.169.254/latest/meta-data/")
    assert exc_info.value.code == "net-denied"
    assert exc_info.value.retryable == "no"


async def test_guard_allows_public_and_synthetic_hosts(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)
    resolved = await runtime_http.assert_safe_url("https://api.example.com/fhir", resolver=RESOLVER)
    assert resolved == ["93.184.216.34"]
    # best-effort allow for unresolvable synthetic names (air-gapped runs)
    assert await runtime_http.assert_safe_url("https://synthetic.test/x") == []


async def test_allowed_hosts_setting_bypasses_private_check(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = "lan-fhir.local"
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)
    cfg = net_guard_config()
    assert "lan-fhir.local" in cfg.allowed_hosts
    resolved = await runtime_http.assert_safe_url(
        "https://lan-fhir.local/fhir", resolver=_resolver({"lan-fhir.local": ["10.0.0.5"]})
    )
    assert resolved == ["10.0.0.5"]


async def test_block_private_ranges_setting_is_the_wholesale_hatch(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = False
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)
    assert await runtime_http.assert_safe_url("http://10.0.0.1:8080/") == ["10.0.0.1"]


# ---------------------------------------------------------------------------
# guarded_request status policy
# ---------------------------------------------------------------------------


async def test_401_raises_auth_immediately(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"error": "invalid_token"})

    async with _client(handler) as client:
        with pytest.raises(ExtensionError) as exc_info:
            await guarded_request(client, "GET", "https://api.example.com/token")
    assert exc_info.value.code == "auth"
    assert len(calls) == 1  # never retried


async def test_429_exhaustion_raises_rate_limited_with_hint(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"})

    async with _client(handler) as client:
        with pytest.raises(ExtensionError) as exc_info:
            await guarded_request(client, "GET", "https://api.example.com/fhir", max_retries=2)
    assert exc_info.value.code == "rate-limited"
    assert exc_info.value.retry_after_ms == 30000
    assert exc_info.value.retryable == "after-delay"


async def test_5xx_exhaustion_raises_provider(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    async with _client(handler) as client:
        with pytest.raises(ExtensionError) as exc_info:
            await guarded_request(client, "GET", "https://api.example.com/fhir", max_retries=2)
    assert exc_info.value.code == "provider"


async def test_network_errors_exhaust_to_unreachable(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection failure", request=request)

    async with _client(handler) as client:
        with pytest.raises(ExtensionError) as exc_info:
            await guarded_request(client, "GET", "https://api.example.com/fhir", max_retries=2)
    assert exc_info.value.code == "unreachable"
    assert exc_info.value.retryable == "yes"


async def test_other_statuses_returned_to_caller(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(412, json={"resourceType": "OperationOutcome"})

    async with _client(handler) as client:
        response = await guarded_request(client, "PUT", "https://api.example.com/Observation")
    assert response.status_code == 412


# ---------------------------------------------------------------------------
# per-hop redirect revalidation — the deliberately stricter behavior
# ---------------------------------------------------------------------------


async def test_redirect_to_private_target_is_net_denied(monkeypatch):
    """A public host redirecting to a private target fails LOUDLY — the
    per-hop revalidation is stricter than health's history (that is the
    point of D5)."""
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.example.com":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/meta-data/"})
        return httpx.Response(200, text="should never be reached")

    async with _client(handler) as client:
        with pytest.raises(ExtensionError) as exc_info:
            await guarded_request(client, "GET", "https://api.example.com/doc", resolver=RESOLVER)
    assert exc_info.value.code == "net-denied"


async def test_public_redirect_is_followed_and_guarded_per_hop(monkeypatch):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = False

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "https://cdn.example.com/final"})
        return httpx.Response(200, json={"ok": True})

    resolver = _resolver(
        {"api.example.com": ["93.184.216.34"], "cdn.example.com": ["93.184.216.35"]}
    )
    async with _client(handler) as client:
        response = await guarded_request(
            client, "GET", "https://api.example.com/start", resolver=resolver
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def _instant_sleep(_seconds: float) -> None:
    return None
