"""polite_http — the family Connector Runtime (ADR-0040 clause 6 / plan-32 D5).

One polite-HTTP seam for every T1/T2 connector in the family, vendored
byte-identical from ``dev/contracts/polite_http.py`` (ADR-0031
sync-and-gate; never edited in-repo). Pure functions over an injectable
``httpx.AsyncClient``; the module reads NO environment and NO config
files — every escape hatch is an injectable field of
:class:`NetGuardConfig` / :class:`PoliteHttpConfig` (hosts route their
product settings into those).

Parity is enumerated, not assumed (D5):

* from health's ``net_guard.py`` — scheme allowlist, no-host rejection,
  IP-literal + IPv4-mapped-IPv6 checks, the blocked-range set (loopback,
  link-local incl. cloud metadata, RFC 1918/4193 private, reserved,
  multicast, unspecified), resolve-and-check-EVERY-address,
  resolution-failure = best-effort allow (air-gapped runs, synthetic test
  hostnames), injectable resolver, the guard run off the event loop, and
  ``net-denied`` as a NON-retryable config-class error (logged, the
  instance stays live; config/domains change is the remedy);
* stricter than health — per-hop redirect revalidation (every redirect
  target re-runs the full guard + domain scope + robots + rate check),
  and numeric IPv4 host forms (decimal / octal / hex / short
  ``inet_aton`` forms) are coerced and checked BEFORE any DNS reliance;
* from health's ``sdk/http.py`` — full-jitter exponential backoff with a
  60 s ceiling, RFC 7231 Retry-After in BOTH forms (delta-seconds and
  HTTP-date) honored over computed backoff with the 429 payload
  surfaced as ``retry_after_ms``, the 401/403-immediate + 429/5xx-retried
  + all-others-returned status policy;
* from career's designed runtime (now built) — conditional GET helpers
  (etag / last-modified), per-host token-bucket rate caps, robots.txt
  fetch/cache per host with 5xx ⇒ DENY (fail-closed), and response size
  caps;
* DNS-rebinding TOCTOU mitigation — resolve-then-connect pinning where
  the transport allows it (default on): the checked address is pinned
  into the request URL with the original Host header and
  ``sni_hostname`` extension so TLS still validates the real name.
  Residual (documented, guidelines/security.md): a deployed proxy
  re-resolves the hostname itself, defeating pinning; and a host that
  fails guard-time resolution (best-effort allow) connects by name —
  the rebind window health documented remains in exactly those two
  cases.

Error model: :class:`PoliteHttpError` carries the charter error enum
(``extension-charter.json`` errors.codes — 15 codes, closed
``retryable`` sub-enum). Codes raised here: ``net-denied`` (guard /
robots / domain-scope refusal), ``auth`` (401/403),
``rate-limited`` (429 exhausted, retry hint attached), ``unreachable``
(transport-level failure before an HTTP verdict), ``provider`` (5xx
exhausted, redirect cap exceeded, upstream non-2xx passed through as
typed errors by callers). ``detail`` is capped at
``limits.errorDetailMaxChars`` (500) — never a raw exception dump.

Stdlib-only at import time (``httpx`` is referenced only under
``TYPE_CHECKING``) so contract walls can import the module without the
dependency; the runtime itself needs an ``httpx.AsyncClient``-shaped
client (``build_request`` + ``send`` — ``httpx.MockTransport`` in
tests).
"""

from __future__ import annotations

import asyncio
import ipaddress
import random
import re
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

if TYPE_CHECKING:  # pragma: no cover — typing only, keeps the module stdlib-importable
    import httpx

__all__ = [
    "ERROR_CODES",
    "RETRYABLE_VOCABULARY",
    "DEFAULT_RETRYABLE",
    "ERROR_DETAIL_MAX_CHARS",
    "NetGuardConfig",
    "PoliteHttpConfig",
    "PoliteResponse",
    "PoliteHttpError",
    "HostRateLimiter",
    "RobotsCache",
    "assert_safe_url",
    "check_url",
    "is_blocked_ip",
    "parse_retry_after",
    "backoff_delay",
    "conditional_headers",
    "merge_cache_state",
    "host_matches_domain",
    "pinned_request_parts",
    "polite_get",
]

