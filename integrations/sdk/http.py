"""Token-aware HTTP helpers + FHIR Bundle pagination for cloud integrations.

Complements :mod:`integrations.sdk.auth`. ``http_request`` is the low-level
verb-agnostic helper: it injects the Bearer token, retries 5xx/network errors
with exponential backoff, and maps non-2xx to the SDK exception hierarchy
(``IntegrationAuthError`` for 401/403, ``IntegrationRateLimitError`` for 429,
``IntegrationDataError`` for other 4xx + non-JSON). ``paginate_bundle`` walks a
FHIR searchset Bundle, following ``link[rel=next]``.

These helpers take an ``httpx.AsyncClient`` (the provider's pooled client) plus
an ``access_token`` string, so they're decoupled from the provider and unit-
testable with ``httpx.MockTransport``. Refresh-on-401 is handled one level up
in :mod:`integrations.sdk.fhir` (``fhir_search``), which owns the token lifecycle.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

import httpx

from .exceptions import IntegrationAuthError, IntegrationDataError, IntegrationRateLimitError

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}

# Default upper bound on Bundle pagination. ``paginate_bundle`` and
# ``fhir_search`` accept ``max_pages=None`` to opt out (truly unbounded), but
# the default is capped so an integration that forgets the argument can't
# walk a multi-million-entry Bundle forever.
DEFAULT_MAX_PAGES = 100

# Ceiling on a single backoff sleep. Even at high attempt counts we don't
# wait longer than a minute between retries.
_BACKOFF_CEILING_SECONDS = 60.0


def _backoff_delay(attempt: int, base: float = 1.0) -> float:
    """Full-jitter exponential backoff for retry waits.

    Returns a uniform-random duration in ``[0, base * 2**attempt]``, capped
    at :data:`_BACKOFF_CEILING_SECONDS`. Full jitter (AWS architecture blog,
    "Exponential Backoff and Jitter") is the most effective scheme for
    breaking lockstep retries across many concurrent clients: each client
    picks an independent random wait, so the retry wave spreads out instead
    of stampeding the server on each tick.

    Theoretical cap at the configured ceiling; on attempt 0 the range is
    ``[0, base]`` (so the first retry can happen near-immediately), on
    attempt 5 it's ``[0, 32 * base]`` (a wide spread that lowers the
    expected wait but bounds the worst case).
    """
    cap = min(base * (2 ** attempt), _BACKOFF_CEILING_SECONDS)
    return random.uniform(0.0, cap)


async def _retry_request(
    do_request: Callable[[], Awaitable[httpx.Response]],
    *,
    url: str,
    method: str,
    max_retries: int = 3,
) -> httpx.Response:
    """Run an HTTP request with retry + full-jitter backoff.

    Single source of truth for the SDK's retry semantics. Used by
    :func:`http_request` (JSON-returning generic helper), :func:`fhir_create`
    and :func:`fhir_conditional_update` (FHIR tuple-returning helpers), and
    ``auth._request_json`` (OAuth / DCR / token-refresh helper).

    Retry policy:
      - **Network errors** (``httpx.RequestError`` / ``TimeoutException``):
        retried up to ``max_retries`` total attempts; on exhaustion →
        :class:`IntegrationDataError`.
      - **429**: retried up to ``max_retries``; honors a server-provided
        ``Retry-After`` header when present, otherwise full-jitter backoff.
        On exhaustion → :class:`IntegrationRateLimitError`.
      - **5xx**: retried up to ``max_retries`` with full-jitter backoff. On
        exhaustion → :class:`IntegrationDataError`.
      - **401/403**: raises :class:`IntegrationAuthError` immediately
        (retrying won't help — the token is rejected).
      - **All other statuses (2xx, 4xx except 401/403)**: returned to the
        caller, which decides what to do (e.g. ``fhir_conditional_update``
        treats 412 as a non-error precondition-not-met tuple).
    """
    attempt = 0
    while True:
        try:
            response = await do_request()
        except (httpx.RequestError, httpx.TimeoutException) as e:
            attempt += 1
            if attempt >= max_retries:
                raise IntegrationDataError(f"Network error contacting {url}: {e}") from e
            logger.warning(
                "Network error %s %s (attempt %d/%d): %s",
                method, url, attempt, max_retries, e,
            )
            await asyncio.sleep(_backoff_delay(attempt))
            continue

        if response.status_code in (401, 403):
            raise IntegrationAuthError(
                f"{url} returned {response.status_code} (token rejected)."
            )
        if response.status_code == 429:
            attempt += 1
            if attempt >= max_retries:
                raise IntegrationRateLimitError(
                    f"Rate limited by {url} after {max_retries} attempts."
                )
            retry_after = response.headers.get("Retry-After")
            # Retry-After overrides our computed backoff when present (server
            # knows best); otherwise fall back to full-jitter exponential.
            wait = (
                float(retry_after)
                if retry_after and retry_after.isdigit()
                else _backoff_delay(attempt)
            )
            logger.warning("Rate limited by %s. Waiting %.2fs.", url, wait)
            await asyncio.sleep(wait)
            continue
        if response.status_code >= 500:
            attempt += 1
            if attempt >= max_retries:
                raise IntegrationDataError(
                    f"{method} {url} -> {response.status_code}: "
                    f"{_response_detail(response)}"
                )
            logger.warning(
                "Server error %s %s -> %d (attempt %d/%d)",
                method, url, response.status_code, attempt, max_retries,
            )
            await asyncio.sleep(_backoff_delay(attempt))
            continue

        return response


async def http_request(
    http_client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    access_token: Optional[str] = None,
    json_body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    max_retries: int = 3,
) -> Any:
    """Token-aware request returning parsed JSON (or ``None`` for HTTP 204).

    Injects ``Authorization: Bearer <access_token>`` when a token is supplied.
    Retries 429/5xx/network errors via :func:`_retry_request` (full-jitter
    exponential backoff). Raises :class:`IntegrationAuthError` (401/403),
    :class:`IntegrationRateLimitError` (429 after retries exhausted),
    :class:`IntegrationDataError` (other 4xx or non-JSON body).
    """
    final_headers: Dict[str, str] = {"Accept": "application/fhir+json, application/json"}
    if access_token:
        final_headers["Authorization"] = f"Bearer {access_token}"
    if headers:
        final_headers.update(headers)

    response = await _retry_request(
        lambda: http_client.request(
            method, url, headers=final_headers, params=params, json=json_body
        ),
        url=url,
        method=method,
        max_retries=max_retries,
    )
    if response.status_code >= 400:
        raise IntegrationDataError(
            f"{method} {url} -> {response.status_code}: {_response_detail(response)}"
        )
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as e:
        raise IntegrationDataError(
            f"Non-JSON response from {url}: {_response_detail(response)}"
        ) from e


async def paginate_bundle(
    http_client: httpx.AsyncClient,
    url: str,
    *,
    access_token: Optional[str],
    params: Optional[Dict[str, Any]] = None,
    max_pages: Optional[int] = DEFAULT_MAX_PAGES,
    max_retries: int = 3,
) -> AsyncIterator[Dict[str, Any]]:
    """Yield each ``entry[].resource`` from a FHIR searchset Bundle.

    Follows ``link[rel=next].url`` (FHIR servers return absolute URLs) until
    exhausted or ``max_pages`` is reached. ``params`` apply only to the first
    request (subsequent pages use the server-provided ``next`` URL verbatim).

    The default ``max_pages`` is :data:`DEFAULT_MAX_PAGES` (100) — pass
    ``max_pages=None`` explicitly for truly unbounded iteration.
    """
    next_url: Optional[str] = url
    next_params: Optional[Dict[str, Any]] = params
    pages = 0
    while next_url:
        if max_pages is not None and pages >= max_pages:
            logger.info("paginate_bundle: reached max_pages=%d, stopping.", max_pages)
            return
        pages += 1
        bundle = await http_request(
            http_client, "GET", next_url, access_token=access_token,
            params=next_params, max_retries=max_retries,
        )
        if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
            raise IntegrationDataError(f"Expected a FHIR Bundle from {next_url}; got {type(bundle).__name__}.")
        for entry in bundle.get("entry") or []:
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if isinstance(resource, dict):
                yield resource
        next_url = _next_link(bundle)
        next_params = None  # the next URL already carries the pagination offset
    return


def _next_link(bundle: Dict[str, Any]) -> Optional[str]:
    for link in bundle.get("link") or []:
        if isinstance(link, dict) and link.get("relation") == "next":
            url = link.get("url")
            if isinstance(url, str):
                return url
    return None


def _response_detail(response: httpx.Response) -> str:
    """Best-effort human-readable detail from a FHIR error response.

    H8: tries to extract ``OperationOutcome.issue[].diagnostics`` (the most
    useful field for debugging hospital 422/400 rejections) before falling back
    to the raw response body. Local helper — avoids importing from ``fhir.py``
    (which would create a circular dependency since ``fhir.py`` imports
    ``paginate_bundle`` from here).
    """
    if not response.content:
        return "empty response body"
    try:
        body = response.json()
        if isinstance(body, dict):
            issues = body.get("issue") or []
            for issue in issues:
                if isinstance(issue, dict):
                    msg = (
                        issue.get("diagnostics")
                        or (issue.get("details") or {}).get("text")
                        or issue.get("code")
                    )
                    if msg:
                        return str(msg)
    except (ValueError, TypeError):
        pass
    return response.text[:300]
