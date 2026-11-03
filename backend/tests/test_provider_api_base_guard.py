"""Audit 2026-09-11 S-2 — provider api_base SSRF guard.

``guard_api_base`` blocks non-http(s) schemes, unresolvable URLs, and hosts
resolving to loopback/private/link-local/metadata ranges. It rides the
vendored family guard (``app.extensions.polite_http.check_url``) configured
from Settings; the failure surfaces as a client-facing ``ValueError`` (the
AI-settings surface maps it to a 4xx, never a 500).

The guard's own behavior wall is ``tests/test_extensions_contract.py`` +
``tests/test_sdk_net_guard.py``; what THIS file pins is the api_base
routing in the AI provider service.
"""

from __future__ import annotations

import pytest

from app.ai.providers.service import guard_api_base


def _patch_settings(monkeypatch, *, debug: bool = False, allow_private: bool = False):
    from app.extensions.runtime import http as runtime_http

    class _S:
        INTEGRATION_ALLOWED_HOSTS = ""
        INTEGRATION_BLOCK_PRIVATE_RANGES = True
        INTEGRATION_ALLOW_PRIVATE = allow_private

    monkeypatch.setattr(runtime_http, "get_settings", lambda: _S)
    import app.ai.providers.service as svc

    monkeypatch.setattr(svc.settings, "DEBUG", debug)


def test_public_url_allowed(monkeypatch):
    _patch_settings(monkeypatch)
    guard_api_base("https://api.openai.com/v1")


def test_loopback_literal_blocked(monkeypatch):
    _patch_settings(monkeypatch)
    with pytest.raises(ValueError, match="internal, private, or loopback"):
        guard_api_base("http://127.0.0.1:8087/v1")


def test_dns_name_resolving_internally_blocked(monkeypatch):
    from app.extensions import polite_http

    def _fake(url, *, config=None, resolver=None):
        raise polite_http.PoliteHttpError(
            "net-denied", "host internal.corp.local resolves to blocked address"
        )

    monkeypatch.setattr(polite_http, "check_url", _fake)
    with pytest.raises(ValueError):
        guard_api_base("https://internal.corp.local/v1")


def test_empty_api_base_is_noop(monkeypatch):
    from app.extensions import polite_http

    def _boom(url, *, config=None, resolver=None):
        raise AssertionError("should not be called")

    monkeypatch.setattr(polite_http, "check_url", _boom)
    guard_api_base("")


def test_debug_allows_private(monkeypatch):
    _patch_settings(monkeypatch, debug=True)
    # Settings.DEBUG routes the trusted-flow bypass (settings-level allow).
    guard_api_base("http://192.168.1.10:11434/v1")


def test_prod_blocks_private(monkeypatch):
    _patch_settings(monkeypatch, debug=False)
    with pytest.raises(ValueError):
        guard_api_base("http://192.168.1.10:11434/v1")


def test_create_provider_rejects_internal_api_base(monkeypatch):
    """The guard runs on the create-provider path too (S-2's fix)."""
    from app.extensions import polite_http

    def _fake(url, *, config=None, resolver=None):
        raise polite_http.PoliteHttpError("net-denied", "blocked")

    monkeypatch.setattr(polite_http, "check_url", _fake)
    with pytest.raises(ValueError):
        guard_api_base("http://10.1.2.3/v1")
