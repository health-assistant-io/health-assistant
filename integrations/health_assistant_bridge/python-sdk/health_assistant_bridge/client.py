import logging
import random
import time
from typing import List, Optional

import requests

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

# Single default timeout for all calls (30s) — protects against a stalled
# backend hanging the client forever. Override per-call via the ``timeout``
# kwarg (each method forwards ``**kwargs`` to ``requests``).
DEFAULT_TIMEOUT = 30.0
# Simple bounded retry on transient network/5xx errors (3 attempts, full-jitter
# backoff capped at 8s) so a flaky network doesn't surface a single-shot
# failure. Parity with the async client + the TS client.
DEFAULT_MAX_RETRIES = 3
_BACKOFF_CEILING = 8.0
# Statuses worth retrying (rate-limit + the transient server errors).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HealthAssistantBridgeClient:
    """Synchronous client for the Health Assistant Universal Bridge integration.

    Args:
        base_url: Backend base URL (no trailing slash), e.g. ``https://ha.example``.
        integration_id: The bridge instance UUID (part of the API URL).
        api_secret: Optional HMAC secret. When set, ``/map`` and ``/sync``
            requests are signed (``X-Api-Signature`` + ``X-Api-Timestamp``).
            Leave unset for UUID-only mode (trusted/LAN self-hosted).
        timeout: Per-request timeout in seconds (default 30).
        max_retries: Max attempts on network/5xx errors (default 3, full-jitter
            backoff). Set to ``1`` to disable retry.
    """

    def __init__(
        self,
        base_url: str,
        integration_id: str,
        *,
        api_secret: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.integration_id = integration_id
        self.api_secret = api_secret
        self.timeout = timeout
        self.max_retries = max(1, max_retries)

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v1/integrations/health_assistant_bridge/api/{self.integration_id}"

    def get_status(self, **kwargs) -> BridgeStatus:
        """Check the connection status and retrieve the current sync cursor."""
        response = self._request(
            "GET", f"{self.api_url}/status", **kwargs
        )
        data = response.json()
        status_obj = BridgeStatus(**data)

        if status_obj.latest_sdks and status_obj.latest_sdks.get("python"):
            latest = status_obj.latest_sdks["python"]
            if latest != __version__:
                import warnings

                warnings.warn(
                    f"You are using SDK version {__version__}, but the latest "
                    f"available is {latest}. Please consider updating."
                )

        return status_obj

    def request_mapping(self, metrics: List[MetricMappingRequest], **kwargs) -> MapResponsePayload:
        """Ask the Health Assistant AI to map unrecognized metrics."""
        payload = MapRequestPayload(unmapped_metrics=metrics)
        raw_body = payload.model_dump_json(exclude_unset=True).encode()
        headers = self._signed_headers("POST", "/map", raw_body)
        response = self._request(
            "POST", f"{self.api_url}/map", data=raw_body, headers=headers, **kwargs,
        )
        return MapResponsePayload(**response.json())

    def sync_data(self, payload: SyncPayload, **kwargs) -> SyncResponse:
        """Push data into the Health Assistant platform."""
        raw_body = payload.model_dump_json(exclude_unset=True).encode()
        headers = self._signed_headers("POST", "/sync", raw_body)
        response = self._request(
            "POST", f"{self.api_url}/sync", data=raw_body, headers=headers, **kwargs,
        )
        return SyncResponse(**response.json())

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Issue a request with simple full-jitter retry on transient errors.

        Mirrors the async client's retry contract: network errors and the
        retryable status set (429/5xx) are retried up to ``max_retries``
        times with full-jitter exponential backoff capped at
        ``_BACKOFF_CEILING``. Non-retryable 4xx raises immediately via
        ``raise_for_status``.
        """
        timeout = kwargs.pop("timeout", self.timeout)
        attempt = 0
        while True:
            try:
                response = requests.request(
                    method, url, timeout=timeout, **kwargs
                )
            except requests.RequestException as e:
                attempt += 1
                if attempt >= self.max_retries:
                    raise
                wait = random.uniform(0.0, min(_BACKOFF_CEILING, 2 ** attempt))
                logger.warning(
                    "Network error %s %s (attempt %d/%d): %s",
                    method, url, attempt, self.max_retries, e,
                )
                time.sleep(wait)
                continue
            if response.status_code in _RETRYABLE_STATUS:
                attempt += 1
                if attempt >= self.max_retries:
                    response.raise_for_status()
                wait = random.uniform(0.0, min(_BACKOFF_CEILING, 2 ** attempt))
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response

    def _signed_headers(self, method: str, path: str, raw_body: bytes) -> dict:
        """Return ``Content-Type`` + (when an api_secret is set) HMAC headers.

        The body is sent as the EXACT raw bytes the signature covers (not
        re-serialized by ``requests``), so we pass ``data=raw_body`` and an
        explicit ``Content-Type`` to the caller — never ``json=``."""
        headers = {"Content-Type": "application/json"}
        if self.api_secret:
            headers.update(sign_request(self.api_secret, method, path, raw_body))
        return headers