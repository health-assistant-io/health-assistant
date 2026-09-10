"""Audit 2026-09-11 S-2 — provider api_base SSRF guard.

``guard_api_base`` blocks non-http(s) schemes, unresolvable URLs, and hosts
that resolve to loopback/private/link-local/metadata ranges — including DNS
hostnames mapping internally (the gap the previous literal-IP endpoint check
could not close). Self-hosted local LLMs keep working via
``INTEGRATION_ALLOWED_HOSTS`` / ``INTEGRATION_BLOCK_PRIVATE_RANGES=false`` or
``settings.DEBUG``.
"""

from __future__ import annotations

import pytest

from app.ai.providers.service import guard_api_base


def test_public_url_allowed(monkeypatch):
    import integrations.sdk.net_guard as ng

    monkeypatch.setattr(ng, "assert_safe_url", lambda url, **kw: [])
    guard_api_base("https://api.openai.com/v1")


def test_loopback_literal_blocked(monkeypatch):
    import integrations.sdk.net_guard as ng

    calls = []

    def _fake(url, **kw):
        calls.append(url)
        raise ng.SSRFBlockedError("loopback")

    monkeypatch.setattr(ng, "assert_safe_url", _fake)
    with pytest.raises(ValueError, match="internal, private, or loopback"):
        guard_api_base("http://127.0.0.1:8087/v1")
    assert calls


def test_dns_name_resolving_internally_blocked(monkeypatch):
    import integrations.sdk.net_guard as ng

    def _fake(url, **kw):
        raise ng.SSRFBlockedError("resolves to private")

    monkeypatch.setattr(ng, "assert_safe_url", _fake)
    with pytest.raises(ValueError):
        guard_api_base("https://internal.corp.local/v1")


def test_empty_api_base_is_noop(monkeypatch):
    import integrations.sdk.net_guard as ng

    def _boom(url, **kw):
        raise AssertionError("should not be called")

    monkeypatch.setattr(ng, "assert_safe_url", _boom)
    guard_api_base("")
    guard_api_base(None)


def test_debug_allows_private(monkeypatch):
    from app.core.config import settings
    import integrations.sdk.net_guard as ng

    captured = {}

    def _fake(url, allow_private=False, **kw):
        captured["allow_private"] = allow_private
        return []

    monkeypatch.setattr(ng, "assert_safe_url", _fake)
    monkeypatch.setattr(settings, "DEBUG", True)
    guard_api_base("http://192.168.1.10:11434")
    assert captured["allow_private"] is True


def test_prod_blocks_private(monkeypatch):
    from app.core.config import settings
    import integrations.sdk.net_guard as ng

    captured = {}

    def _fake(url, allow_private=False, **kw):
        captured["allow_private"] = allow_private
        if not allow_private:
            raise ng.SSRFBlockedError("private")
        return []

    monkeypatch.setattr(ng, "assert_safe_url", _fake)
    monkeypatch.setattr(settings, "DEBUG", False)
    with pytest.raises(ValueError):
        guard_api_base("http://192.168.1.10:11434")


async def test_create_provider_rejects_internal_api_base(monkeypatch):
    """Service-level: create_provider with a private api_base raises ValueError."""
    from app.ai.providers.service import AIProviderService
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal
    import integrations.sdk.net_guard as ng

    def _fake(url, allow_private=False, **kw):
        raise ng.SSRFBlockedError("private")

    monkeypatch.setattr(ng, "assert_safe_url", _fake)
    monkeypatch.setattr(settings, "DEBUG", False)

    async with AsyncSessionLocal() as db:
        svc = AIProviderService(db)

        class _Data:
            api_base = "http://10.0.0.5:1234"
            api_key = None

        with pytest.raises(ValueError):
            await svc.create_provider(_Data())
