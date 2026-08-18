"""Security regression tests — 2026-08 audit P1 (rate-limit proxy trust + WS)."""

from unittest.mock import MagicMock

import pytest

from app.core.rate_limit import _client_ip


def _request(ip="203.0.113.7", xff=None):
    r = MagicMock()
    r.client = MagicMock(host=ip)
    r.headers = MagicMock()
    h = {"x-forwarded-for": xff} if xff else {}
    r.headers.get = lambda key, default=None: h.get(key.lower(), default)
    return r


def test_no_trusted_proxies_ignores_xff(monkeypatch):
    """AUTH-H1: with TRUSTED_PROXY_COUNT=0 (direct exposure) a spoofed
    X-Forwarded-For must NOT create fresh rate-limit buckets."""
    monkeypatch.setattr(
        "app.core.rate_limit.get_settings",
        lambda: MagicMock(TRUSTED_PROXY_COUNT=0),
    )
    req = _request(ip="198.51.100.9", xff="1.2.3.4, 5.6.7.8")
    assert _client_ip(req) == "198.51.100.9"


def test_one_trusted_proxy_uses_last_hop(monkeypatch):
    monkeypatch.setattr(
        "app.core.rate_limit.get_settings",
        lambda: MagicMock(TRUSTED_PROXY_COUNT=1),
    )
    req = _request(ip="10.0.0.5", xff="6.6.6.6, 198.51.100.9")
    # The proxy appended the real client (rightmost); 6.6.6.6 is spoofed.
    assert _client_ip(req) == "198.51.100.9"


def test_two_trusted_proxies(monkeypatch):
    monkeypatch.setattr(
        "app.core.rate_limit.get_settings",
        lambda: MagicMock(TRUSTED_PROXY_COUNT=2),
    )
    req = _request(ip="10.0.0.5", xff="6.6.6.6, 198.51.100.9, 10.0.0.5")
    assert _client_ip(req) == "198.51.100.9"


def test_no_xff_uses_peer(monkeypatch):
    monkeypatch.setattr(
        "app.core.rate_limit.get_settings",
        lambda: MagicMock(TRUSTED_PROXY_COUNT=1),
    )
    req = _request(ip="198.51.100.9")
    assert _client_ip(req) == "198.51.100.9"


@pytest.mark.asyncio
async def test_ws_query_token_fallback_removed():
    import asyncio

    from app.api.v1.endpoints.websockets import _extract_token
    from unittest.mock import MagicMock

    ws = MagicMock()
    ws.scope = {}
    ws.headers = {}
    assert await _extract_token(ws) is None
