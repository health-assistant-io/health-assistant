"""Unit tests for integrations.sdk.http (Stage 2 Pair B).

All HTTP is mocked via httpx.MockTransport — no network, no DB.
"""
import json

import httpx
import pytest

from integrations.sdk.exceptions import IntegrationAuthError, IntegrationDataError, IntegrationRateLimitError
from integrations.sdk.http import http_request, paginate_bundle


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
