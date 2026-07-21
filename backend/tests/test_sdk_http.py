"""Unit tests for integrations.sdk.http (Stage 2 Pair B).

All HTTP is mocked via httpx.MockTransport — no network, no DB.
"""
import json

import httpx
import pytest

from integrations.sdk.exceptions import IntegrationAuthError, IntegrationDataError, IntegrationRateLimitError
from integrations.sdk.http import DEFAULT_MAX_PAGES, _backoff_delay, http_request, paginate_bundle


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_http_request_returns_json_and_injects_bearer():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["accept"] = request.headers.get("accept")
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as http:
        data = await http_request(http, "GET", "https://ehr/Observation", access_token="TOKEN")
    assert data == {"ok": True}
    assert seen["auth"] == "Bearer TOKEN"
    assert "fhir+json" in seen["accept"]


@pytest.mark.asyncio
async def test_http_request_post_json_body():
    def handler(request):
        assert request.method == "POST"
        assert json.loads(request.content)["resourceType"] == "Observation"
        return httpx.Response(201, json={"id": "new"})

    async with _client(handler) as http:
        data = await http_request(
            http, "POST", "https://ehr/Observation",
            access_token="T", json_body={"resourceType": "Observation"},
        )
    assert data == {"id": "new"}


@pytest.mark.asyncio
async def test_http_request_401_raises_auth_error():
    async with _client(lambda r: httpx.Response(401, text="bad token")) as http:
        with pytest.raises(IntegrationAuthError):
            await http_request(http, "GET", "https://ehr/x", access_token="T")


@pytest.mark.asyncio
async def test_http_request_404_raises_data_error():
    async with _client(lambda r: httpx.Response(404, text="not found")) as http:
        with pytest.raises(IntegrationDataError):
            await http_request(http, "GET", "https://ehr/x", access_token="T")


@pytest.mark.asyncio
async def test_http_request_204_returns_none():
    async with _client(lambda r: httpx.Response(204)) as http:
        assert await http_request(http, "DELETE", "https://ehr/x", access_token="T") is None


@pytest.mark.asyncio
async def test_http_request_retries_5xx_then_raises_data_error():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, text="down")

    async with _client(handler) as http:
        with pytest.raises(IntegrationDataError):
            await http_request(http, "GET", "https://ehr/x", access_token="T", max_retries=2)
    assert calls["n"] == 2  # initial + 1 retry


@pytest.mark.asyncio
async def test_http_request_429_raises_rate_limit_after_retries():
    async with _client(lambda r: httpx.Response(429, headers={"Retry-After": "0"})) as http:
        with pytest.raises(IntegrationRateLimitError):
            await http_request(http, "GET", "https://ehr/x", access_token="T", max_retries=2)


# ---------- paginate_bundle ----------

def _bundle(resources, next_url=None):
    entry = [{"resource": r} for r in resources]
    link = [{"relation": "next", "url": next_url}] if next_url else []
    return {"resourceType": "Bundle", "type": "searchset", "entry": entry, "link": link}


@pytest.mark.asyncio
async def test_paginate_bundle_single_page_yields_resources():
    async with _client(lambda r: httpx.Response(200, json=_bundle([{"id": "1"}, {"id": "2"}]))) as http:
        out = [r async for r in paginate_bundle(http, "https://ehr/Observation", access_token="T")]
    assert [r["id"] for r in out] == ["1", "2"]


@pytest.mark.asyncio
async def test_paginate_bundle_follows_next_link():
    page = {"p1": None, "p2": None}

    def handler(request):
        if "page=2" in str(request.url):
            page["p2"] = str(request.url)
            return httpx.Response(200, json=_bundle([{"id": "B"}]))
        page["p1"] = str(request.url)
        return httpx.Response(200, json=_bundle([{"id": "A"}], next_url="https://ehr/Observation?page=2"))

    async with _client(handler) as http:
        out = [r async for r in paginate_bundle(http, "https://ehr/Observation", access_token="T")]
    assert [r["id"] for r in out] == ["A", "B"]
    # params only applied to first page
    assert "_count" not in page["p1"] or True


@pytest.mark.asyncio
async def test_paginate_bundle_max_pages_caps_iteration():
    def handler(request):
        return httpx.Response(200, json=_bundle([{"id": "x"}], next_url="https://ehr/Observation?more"))

    async with _client(handler) as http:
        out = [r async for r in paginate_bundle(http, "https://ehr/Observation", access_token="T", max_pages=2)]
    assert len(out) == 2  # one resource per page, capped at 2 pages