ERROR_CODES: tuple[str, ...] = (
    "not-ready", "needs-secret", "invalid", "unsupported", "io", "not-found",
    "path-denied", "net-denied", "auth", "rate-limited", "unreachable",
    "protocol", "provider", "budget", "canceled",
)
RETRYABLE_VOCABULARY: tuple[str, ...] = ("yes", "no", "after-delay", "after-config-change")
DEFAULT_RETRYABLE: dict[str, str] = {
    "not-ready": "after-config-change",
    "needs-secret": "after-config-change",
    "invalid": "no",
    "unsupported": "no",
    "io": "no",
    "not-found": "no",
    "path-denied": "no",
    "net-denied": "no",
    "auth": "after-config-change",
    "rate-limited": "after-delay",
    "unreachable": "yes",
    "protocol": "after-config-change",
    "provider": "yes",
    "budget": "after-config-change",
    "canceled": "no",
}
ERROR_DETAIL_MAX_CHARS = 500

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
_BACKOFF_CEILING_SECONDS = 60.0
_SCHEME_ALLOWLIST = frozenset({"http", "https"})
_ROBOTS_PATH = "/robots.txt"


class PoliteHttpError(Exception):
    """A typed charter error raised by the polite runtime.

    ``code`` is one of :data:`ERROR_CODES`; ``retryable`` follows the
    charter's per-code default unless overridden; ``retry_after_ms``
    carries the surfaced 429 payload (Retry-After, RFC 7231 both forms)
    when known; ``status`` is the upstream HTTP status when one was
    received. ``to_dict()`` is the wire shape used in result envelopes'
    ``partial_errors``.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        retryable: str | None = None,
        retry_after_ms: int | None = None,
        status: int | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown charter error code: {code!r}")
        self.code = code
        self.detail = detail[:ERROR_DETAIL_MAX_CHARS]
        self.retryable = retryable or DEFAULT_RETRYABLE[code]
        if self.retryable not in RETRYABLE_VOCABULARY:
            raise ValueError(f"retryable outside the closed sub-enum: {self.retryable!r}")
        self.retry_after_ms = retry_after_ms
        self.status = status
        super().__init__(f"{code}: {self.detail}")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            # 7E wire truth: the serialized detail is the bounded object
            # (`{"message": ...}` for this prose-carrying error); the
            # in-process `.detail` stays the capped diagnostic string.
            "detail": {"message": self.detail[:400]},
            "retryable": self.retryable,
        }
        if self.retry_after_ms is not None:
            payload["retryAfterMs"] = self.retry_after_ms
        return payload


# ---------------------------------------------------------------- SSRF guard


Resolver = Callable[[str], list[str]]
_IpAddr = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class NetGuardConfig:
    """Injectable SSRF-guard policy (health's env hatches, env-free).

    ``allowed_hosts`` bypasses the private-range check for trusted
    self-hosted names; ``allow_private`` skips it wholesale; both are
    host-injected — this module reads no environment.
    """

    allowed_hosts: frozenset[str] = frozenset()
    allow_private: bool = False
    block_private_ranges: bool = True


def is_blocked_ip(ip: _IpAddr) -> bool:
    """True for loopback / link-local / private / reserved / multicast /
    unspecified targets. IPv4-mapped IPv6 is unwrapped first (the
    ``::ffff:127.0.0.1`` form must not slip past the IPv4 rules)."""
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


def _default_resolver(host: str) -> list[str]:
    """OS resolver via getaddrinfo; de-duplicated address strings.

    Raises ``socket.gaierror``/``OSError`` on failure (the caller treats
    a failed lookup as best-effort allow — see :func:`check_url`).
    """
    infos = socket.getaddrinfo(host, None)
    seen: list[str] = []
    for *_, sockaddr in infos:
        addr = str(sockaddr[0])
        if addr not in seen:
            seen.append(addr)
    return seen


def _coerce_numeric_host(host: str) -> _IpAddr | None:
    """Coerce numeric IPv4 forms glibc-style resolvers accept but
    ``ipaddress`` string parsing rejects: decimal (``2130706433``),
    octal (``0177.0.0.1``), hex (``0x7f.0.0.1``) and short
    (``127.1``) — the classic SSRF literal obfuscations. Returns the
    canonical address, or None for real hostnames."""
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return None
    try:
        return ipaddress.IPv4Address(packed)
    except ValueError:
        return None


def _safe_url_for_detail(url: str) -> str:
    """URL form safe to embed in error details: userinfo masked to
    ``***``, query and fragment dropped. Details cross logs and trust
    seams (a T2 pack reads them back over the capability seam), and URLs
    are exactly where credentials and tokens ride — including upstream
    redirect targets the caller never authored (2026-10-18 evaluation,
    H1b). Host and path stay visible for debugging."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    netloc = host
    if host and parsed.port:
        netloc = f"{host}:{parsed.port}"
    if host and (parsed.username or parsed.password):
        netloc = f"***@{netloc}"
    return parsed._replace(netloc=netloc, query="", fragment="").geturl()


def check_url(
    url: str,
    *,
    config: NetGuardConfig | None = None,
    resolver: Resolver | None = None,
) -> list[str]:
    """Synchronous guard check; returns resolved addresses (possibly empty).

    Raises :class:`PoliteHttpError` ``net-denied`` (non-retryable
    config-class) when the scheme is not http/https, the URL has no
    host, the URL carries userinfo credentials, or the host is (or
    resolves to) a blocked address. A DNS name that fails to resolve is
    ALLOWED best-effort — blocking there would break air-gapped runs and
    synthetic test hostnames, and the request itself fails loudly if the
    host is genuinely unreachable (health's documented rationale,
    preserved).

    Guard-denial details embed the URL only through
    :func:`_safe_url_for_detail` (userinfo masked, query and fragment
    dropped): details cross logs and trust seams, and URLs are where
    credentials and tokens ride (2026-10-18 evaluation, H1b).
    """
    cfg = config or NetGuardConfig()
    parsed = urlparse(url)
    if parsed.scheme not in _SCHEME_ALLOWLIST:
        raise PoliteHttpError(
            "net-denied", f"unsupported scheme {parsed.scheme!r} in {_safe_url_for_detail(url)}"
        )
    host = parsed.hostname or ""
    if not host:
        raise PoliteHttpError("net-denied", f"no host in {_safe_url_for_detail(url)}")
    if parsed.username or parsed.password:
        raise PoliteHttpError(
            "net-denied", f"credentials in URL are not allowed: {_safe_url_for_detail(url)}"
        )
    host_lower = host.lower()

    bypass = (
        cfg.allow_private
        or not cfg.block_private_ranges
        or host_lower in {h.lower() for h in cfg.allowed_hosts}
    )

    literal: _IpAddr | None = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = _coerce_numeric_host(host)
    if literal is not None:
        if is_blocked_ip(literal) and not bypass:
            raise PoliteHttpError(
                "net-denied", f"host {host} is a private/loopback/link-local address"
            )
        return [str(literal)]

    resolve = resolver or _default_resolver
    try:
        addresses = resolve(host_lower)
    except (socket.gaierror, OSError):
        return []
    resolved: list[str] = []
    for addr in addresses:
        try:
            ip: _IpAddr = ipaddress.ip_address(addr)
        except ValueError:
            continue
        resolved.append(str(ip))
        if is_blocked_ip(ip) and not bypass:
            raise PoliteHttpError(
                "net-denied", f"host {host} resolves to blocked address {addr}"
            )
    return resolved


async def assert_safe_url(
    url: str,
    *,
    config: NetGuardConfig | None = None,
    resolver: Resolver | None = None,
) -> list[str]:
    """Guard check off the event loop (resolution may block)."""
    return await asyncio.to_thread(check_url, url, config=config, resolver=resolver)


# ------------------------------------------------------- retry & politeness


def parse_retry_after(header_value: str | None) -> float | None:
    """RFC 7231 §7.1.3 — delta-seconds or HTTP-date; None when
    malformed/missing; past dates clamp to 0 (a skewed upstream clock
    must not produce a negative sleep)."""
    if not header_value:
        return None
    value = header_value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


def backoff_delay(attempt: int, base: float = 1.0, *, rng: Callable[[float], float] | None = None) -> float:
    """Full-jitter exponential backoff: uniform in ``[0, base * 2**attempt]``
    capped at 60 s (the AWS full-jitter scheme — independent random waits
    break retry lockstep). ``rng`` maps the cap to the chosen delay so
    tests can pin behavior without sleeping."""
    cap = min(base * (2**attempt), _BACKOFF_CEILING_SECONDS)
    if rng is not None:
        return max(0.0, min(cap, rng(cap)))
    return random.uniform(0.0, cap)


def conditional_headers(state: Mapping[str, object] | None) -> dict[str, str]:
    """Conditional-GET validators from cached sync state → request headers."""
    headers: dict[str, str] = {}
    if not state:
        return headers
    etag = state.get("etag")
    if etag:
        headers["If-None-Match"] = str(etag)
    last_modified = state.get("last_modified")
    if last_modified:
        headers["If-Modified-Since"] = str(last_modified)
    return headers


def merge_cache_state(
    state: Mapping[str, object] | None,
    etag: str | None,
    last_modified: str | None,
) -> dict[str, object]:
    """Round-trip the sync cursor: copy state, stamp fresh validators."""
    next_state: dict[str, object] = dict(state or {})
    if etag:
        next_state["etag"] = etag
    if last_modified:
        next_state["last_modified"] = last_modified
    return next_state


def host_matches_domain(host: str, domain: str) -> bool:
    """Declared-domain scope: exact match, or a leading ``*.`` wildcard
    matching the host's own subdomains (one-or-more labels)."""
    host_lower = host.lower().rstrip(".")
    domain_lower = domain.lower().rstrip(".")
    if domain_lower.startswith("*."):
        suffix = domain_lower[1:]
        return host_lower.endswith(suffix) and len(host_lower) > len(suffix)
    return host_lower == domain_lower


class HostRateLimiter:
    """Per-host token bucket (capacity = ``requests_per_minute`` tokens,
    continuous refill at ``rpm / 60`` per second). The first request on a
    fresh host never waits. ``acquire`` returns the seconds to wait BEFORE
    sending (the caller sleeps via the injectable sleep); the token is
    reserved, so concurrent callers queue fairly. Injectable ``clock``
    keeps tests off the wall clock."""

    def __init__(self, *, requests_per_minute: int = 30, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = max(1, requests_per_minute)
        self.rate = requests_per_minute / 60.0
        self._clock = clock
        self._state: dict[str, tuple[float, float]] = {}

    def acquire(self, host: str) -> float:
        now = self._clock()
        tokens, last = self._state.get(host.lower(), (float(self.capacity), now))
        tokens = min(float(self.capacity), tokens + max(0.0, now - last) * self.rate)
        if tokens >= 1.0:
            self._state[host.lower()] = (tokens - 1.0, now)
            return 0.0
        wait = (1.0 - tokens) / self.rate
        self._state[host.lower()] = (tokens + wait * self.rate - 1.0, now + wait)
        return wait


class _RobotRules:
    """Minimal robots.txt group parser (REP semantics): agent groups
    (longest product-token match, ``*`` fallback), ``Allow``/``Disallow``
    path rules with ``*`` and ``$`` wildcards, longest-pattern-wins with
    Allow breaking ties. Empty ``Disallow:`` allows all. Unknown keys and
    blank lines are ignored."""

    _KEY = re.compile(r"^(user-agent|allow|disallow|crawl-delay)\s*:\s*(.*)$", re.IGNORECASE)

    def __init__(self, body: str) -> None:
        self.groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        for raw in body.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            match = self._KEY.match(line)
            if match is None:
                continue
            key, value = match.group(1).lower(), match.group(2).strip()
            if key == "user-agent":
                if rules:
                    self.groups.append((agents, rules))
                    agents, rules = [], []
                if value:
                    agents.append(value.lower())
            elif key in ("allow", "disallow"):
                if not agents:
                    continue
                rules.append((key == "allow", value))
        if agents or rules:
            self.groups.append((agents, rules))

    @staticmethod
    def _path_matches(pattern: str, path: str) -> bool:
        if pattern == "":
            return False
        anchor = pattern.endswith("$")
        body = pattern[:-1] if anchor else pattern
        parts = body.split("*")
        pos = 0
        for index, part in enumerate(parts):
            if not part:
                continue
            found = path.find(part, pos)
            if found < 0:
                return False
            if index == 0 and found != 0:
                return False
            pos = found + len(part)
        if anchor and parts[-1] and not path.endswith(parts[-1]):
            return False
        return True

    def _group_for(self, product: str) -> list[tuple[bool, str]]:
        best: list[tuple[bool, str]] | None = None
        best_len = -1
        for agents, rules in self.groups:
            for agent in agents:
                if agent == "*" and best is None and best_len < 0:
                    best = rules
                elif agent == product and len(agent) > best_len:
                    best, best_len = rules, len(agent)
        return best or []

    def can_fetch(self, user_agent: str, path: str) -> bool:
        product = re.split(r"[/\s]", (user_agent or "*").strip(), 1)[0].lower() or "*"
        rules = self._group_for(product)
        verdict = True
        best_len = -1
        for allow, pattern in rules:
            if not self._path_matches(pattern, path):
                continue
            if len(pattern) > best_len or (len(pattern) == best_len and allow):
                verdict = allow
                best_len = len(pattern)
        return verdict


@dataclass
class RobotsCache:
    """Per-host robots.txt verdicts, fetched through the SAME guarded
    transport (guard + rate bucket + size cap) — robots never bypasses
    politeness. Fail-closed law (D23): a 5xx robots response, or any
    failure to obtain a definitive verdict (transport error, size-cap
    truncation), DENIES the fetch; 2xx parses; other 4xx (404 etc.)
    allows all (the REP default — no robots.txt means no restrictions).

    Production wiring fetches via :func:`polite_get` itself (no
    recursion: robots requests skip the robots check). Injectable
    ``clock`` keeps TTL behavior testable off the wall clock."""

    ttl_seconds: float = 3600.0
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, tuple[float, "_RobotRules | None"]] = field(
        default_factory=dict, repr=False
    )

    def verdict_for(self, scheme_host: str) -> tuple[bool, "_RobotRules | None"]:
        """(has_verdict, rules-or-None): a cached definitive verdict, or
        (True, None) meaning 4xx-allow-all, or (False, None) meaning no
        cached verdict yet (the caller must fetch)."""
        entry = self._entries.get(scheme_host)
        if entry is None:
            return False, None
        stamp, rules = entry
        if self.clock() - stamp > self.ttl_seconds:
            return False, None
        return True, rules

    def record(self, scheme_host: str, rules: _RobotRules | None) -> None:
        self._entries[scheme_host] = (self.clock(), rules)

    @staticmethod
    def allows(rules: _RobotRules | None, user_agent: str, path: str) -> bool:
        if rules is None:
            return True
        return rules.can_fetch(user_agent, path)


# ------------------------------------------------------------- the runtime


@dataclass(frozen=True)
class PoliteHttpConfig:
    """One fetch's policy, fully injectable (hosts map their product
    settings + instance scope onto this; the module reads nothing)."""

    guard: NetGuardConfig = NetGuardConfig()
    domains: frozenset[str] = frozenset()
    requests_per_minute: int = 30
    max_response_bytes: int = 4 * 1024 * 1024
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    max_retries: int = 3
    max_redirects: int = 5
    user_agent: str = "Mozilla/5.0 (compatible; NeuronectionPoliteRuntime/1.0; +https://neuronection.dev)"
    respect_robots: bool = True
    pin_resolved_address: bool = True


@dataclass
class PoliteResponse:
    """The normalized result of one polite GET."""

    status: int
    body: str
    etag: str | None = None
    last_modified: str | None = None
    url: str = ""
    truncated: bool = False
    headers: dict[str, str] = field(default_factory=dict)


Sleep = Callable[[float], Awaitable[None]]


async def _read_capped(response, max_bytes: int) -> tuple[str, bool]:
    raw = bytearray()
    truncated = False
    async for chunk in response.aiter_bytes():
        if len(raw) + len(chunk) > max_bytes:
            raw += chunk[: max(0, max_bytes - len(raw))]
            truncated = True
            break
        raw += chunk
    encoding = getattr(response, "encoding", None) or "utf-8"
    return raw.decode(encoding, errors="replace"), truncated


def pinned_request_parts(url: str, resolved: list[str]) -> tuple[str, dict[str, str], dict[str, object]]:
    """Pin the connect target to the guard-checked address (DNS-rebinding
    TOCTOU mitigation): URL host → checked IP, ``Host`` header keeps the
    original name (virtual-host CDNs), ``sni_hostname`` extension keeps
    TLS certificate validation against the real hostname. Hosts wire
    their guarded transports onto this (health's ``guarded_request`` —
    the 2026-10-18 evaluation's H2); an empty resolution list or an
    IP-literal host pins nothing (the caller decides fail-closed
    semantics for the former)."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not resolved:
        return url, {}, {}
    pinned = resolved[0]
    try:
        ipaddress.ip_address(host)
        return url, {}, {}
    except ValueError:
        pass
    netloc = f"[{pinned}]" if ":" in pinned else pinned
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    rewritten = parsed._replace(netloc=netloc).geturl()
    return rewritten, {"host": host}, {"sni_hostname": host}


_pinned_request_parts = pinned_request_parts


async def polite_get(
    url: str,
    *,
    client: "httpx.AsyncClient",
    config: PoliteHttpConfig | None = None,
    headers: Mapping[str, str] | None = None,
    state: Mapping[str, object] | None = None,
    resolver: Resolver | None = None,
    rate: HostRateLimiter | None = None,
    robots: RobotsCache | None = None,
    sleep: Sleep = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    rng: Callable[[float], float] | None = None,
) -> PoliteResponse:
    """The polite GET: SSRF guard + declared-domain scope on EVERY hop,
    robots.txt (fail-closed), per-host token bucket, full-jitter retry
    with RFC 7231 Retry-After, conditional-GET header passthrough, and
    a size-capped body.

    Status policy (health parity): 401/403 → ``auth`` immediately;
    429/5xx retried up to ``max_retries`` then ``rate-limited`` (hint
    attached) / ``provider``; redirects followed manually with the full
    per-hop pipeline re-run; every other status is RETURNED to the
    caller (304 included, so callers implement conditional GET).
    """
    cfg = config or PoliteHttpConfig()
    limiter = rate or HostRateLimiter(requests_per_minute=cfg.requests_per_minute, clock=clock)
    robots_cache = robots or RobotsCache(clock=clock)
    base_headers = {"User-Agent": cfg.user_agent, "Accept": "*/*"}
    if headers:
        base_headers.update(dict(headers))

    current = url
    hop = 0
    while True:
        parsed = urlparse(current)
        host = (parsed.hostname or "").lower()

        if cfg.domains and not any(host_matches_domain(host, d) for d in cfg.domains):
            raise PoliteHttpError(
                "net-denied",
                f"host {host} is outside the declared domain scope",
            )

        resolved = await assert_safe_url(current, config=cfg.guard, resolver=resolver)

        if cfg.respect_robots:
            scheme_host = f"{parsed.scheme}://{host}"
            have, rules = robots_cache.verdict_for(scheme_host)
            if not have:
                robots_url = urljoin(current, _ROBOTS_PATH)
                try:
                    robots_resp = await polite_get(
                        robots_url,
                        client=client,
                        config=PoliteHttpConfig(
                            guard=cfg.guard,
                            domains=cfg.domains,
                            requests_per_minute=cfg.requests_per_minute,
                            max_response_bytes=64 * 1024,
                            connect_timeout=cfg.connect_timeout,
                            read_timeout=cfg.read_timeout,
                            max_retries=1,
                            max_redirects=cfg.max_redirects,
                            user_agent=cfg.user_agent,
                            respect_robots=False,
                            pin_resolved_address=cfg.pin_resolved_address,
                        ),
                        resolver=resolver,
                        rate=limiter,
                        robots=robots_cache,
                        sleep=sleep,
                        clock=clock,
                        rng=rng,
                    )
                except PoliteHttpError as exc:
                    raise PoliteHttpError(
                        "net-denied",
                        f"robots.txt check failed (fail-closed) for {host}: {exc.detail}",
                    ) from exc
                if robots_resp.status >= 500:
                    raise PoliteHttpError(
                        "net-denied",
                        f"robots.txt returned {robots_resp.status} for {host} — denying (fail-closed)",
                    )
                if robots_resp.status >= 400:
                    robots_cache.record(scheme_host, None)
                elif robots_resp.truncated:
                    raise PoliteHttpError(
                        "net-denied",
                        f"robots.txt for {host} exceeded the size cap — denying (fail-closed)",
                    )
                else:
                    robots_cache.record(scheme_host, _RobotRules(robots_resp.body))
                have, rules = robots_cache.verdict_for(scheme_host)
            if have and not RobotsCache.allows(rules, cfg.user_agent, parsed.path or "/"):
                raise PoliteHttpError(
                    "net-denied", f"robots.txt disallows {parsed.path or '/'} on {host}"
                )

        wait = limiter.acquire(host)
        if wait > 0:
            await sleep(wait)

        request_url = current
        extra_headers: dict[str, str] = {}
        extensions: dict[str, object] = {}
        if cfg.pin_resolved_address:
            request_url, extra_headers, extensions = pinned_request_parts(current, resolved)

        import httpx as _httpx  # runtime-only: keeps module import stdlib-clean

        attempt = 0
        last_retry_after: float | None = None
        while True:
            merged = dict(base_headers)
            merged.update(extra_headers)
            request = client.build_request("GET", request_url, headers=merged)
            if extensions:
                request.extensions.update(extensions)
            try:
                response = await client.send(request, stream=True)
            except (_httpx.RequestError, OSError) as exc:
                await _quiet_close(None)
                attempt += 1
                if attempt >= cfg.max_retries:
                    raise PoliteHttpError(
                        "unreachable", f"network error contacting {host}: {exc}"
                    ) from exc
                await sleep(backoff_delay(attempt, rng=rng))
                continue

            status = response.status_code
            if status in (401, 403):
                await _quiet_close(response)
                raise PoliteHttpError(
                    "auth", f"{host} returned {status} (credentials rejected)", status=status
                )
            if status in _RETRY_STATUS:
                attempt += 1
                retry_after_header = response.headers.get("Retry-After")
                wait_seconds = parse_retry_after(retry_after_header)
                if status == 429 and wait_seconds is not None:
                    last_retry_after = wait_seconds
                if wait_seconds is None:
                    wait_seconds = backoff_delay(attempt, rng=rng)
                await _quiet_close(response)
                if attempt >= cfg.max_retries:
                    if status == 429:
                        raise PoliteHttpError(
                            "rate-limited",
                            f"rate limited by {host} after {cfg.max_retries} attempts",
                            retry_after_ms=(
                                int(last_retry_after * 1000) if last_retry_after is not None else None
                            ),
                            status=status,
                        )
                    raise PoliteHttpError(
                        "provider", f"{host} returned {status} after retries", status=status
                    )
                await sleep(wait_seconds)
                continue
            if status in _REDIRECT_STATUS:
                location = response.headers.get("location")
                await _quiet_close(response)
                if not location:
                    raise PoliteHttpError("provider", f"redirect without location from {host}")
                hop += 1
                if hop > cfg.max_redirects:
                    raise PoliteHttpError(
                        "provider", f"too many redirects (>{cfg.max_redirects})"
                    )
                current = urljoin(current, location)
                break

            body, truncated = await _read_capped(response, cfg.max_response_bytes)
            result = PoliteResponse(
                status=status,
                body=body,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                url=current,
                truncated=truncated,
                headers={k: v for k, v in response.headers.items()},
            )
            await _quiet_close(response)
            return result


async def _quiet_close(response) -> None:
    if response is None:
        return
    aclose = getattr(response, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:
            pass
