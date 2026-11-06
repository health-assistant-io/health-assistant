"""The SSRF-guarded HTTP seam the vendored calendar transports see.

A self-hosted CalDAV/ICS binding carries a **user-supplied URL** — that is a
server-side fetch primitive on a multi-tenant host. The vendored transports
call ``client.request(method, url, …)`` on whatever client they were built
with, so the guard must sit *behind* that seam: :class:`GuardedCalendarClient`
is an ``httpx.AsyncClient`` stand-in whose every request

1. resolves and validates the URL through the polite runtime's guard
   (``polite_http.check_url`` — reserved ranges refused, numeric-literal
   obfuscations, injectable resolver, off the event loop; policy from
   :func:`app.extensions.runtime.http.net_guard_config`, so the
   ``INTEGRATION_ALLOWED_HOSTS`` hatch is the documented way to trust one
   self-hosted name);
2. connects to the **pinned** guard-checked address
   (``polite_http.pinned_request_parts`` — original Host header, TLS SNI
   against the real hostname) so httpx never re-resolves at connect (the
   DNS-rebind TOCTOU);
3. walks redirects itself — every hop is re-guarded and re-pinned, max 5,
   and httpx never double-follows (build the wrapped client with
   ``follow_redirects=False``);
4. caps the response body (streamed incremental read, default 10 MB) — an
   unbounded feed must not balloon the host's memory;
5. fails **typed**: guard refusals raise ``CalendarError("net-denied", …)``
   (non-retryable config class), network failures
   ``CalendarError("unreachable", …)``; every detail passes through
   :func:`app.services.calendar.sanitize.sanitize_error_detail` so a query
   string carrying a feed token never leaves the process (plan 52 §10 E12).

Mirrors the family precedent (study's ``_guarded_client``) at the health
strictness: private ranges are refused unless Settings explicitly allow
them — the guard config is injectable for tests.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from app.extensions import polite_http
from app.extensions.cloud.cloud_calendar import CalendarError
from app.extensions.runtime.http import net_guard_config
from app.services.calendar.sanitize import sanitize_error_detail, sanitize_url

logger = logging.getLogger(__name__)

#: Bounded response: an ICS feed or CalDAV collection larger than this is a
#: misconfiguration, not data (the family caps expansion and pages too).
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024

_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_READ_CHUNK = 64 * 1024


class GuardedCalendarClient:
    """``httpx.AsyncClient`` stand-in enforcing the polite runtime's guard."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        config: polite_http.NetGuardConfig | None = None,
        resolver: polite_http.Resolver | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_redirects: int = _MAX_REDIRECTS,
    ) -> None:
        self._client = client
        self._config = config or net_guard_config()
        self._resolver = resolver
        self._max_bytes = max_response_bytes
        self._max_redirects = max_redirects

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Any = None,
        content: Any = None,
        params: Any = None,
        timeout: Any = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Run one guarded request; every redirect hop is re-guarded."""
        current: str = str(httpx.URL(url, params=params) if params else url)
        hop = 0
        while True:
            resolved = await self._guard(current)
            if not resolved:
                # Resolution failure during a fetch fails CLOSED — sending by
                # name would be the rebinding window the pin closes.
                raise CalendarError(
                    "unreachable",
                    f"could not resolve {sanitize_url(current)} — failing closed",
                    retryable=True,
                )
            request_url, pin_headers, pin_extensions = polite_http.pinned_request_parts(
                current, resolved
            )
            merged_headers: dict[str, str] = {**(dict(headers) if headers else {}), **pin_headers}
            request = self._client.build_request(
                method,
                request_url,
                headers=merged_headers,
                content=content,
                timeout=timeout,
            )
            if pin_extensions:
                request.extensions.update(pin_extensions)
            try:
                response = await self._client.send(request, stream=True)
            except (httpx.RequestError, OSError) as exc:
                # httpx exception messages can embed the full URL — the
                # query string of a private feed is a bearer token (C4).
                raise CalendarError(
                    "unreachable",
                    sanitize_error_detail(str(exc)) or "network error",
                    retryable=True,
                ) from exc

            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    raise CalendarError(
                        "provider", f"redirect without location from {sanitize_url(current)}"
                    )
                hop += 1
                if hop > self._max_redirects:
                    raise CalendarError(
                        "provider", f"too many redirects (>{self._max_redirects})"
                    )
                current = urljoin(current, location)
                content = None  # a redirect body is never re-sent
                continue

            return await self._read_capped(response)

    async def _guard(self, url: str) -> list[str]:
        """The SSRF gate: resolve + validate, raising typed ``net-denied``."""
        try:
            return await asyncio.to_thread(
                polite_http.check_url, url, config=self._config, resolver=self._resolver
            )
        except polite_http.PoliteHttpError as exc:
            raise CalendarError(
                exc.code if exc.code in ("net-denied", "unreachable") else "net-denied",
                sanitize_error_detail(exc.detail) or "blocked by the network guard",
            ) from exc

    async def _read_capped(self, response: httpx.Response) -> httpx.Response:
        """Stream-read up to the size cap, then hand back a normal Response."""
        buffer = bytearray()
        try:
            async for chunk in response.aiter_bytes(_READ_CHUNK):
                buffer.extend(chunk)
                if len(buffer) > self._max_bytes:
                    raise CalendarError(
                        "io",
                        "response exceeded the calendar size cap "
                        f"({self._max_bytes} bytes) — refusing to buffer it",
                    )
        finally:
            await response.aclose()
        # Rebuild a plain (non-streaming) response: the vendored transports
        # read ``.text``/``.json()``/``.status_code`` freely.
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=bytes(buffer),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def wrap_guarded(client: Any, **kwargs: Any) -> Any:
    """Wrap a raw client in the guard (idempotent — never double-wrap)."""
    if isinstance(client, GuardedCalendarClient):
        return client
    return GuardedCalendarClient(client, **kwargs)


__all__ = ["DEFAULT_MAX_RESPONSE_BYTES", "GuardedCalendarClient", "wrap_guarded"]
