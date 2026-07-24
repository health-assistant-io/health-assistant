import logging
import warnings
from typing import List, Optional

import httpx

from . import __version__
from .models import (
    BridgeStatus,
    MetricMappingRequest,
    MapRequestPayload,
    MapResponsePayload,
    SyncPayload,
    SyncResponse,
)
from .signing import sign_request

logger = logging.getLogger(__name__)

# Default per-request timeout. The underlying httpx.AsyncClient is pooled and
# reused across calls (constructed in __init__, closed in aclose()), so a
# stalled backend can't hang a long-lived client process indefinitely.
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
DEFAULT_LIMITS = httpx.Limits(max_keepalive_connections=5, max_connections=10)
# Simple bounded retry on transient network/5xx errors (3 attempts, full-jitter
# backoff capped at 8s) so a flaky network doesn't surface a single-shot
# failure. Per-request, not per-call-session.
DEFAULT_MAX_RETRIES = 3
_BACKOFF_CEILING = 8.0


class AsyncHealthAssistantBridgeClient:
    """Asynchronous client for the Health Assistant Universal Bridge integration.

    Uses a pooled ``httpx.AsyncClient`` (reused across calls) with a default
    30s timeout and simple full-jitter retry on transient errors. Pass an
    ``api_secret`` to sign ``/map`` and ``/sync`` requests when the bridge
    instance is configured for HMAC.

    Args:
        base_url: Backend base URL (no trailing slash).
        integration_id: The bridge instance UUID (part of the API URL).
        api_secret: Optional HMAC secret. When set, /map and /sync are signed.
        timeout: Per-request ``httpx.Timeout`` (default 30s).
        max_retries: Max attempts on network/5xx (default 3).
    """

    def __init__(
        self,
        base_url: str,
        integration_id: str,
        *,
        api_secret: Optional[str] = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.integration_id = integration_id
        self.api_secret = api_secret
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout, limits=DEFAULT_LIMITS)

    async def aclose(self) -> None:
        """Release the pooled HTTP client. Call on shutdown."""
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v1/integrations/health_assistant_bridge/api/{self.integration_id}"

    async def get_status(self) -> BridgeStatus:
        """Check the connection status and retrieve the current sync cursor."""
        response = await self._request("GET", f"{self.api_url}/status")
        data = response.json()
        status_obj = BridgeStatus(**data)

        if status_obj.latest_sdks and status_obj.latest_sdks.get("python"):
            latest = status_obj.latest_sdks["python"]
            if latest != __version__:
                warnings.warn(
                    f"You are using SDK version {__version__}, but the latest "
                    f"available is {latest}. Please consider updating."
                )

        return status_obj

    async def request_mapping(self, metrics: List[MetricMappingRequest]) -> MapResponsePayload:
        """Ask the Health Assistant AI to map unrecognized metrics."""
        payload = MapRequestPayload(unmapped_metrics=metrics)
        raw_body = payload.model_dump_json(exclude_unset=True).encode()
        response = await self._request(
            "POST", f"{self.api_url}/map", content=raw_body,
            headers=self._signed_headers("POST", "/map", raw_body),
        )
        return MapResponsePayload(**response.json())

    async def sync_data(self, payload: SyncPayload) -> SyncResponse:
        """Push data into the Health Assistant platform."""
        raw_body = payload.model_dump_json(exclude_unset=True).encode()
        response = await self._request(
            "POST", f"{self.api_url}/sync", content=raw_body,
            headers=self._signed_headers("POST", "/sync", raw_body),
        )
        return SyncResponse(**response.json())

    # --- internals ------------------------------------------------------

    def _signed_headers(self, method: str, path: str, raw_body: bytes) -> dict:
        """Return ``Content-Type`` + (when an api_secret is set) HMAC headers.

        The body is sent as the EXACT raw bytes the signature covers (via
        ``content=raw_body``), never re-serialized, so the MAC always matches.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_secret:
            headers.update(sign_request(self.api_secret, method, path, raw_body))
        return headers

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Issue a request with simple full-jitter retry on transient errors."""
        import asyncio
        import random

        attempt = 0
        while True:
            try:
                response = await self._client.request(method, url, **kwargs)
            except (httpx.RequestError, httpx.TimeoutException) as e:
                attempt += 1
                if attempt >= self.max_retries:
                    raise
                wait = random.uniform(0.0, min(_BACKOFF_CEILING, 2 ** attempt))
                logger.warning("Network error %s %s (attempt %d/%d): %s", method, url, attempt, self.max_retries, e)
                await asyncio.sleep(wait)
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                attempt += 1
                if attempt >= self.max_retries:
                    response.raise_for_status()
                wait = random.uniform(0.0, min(_BACKOFF_CEILING, 2 ** attempt))
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            return response