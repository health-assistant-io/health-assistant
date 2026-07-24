"""Unit tests for ``integrations.sdk.net_guard`` — the SSRF defense layer.

The resolver is injected so the tests never touch the network. The contract
under test:

* Literal-IP hosts are checked directly (no DNS).
* DNS names resolve to *every* address; if *any* is private the URL is blocked.
* Resolution failure is allowed (best-effort) so synthetic hostnames and
  air-gapped runs keep working.
* Env knobs (``INTEGRATION_ALLOWED_HOSTS`` /
  ``INTEGRATION_BLOCK_PRIVATE_RANGES``) and the ``allow_private`` /
  ``allowed_hosts`` args bypass the private check for trusted self-hosted setups.
"""
from __future__ import annotations

import ipaddress

import pytest

from integrations.sdk.exceptions import IntegrationDataError
from integrations.sdk.net_guard import (
    SSRFBlockedError,
    assert_safe_url,
    is_blocked_ip,
)


# ---------------------------------------------------------------------------
# is_blocked_ip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1",
        "127.255.255.255",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # AWS/GCP metadata
        "169.254.170.2",   # ECS metadata
        "::1",             # IPv6 loopback
        "fe80::1",         # IPv6 link-local
        "fc00::1",         # IPv6 unique-local
        "::",              # IPv6 unspecified
        "ff00::1",         # IPv6 multicast
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6 loopback
        "::ffff:169.254.169.254",  # mapped metadata
    ],
)
def test_is_blocked_ip_blocks_risky_addresses(addr):
    assert is_blocked_ip(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize(
    "addr",
    [
        "8.8.8.8",
        "1.1.1.1",
        "104.20.23.154",
        "2606:4700:10::6814:179a",  # public IPv6
    ],
)
def test_is_blocked_ip_allows_public_addresses(addr):
    assert is_blocked_ip(ipaddress.ip_address(addr)) is False


# ---------------------------------------------------------------------------
# assert_safe_url — literal IP hosts (no DNS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:6379/",  # localhost handled via resolver here
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/admin",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
    ],
)
def test_assert_safe_url_blocks_literal_private(url):
    with pytest.raises(SSRFBlockedError):
        assert_safe_url(url)


def test_assert_safe_url_blocks_non_http_scheme():
    with pytest.raises(SSRFBlockedError, match="unsupported scheme"):
        assert_safe_url("file:///etc/passwd")
    with pytest.raises(SSRFBlockedError, match="unsupported scheme"):
        assert_safe_url("gopher://example.com/")


def test_assert_safe_url_blocks_missing_host():
    with pytest.raises(SSRFBlockedError, match="no host"):
        assert_safe_url("http:///path")


def test_assert_safe_url_blocks_with_message_redacts_no_secret():
    """The error string should name the host but not contain anything sensitive
    (there's nothing secret in a URL host, but pin the message shape)."""
    with pytest.raises(IntegrationDataError, match="169.254.169.254"):
        assert_safe_url("http://169.254.169.254/")


def test_assert_safe_url_allows_public_literal_ip():
    assert assert_safe_url("https://8.8.8.8/dns") == ["8.8.8.8"]


def test_assert_safe_url_allow_private_bypasses_literal():
    # allow_private=True skips the private check for trusted self-hosted setups.
    assert assert_safe_url("http://10.0.0.1/", allow_private=True) == ["10.0.0.1"]


def test_assert_safe_url_env_disable_private(monkeypatch):
    monkeypatch.setenv("INTEGRATION_BLOCK_PRIVATE_RANGES", "false")
    assert assert_safe_url("http://127.0.0.1/") == ["127.0.0.1"]


def test_assert_safe_url_allowed_hosts_bypass(monkeypatch):
    monkeypatch.delenv("INTEGRATION_ALLOWED_HOSTS", raising=False)
    # Private IP can't be bypassed by hostname allowlist (it has no hostname),
    # but a DNS name resolving to private space can.
    resolver = lambda host: ["10.0.0.1"]  # noqa: E731
    with pytest.raises(SSRFBlockedError):
        assert_safe_url(
            "https://internal.example.com/",
            resolver=resolver,
            allowed_hosts=set(),
        )
    # allowlisting the hostname lets it through
    assert assert_safe_url(
        "https://internal.example.com/",
        resolver=resolver,
        allowed_hosts={"internal.example.com"},
    ) == ["10.0.0.1"]


def test_assert_safe_url_env_allowed_hosts(monkeypatch):
    monkeypatch.setenv("INTEGRATION_ALLOWED_HOSTS", "lan-fhir.local, my-box")
    resolver = lambda host: ["192.168.1.5"]  # noqa: E731
    assert assert_safe_url("https://lan-fhir.local/fhir", resolver=resolver) == [
        "192.168.1.5"
    ]


# ---------------------------------------------------------------------------
# assert_safe_url — DNS names (injected resolver)
# ---------------------------------------------------------------------------


def test_assert_safe_url_resolves_and_allows_public(monkeypatch):
    monkeypatch.delenv("INTEGRATION_ALLOWED_HOSTS", raising=False)
    resolver = lambda host: ["104.20.23.154", "2606:4700:10::6814:179a"]  # noqa: E731
    assert assert_safe_url("https://example.com/", resolver=resolver) == [
        "104.20.23.154",
        "2606:4700:10::6814:179a",
    ]


def test_assert_safe_url_blocks_when_any_resolved_ip_is_private():
    # Round-robin / dual-stack: one public + one private -> blocked.
    resolver = lambda host: ["8.8.8.8", "10.0.0.1"]  # noqa: E731
    with pytest.raises(SSRFBlockedError, match="resolves to blocked"):
        assert_safe_url("https://dualstack.example.com/", resolver=resolver)


def test_assert_safe_url_allows_on_resolution_failure(monkeypatch):
    """A hostname that won't resolve isn't necessarily an attack (synthetic
    test hostnames, air-gapped box). Best-effort: allow; the request itself
    will fail with a network error if the host is genuinely unreachable."""
    import socket

    def fail(host):
        raise socket.gaierror("DNS failed")

    monkeypatch.delenv("INTEGRATION_ALLOWED_HOSTS", raising=False)
    assert assert_safe_url("https://ehr/", resolver=fail) == []


def test_assert_safe_url_resolver_returns_non_ip_is_skipped():
    """Defensive: a resolver that yields a non-IP string skips it but still
    returns the valid ones."""
    resolver = lambda host: ["not-an-ip", "8.8.8.8"]  # noqa: E731
    assert assert_safe_url("https://example.com/", resolver=resolver) == ["8.8.8.8"]


def test_assert_safe_url_localhost_resolves_to_loopback():
    """A resolver that maps ``localhost`` to 127.0.0.1 must be blocked."""
    resolver = lambda host: ["127.0.0.1"]  # noqa: E731
    with pytest.raises(SSRFBlockedError):
        assert_safe_url("http://localhost:8080/", resolver=resolver)
