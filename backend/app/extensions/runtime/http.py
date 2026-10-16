"""The polite transport runtime — health's D5 convergence onto the
vendored family Connector Runtime.

Everything outbound from a bundled T1 extension funnels through
:func:`guarded_request`: the vendored SSRF guard
(:func:`app.extensions.polite_http.assert_safe_url` — resolve-then-check
every address, reserved ranges, numeric-literal obfuscations, injectable
resolver, off the event loop) re-run on EVERY redirect hop (stricter than
health's history: a redirect to a private target now fails loudly as
``net-denied`` instead of being followed), the full-jitter retry core
with RFC 7231 Retry-After in both forms, the 401/403-immediate +
429/5xx-retried + others-returned status policy, and the charter typed
errors (:class:`app.extensions.errors.ExtensionError`).

Product knobs are routed through Settings (the module reads NO
environment): ``INTEGRATION_ALLOWED_HOSTS`` (trusted self-hosted names
that bypass the private-range check), ``INTEGRATION_BLOCK_PRIVATE_RANGES``
(wholesale hatch, default on) and ``INTEGRATION_ALLOW_PRIVATE`` (default
off). Per-call ``allow_private`` stays available for trusted flows.

The pooled per-provider ``httpx.AsyncClient`` stays the injected
transport (built with ``follow_redirects=False`` — this runtime walks
redirects itself so every hop is re-guarded). FHIR domain concerns
(Bundle pagination, ``Accept: fhir+json``, OperationOutcome diagnostics)
live in :mod:`app.extensions.sdk.fhir`; the Bearer/refresh lifecycle in
:mod:`app.extensions.sdk.auth`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import get_settings
from app.extensions import polite_http
from app.extensions.errors import ExtensionError
from app.extensions.polite_http import NetGuardConfig, backoff_delay, parse_retry_after

logger = logging.getLogger(__name__)

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


def net_guard_config(
    *,
    allow_private: bool = False,
    extra_allowed_hosts: frozenset[str] | set[str] = frozenset(),
) -> NetGuardConfig:
    """The guard policy from Settings (injectable — tests pass a config)."""
    settings = get_settings()
    allowed = {
        h.strip().lower()
        for h in (settings.INTEGRATION_ALLOWED_HOSTS or "").split(",")
        if h.strip()
    }
    allowed |= {h.lower() for h in extra_allowed_hosts}
    return NetGuardConfig(
        allowed_hosts=frozenset(allowed),
        allow_private=allow_private or settings.INTEGRATION_ALLOW_PRIVATE,
        block_private_ranges=settings.INTEGRATION_BLOCK_PRIVATE_RANGES,
    )


async def assert_safe_url(
    url: str,
    *,
    allow_private: bool = False,
    resolver: polite_http.Resolver | None = None,
) -> list[str]:
    """The vendored guard, off the event loop, raising the charter
    ``net-denied`` typed error (non-retryable config class — logged, the
    instance stays ACTIVE; the remedy is config, not retry)."""
    try:
        return await polite_http.assert_safe_url(
            url, config=net_guard_config(allow_private=allow_private), resolver=resolver
        )
    except polite_http.PoliteHttpError as exc:
        raise ExtensionError.from_polite(exc) from exc


async def guarded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    data: Any | None = None,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
    allow_private: bool = False,
    resolver: polite_http.Resolver | None = None,
    max_redirects: int = _MAX_REDIRECTS,
) -> httpx.Response:
    """Run an HTTP request behind the full polite pipeline.

    Policy (health parity, now per-hop): the SSRF guard validates the URL
    before the request leaves the process AND every redirect target;
    401/403 raise ``auth`` immediately; 429/5xx retry up to
    ``max_retries`` (Retry-After honored over computed backoff, both RFC
    7231 forms; the 429 payload surfaces as ``retry_after_ms``); network
    errors retry then raise ``unreachable``; every other status is
    RETURNED to the caller (which decides — e.g. FHIR conditional-update
    treats 412 as a non-error tuple).
    """
    guard = net_guard_config(allow_private=allow_private)
    current = url
    hop = 0
    while True:
        try:
            await asyncio.to_thread(polite_http.check_url, current, config=guard, resolver=resolver)
        except polite_http.PoliteHttpError as exc:
            raise ExtensionError.from_polite(exc) from exc

        attempt = 0
        last_retry_after: float | None = None
        while True:
            request = client.build_request(
                method,
                current,
                headers=dict(headers or {}),
                params=params,
                json=json_body,
                data=data,
            )
            try:
                response = await client.send(request)
            except (httpx.RequestError, OSError) as exc:
                attempt += 1
                if attempt >= max_retries:
                    raise ExtensionError(
                        "unreachable", f"network error contacting {current}: {exc}"
                    ) from exc
                await asyncio.sleep(backoff_delay(attempt))
                continue

            status = response.status_code
            if status in (401, 403):
                raise ExtensionError(
                    "auth", f"{current} returned {status} (credentials rejected).", status=status
                )
            if status in _RETRY_STATUS:
                attempt += 1
                retry_after_header = response.headers.get("Retry-After")
                wait = parse_retry_after(retry_after_header)
                if status == 429 and wait is not None:
                    last_retry_after = wait
                if wait is None:
                    wait = backoff_delay(attempt)
                if attempt >= max_retries:
                    if status == 429:
                        raise ExtensionError(
                            "rate-limited",
                            f"rate limited by {current} after {max_retries} attempts.",
                            retry_after_ms=(
                                int(last_retry_after * 1000)
                                if last_retry_after is not None
                                else None
                            ),
                            status=status,
                        )
                    raise ExtensionError(
                        "provider", f"{current} returned {status} after retries.", status=status
                    )
                await asyncio.sleep(wait)
                continue
            if status in _REDIRECT_STATUS:
                location = response.headers.get("location")
                if not location:
                    raise ExtensionError(
                        "provider", f"redirect without location from {current}", status=status
                    )
                hop += 1
                if hop > max_redirects:
                    raise ExtensionError(
                        "provider", f"too many redirects (>{max_redirects})", status=status
                    )
                # Per-hop revalidation (STRICTER than health's history):
                # the loop re-runs the full guard on the joined target.
                current = urljoin(current, location)
                params = None
                break

            return response


async def http_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    access_token: str | None = None,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_retries: int = 3,
    allow_private: bool = False,
    resolver: polite_http.Resolver | None = None,
) -> Any:
    """Token-aware request returning parsed JSON (or ``None`` for 204).

    Raises :class:`ExtensionError` with ``auth`` (401/403),
    ``rate-limited`` (429 after retries, hint attached), ``protocol``
    (other 4xx — detail carries the OperationOutcome diagnostics when the
    body speaks FHIR) or ``net-denied``/``unreachable``/``provider`` from
    the pipeline.
    """
    from app.extensions.sdk.fhir import response_detail

    final_headers: dict[str, str] = {"Accept": "application/fhir+json, application/json"}
    if access_token:
        final_headers["Authorization"] = f"Bearer {access_token}"
    if headers:
        final_headers.update(headers)

    response = await guarded_request(
        client,
        method,
        url,
        headers=final_headers,
        json_body=json_body,
        params=params,
        max_retries=max_retries,
        allow_private=allow_private,
        resolver=resolver,
    )
    if response.status_code >= 400:
        raise ExtensionError(
            "protocol",
            f"{method} {url} -> {response.status_code}: {response_detail(response)}",
            status=response.status_code,
        )
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise ExtensionError(
            "protocol", f"non-JSON response from {url}: {response_detail(response)}"
        ) from exc


def host_of(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower()
