import logging
import time
import warnings
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


class HealthAssistantBridgeClient:
    """Synchronous client for the Health Assistant Universal Bridge integration.

    Args:
        base_url: Backend base URL (no trailing slash), e.g. ``https://ha.example``.
        integration_id: The bridge instance UUID (part of the API URL).
        api_secret: Optional HMAC secret. When set, ``/map`` and ``/sync``
            requests are signed (``X-Api-Signature`` + ``X-Api-Timestamp``).
            Leave unset for UUID-only mode (trusted/LAN self-hosted).
        timeout: Per-request timeout in seconds (default 30).
    """

    def __init__(
        self,
        base_url: str,
        integration_id: str,
        *,
        api_secret: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.integration_id = integration_id
        self.api_secret = api_secret
        self.timeout = timeout

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v1/integrations/health_assistant_bridge/api/{self.integration_id}"

    def get_status(self, **kwargs) -> BridgeStatus:
        """Check the connection status and retrieve the current sync cursor."""
        response = requests.get(
            f"{self.api_url}/status", timeout=kwargs.pop("timeout", self.timeout), **kwargs
        )
        response.raise_for_status()
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

    def request_mapping(self, metrics: List[MetricMappingRequest], **kwargs) -> MapResponsePayload:
        """Ask the Health Assistant AI to map unrecognized metrics."""
        payload = MapRequestPayload(unmapped_metrics=metrics)
        raw_body = payload.model_dump_json(exclude_unset=True).encode()
        headers = self._signed_headers("POST", "/map", raw_body)
        response = requests.post(
            f"{self.api_url}/map", data=raw_body, headers=headers,
            timeout=kwargs.pop("timeout", self.timeout), **kwargs,
        )
        response.raise_for_status()
        return MapResponsePayload(**response.json())

    def sync_data(self, payload: SyncPayload, **kwargs) -> SyncResponse:
        """Push data into the Health Assistant platform."""
        raw_body = payload.model_dump_json(exclude_unset=True).encode()
        headers = self._signed_headers("POST", "/sync", raw_body)
        response = requests.post(
            f"{self.api_url}/sync", data=raw_body, headers=headers,
            timeout=kwargs.pop("timeout", self.timeout), **kwargs,
        )
        response.raise_for_status()
        return SyncResponse(**response.json())

    def _signed_headers(self, method: str, path: str, raw_body: bytes) -> dict:
        """Return ``Content-Type`` + (when an api_secret is set) HMAC headers.

        The body is sent as the EXACT raw bytes the signature covers (not
        re-serialized by ``requests``), so we pass ``data=raw_body`` and an
        explicit ``Content-Type`` to the caller — never ``json=``."""
        headers = {"Content-Type": "application/json"}
        if self.api_secret:
            headers.update(sign_request(self.api_secret, method, path, raw_body))
        return headers