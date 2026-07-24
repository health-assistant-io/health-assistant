"""SSRF defense for outbound SDK HTTP calls.

Every URL the SDK requests — a user-supplied ``fhir_base_url``, a server-
supplied pagination ``link[rel=next].url``, a SMART discovery endpoint — is
validated here before the request leaves the process. The goal is to stop an
attacker (or a compromised upstream) from redirecting the SDK's pooled HTTP
client at cloud-metadata endpoints (``169.254.169.254``), loopback services
(``localhost:6379`` Redis, ``localhost:5432`` Postgres), or internal admin
endpoints.

Module boundary: stdlib-only (mirrors :mod:`integrations.sdk.webhook_security`).
Must not import from ``app.*`` — the SDK is imported by the app, so a reverse
import would be circular. The only intra-SDK dependency is
:class:`integrations.sdk.exceptions.IntegrationDataError`.

Default posture is **deny private**: loopback, link-local, private
(RFC 1918 / RFC 4193), reserved, multicast, and unspecified addresses are
rejected. An operator who genuinely needs to talk to a private host (e.g. a
self-hosted FHIR server on the LAN) can allowlist the hostname via the
``INTEGRATION_ALLOWED_HOSTS`` env var, set ``INTEGRATION_BLOCK_PRIVATE_RANGES=
false``, or pass ``allow_private=True``.

Resolution policy:

* **Literal IP hosts** (``http://10.0.0.1``) are checked directly — no DNS,
  no failure mode. IPv4-mapped IPv6 (``::ffff:127.0.0.1``) is unwrapped.
* **DNS names** are resolved and **every** returned address is checked
  (covers round-robin and dual-stack). If *any* resolved IP is private the
  request is rejected. If resolution **fails** the request is *allowed*
  (best-effort): we can't prove the host is private, and the upstream call
  itself will fail with a network error anyway. Blocking on resolution
  failure would break offline/air-gapped runs and every test that uses a
  synthetic hostname.

Known limitation: a DNS-rebinding attack (the hostname resolves to a public IP
at check time, then to a private IP at connect time) is not defeated by
resolution alone — defeating it requires pinning the resolved IP through to
the socket, which httpx doesn't expose cleanly. The resolution check still
stops the common vectors (literal-IP metadata endpoints, ``localhost``, and
hostnames that consistently resolve to private space).
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Callable, List, Optional, Set, Union
from urllib.parse import urlparse

from .exceptions import IntegrationDataError

logger = logging.getLogger(__name__)

__all__ = [
    "assert_safe_url",
    "is_blocked_ip",
    "SSRFBlockedError",
]

# A resolver returns a list of address strings (the first element of each
# sockaddr tuple from ``socket.getaddrinfo``). Kept as a loose alias for
# testability — tests inject a fake resolver instead of hitting real DNS.
Resolver = Callable[[str], List[str]]

_IpAddr = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


class SSRFBlockedError(IntegrationDataError):
    """Raised when a URL points at a blocked (private/loopback/…) target.

    Subclass of :class:`IntegrationDataError` so the existing worker/endpoint
    error handling treats an SSRF block like any other bad-upstream error
    (sync logged, instance left ACTIVE — the URL is config, not a transient
    failure).
    """


def _env_allowed_hosts() -> Set[str]:
    raw = os.environ.get("INTEGRATION_ALLOWED_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _block_private_ranges() -> bool:
    val = os.environ.get("INTEGRATION_BLOCK_PRIVATE_RANGES", "true")
    return val.strip().lower() not in ("0", "false", "no", "off")


def is_blocked_ip(ip: _IpAddr) -> bool:
    """Return True if ``ip`` points at a private/loopback/link-local target.

    Covers: loopback, link-local (incl. AWS/GCP metadata ``169.254.169.254``),
    private (RFC 1918 / RFC 4193 unique-local), reserved, multicast, and
    unspecified (``0.0.0.0`` / ``::``). IPv4-mapped IPv6 is unwrapped first.
    """
    # IPv4-mapped IPv6 (::ffff:a.b.c.d) — unwrap so the IPv4 rules apply.
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_private
    )


def _default_resolver(host: str) -> List[str]:
    """Resolve ``host`` to address strings via the OS resolver.

    Returns a de-duplicated list. Raises ``socket.gaierror`` on failure
    (handled by the caller — a failed lookup means "allow best-effort").
    """
    infos = socket.getaddrinfo(host, None)
    seen: List[str] = []
    for *_, sockaddr in infos:
        addr = sockaddr[0]
        if addr not in seen:
            seen.append(addr)
    return seen


def assert_safe_url(
    url: str,
    *,
    allow_private: bool = False,
    allowed_hosts: Optional[Set[str]] = None,
    resolver: Optional[Resolver] = None,
) -> List[str]:
    """Validate ``url`` for SSRF safety and return the resolved addresses.

    Raises :class:`SSRFBlockedError` (an :class:`IntegrationDataError`) when
    the scheme is not ``http``/``https``, the URL has no host, or the host
    resolves to a blocked range.

    Args:
        url: The absolute URL about to be requested.
        allow_private: If True, skip the private-range check (for trusted
            self-hosted setups). Also implicitly True when
            ``INTEGRATION_BLOCK_PRIVATE_RANGES=false``.
        allowed_hosts: Extra hostnames (lowercased) that bypass the private
            check. Merged with ``INTEGRATION_ALLOWED_HOSTS``.
        resolver: Injectable DNS resolver (defaults to the OS resolver) so
            tests don't touch the network.

    Returns:
        The list of resolved address strings (may be empty for a synthetic
        hostname that fails to resolve — the caller still proceeds).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFBlockedError(
            f"Blocked URL {url!r}: unsupported scheme {parsed.scheme!r}."
        )
    host = parsed.hostname or ""
    if not host:
        raise SSRFBlockedError(f"Blocked URL {url!r}: no host.")
    host_lower = host.lower()

    bypass_hosts = set(allowed_hosts or set()) | _env_allowed_hosts()
    bypass = host_lower in {h.lower() for h in bypass_hosts}
    effective_allow = allow_private or not _block_private_ranges() or bypass

    # 1. Literal IP host — check directly, no DNS.
    try:
        literal_ip: _IpAddr = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None  # type: ignore[assignment]
    if literal_ip is not None:
        if is_blocked_ip(literal_ip) and not effective_allow:
            raise SSRFBlockedError(
                f"Blocked URL {url!r}: host {host} is a "
                "private/loopback/link-local address."
            )
        return [str(literal_ip)]

    # 2. DNS name — resolve and check every returned address.
    resolve = resolver or _default_resolver
    try:
        addresses = resolve(host_lower)
    except socket.gaierror as e:
        # Best-effort: a name that won't resolve isn't necessarily an attack
        # (synthetic hostnames in tests, transient DNS outage, air-gapped
        # box). The request itself will fail with a network error if the
        # host is genuinely unreachable, so we don't hard-block here.
        logger.debug("SSRF check: could not resolve %r (%s); allowing.", host, e)
        return []

    resolved: List[str] = []
    for addr in addresses:
        try:
            ip: _IpAddr = ipaddress.ip_address(addr)
        except ValueError:
            # Resolver returned something that isn't an IP literal (e.g. a
            # unix socket path on some platforms). Skip it.
            continue
        resolved.append(str(ip))
        if is_blocked_ip(ip) and not effective_allow:
            raise SSRFBlockedError(
                f"Blocked URL {url!r}: host {host} resolves to blocked "
                f"address {addr}."
            )
    return resolved