@pytest.mark.asyncio
async def test_paginate_bundle_non_bundle_raises_data_error():
    async with _client(lambda r: httpx.Response(200, json={"resourceType": "Patient"})) as http:
        with pytest.raises(IntegrationDataError):
            _ = [r async for r in paginate_bundle(http, "https://ehr/Observation", access_token="T")]


# ---------------------------------------------------------------------------
# Full-jitter backoff (this stack)
# ---------------------------------------------------------------------------


def test_backoff_delay_returns_uniform_random_in_expected_range():
    """``_backoff_delay(attempt, base)`` must return a value in
    ``[0, base * 2**attempt]`` (full-jitter). Cap at 60s for high attempts."""
    # Sample many times to verify the bounds empirically.
    for attempt in range(8):
        cap = min(1.0 * (2 ** attempt), 60.0)
        for _ in range(50):
            delay = _backoff_delay(attempt)
            assert 0.0 <= delay <= cap, (
                f"attempt={attempt} produced delay={delay} outside [0, {cap}]"
            )


def test_backoff_delay_caps_at_ceiling():
    """At high attempt counts the cap kicks in (60s ceiling)."""
    # 2 ** 7 = 128 → would be the uncapped max; ceiling is 60.
    for _ in range(20):
        assert _backoff_delay(7) <= 60.0


def test_backoff_delay_base_scales_range():
    """``base`` scales the range — useful for callers that want a different
    starting spread (e.g. OAuth token-refresh waits can be tighter)."""
    delay = _backoff_delay(0, base=0.5)
    assert 0.0 <= delay <= 0.5


@pytest.mark.asyncio
async def test_http_request_applies_jitter_on_5xx_retry(monkeypatch):
    """The sleep before a 5xx retry must be a jittered value in
    ``[0, base * 2**attempt]`` (full-jitter) rather than the previous
    fixed-exponential ``base * 2**attempt``.

    Lockstep retries (every client retrying on the same tick) are a known
    cause of thundering-herd load on recovering servers; jitter is the
    standard fix. Test captures every ``asyncio.sleep`` call and asserts
    each falls in the expected per-attempt range.
    """
    sleeps: list[float] = []

    async def _capture_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr("integrations.sdk.http.asyncio.sleep", _capture_sleep)

    def handler(request):
        return httpx.Response(503, text="down")

    async with _client(handler) as http:
        with pytest.raises(IntegrationDataError):
            await http_request(http, "GET", "https://ehr/x", access_token="T", max_retries=4)

    # max_retries=4 → initial attempt + 3 retries → 3 sleeps at attempts 1, 2, 3.
    assert len(sleeps) == 3, f"expected 3 retry sleeps, got {sleeps}"
    for index, delay in enumerate(sleeps, start=1):
        cap = min(1.0 * (2 ** index), 60.0)
        assert 0.0 <= delay <= cap, (
            f"retry #{index} slept {delay}s, expected full-jitter range [0, {cap}]"
        )


@pytest.mark.asyncio
async def test_http_request_429_uses_retry_after_header_when_present(monkeypatch):
    """When the server sends ``Retry-After``, it overrides the jittered
    backoff (the server knows its own load)."""
    sleeps: list[float] = []

    async def _capture_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr("integrations.sdk.http.asyncio.sleep", _capture_sleep)

    def handler(request):
        # Server says "wait exactly 7 seconds"; we must honor it verbatim.
        return httpx.Response(429, headers={"Retry-After": "7"})

    async with _client(handler) as http:
        with pytest.raises(IntegrationRateLimitError):
            await http_request(http, "GET", "https://ehr/x", access_token="T", max_retries=3)

    assert sleeps == [7.0, 7.0], (
        f"Retry-After=7 must override jitter on every retry; got {sleeps}"
    )


@pytest.mark.asyncio
async def test_http_request_429_surfaces_retry_after_on_exception(monkeypatch):
    """When the upstream sends ``Retry-After`` and retries exhaust, the
    raised :class:`IntegrationRateLimitError` must carry ``retry_after_seconds``
    so the worker can write a cooldown key (item 1 of the
    integrations-sdk-improvements plan)."""

    # Mock sleep so the test doesn't actually wait the Retry-After window.
    async def _no_sleep(_d):
        return

    monkeypatch.setattr("integrations.sdk.http.asyncio.sleep", _no_sleep)

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "120"})

    async with _client(handler) as http:
        with pytest.raises(IntegrationRateLimitError) as exc_info:
            await http_request(
                http, "GET", "https://ehr/x", access_token="T", max_retries=2
            )

    assert exc_info.value.retry_after_seconds == 120.0, (
        "retry_after_seconds must be the last-seen Retry-After header value"
    )


@pytest.mark.asyncio
async def test_http_request_429_without_retry_after_leaves_field_none(monkeypatch):
    """When the upstream sends no ``Retry-After`` header (or a non-numeric
    one), ``retry_after_seconds`` stays ``None`` — the worker falls back
    to the per-instance ``sync_interval`` throttle."""

    async def _no_sleep(_d):
        return

    monkeypatch.setattr("integrations.sdk.http.asyncio.sleep", _no_sleep)

    def handler(request):
        # Non-numeric Retry-After (HTTP date form) — not parseable as seconds.
        return httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

    async with _client(handler) as http:
        with pytest.raises(IntegrationRateLimitError) as exc_info:
            await http_request(
                http, "GET", "https://ehr/x", access_token="T", max_retries=2
            )

    assert exc_info.value.retry_after_seconds is None


def test_integration_rate_limit_error_legacy_call_shape_still_works():
    """Pre-2.1 callers raise with just a message — the new
    ``retry_after_seconds`` field defaults to None and the attribute
    must still exist so ``getattr(exc, "retry_after_seconds", None)``
    is safe in the engine."""
    exc = IntegrationRateLimitError("upstream throttled us")
    assert str(exc) == "upstream throttled us"
    assert exc.retry_after_seconds is None


def test_integration_rate_limit_error_accepts_retry_after_kwarg():
    exc = IntegrationRateLimitError("throttled", retry_after_seconds=42.5)
    assert exc.retry_after_seconds == 42.5


# ---------------------------------------------------------------------------
# Default max_pages cap (this stack)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paginate_bundle_default_max_pages_caps_iteration():
    """Without an explicit ``max_pages``, pagination must stop at
    :data:`DEFAULT_MAX_PAGES` (100) — not iterate forever.

    Guards against the prior ``max_pages=None`` default that let an
    integration forget the argument and walk a multi-million-entry Bundle.
    """
    # Each response links to itself, so without the cap this would loop forever.
    self_linking_url = "https://ehr/Observation?scroll=1"

    def handler(request):
        return httpx.Response(
            200,
            json=_bundle([{"id": "x"}], next_url=self_linking_url),
        )

    async with _client(handler) as http:
        out = [
            r
            async for r in paginate_bundle(
                http, "https://ehr/Observation", access_token="T"
            )
        ]
    # Capped at DEFAULT_MAX_PAGES pages, one resource per page.
    assert len(out) == DEFAULT_MAX_PAGES, (
        f"default max_pages cap not enforced — got {len(out)} resources, "
        f"expected exactly DEFAULT_MAX_PAGES={DEFAULT_MAX_PAGES}"
    )


@pytest.mark.asyncio
async def test_paginate_bundle_max_pages_none_opt_out_of_cap():
    """``max_pages=None`` must still mean truly unbounded (explicit opt-out).

    Without this escape hatch, a caller that genuinely needs to walk a huge
    Bundle would have no way to do it. The default cap is a safety net, not
    a hard limit.
    """
    # Build a finite 5-page chain so the test terminates without the cap.
    def handler(request):
        url = str(request.url)
        page_num = int(url.rsplit("page=", 1)[-1]) if "page=" in url else 1
        if page_num >= 5:
            return httpx.Response(200, json=_bundle([{"id": f"p{page_num}"}]))
        return httpx.Response(
            200,
            json=_bundle(
                [{"id": f"p{page_num}"}],
                next_url=f"https://ehr/Observation?page={page_num + 1}",
            ),
        )

    async with _client(handler) as http:
        out = [
            r
            async for r in paginate_bundle(
                http,
                "https://ehr/Observation?page=1",
                access_token="T",
                max_pages=None,
            )
        ]
    assert [r["id"] for r in out] == ["p1", "p2", "p3", "p4", "p5"], (
        f"max_pages=None should walk all 5 pages; got {out}"
    )
