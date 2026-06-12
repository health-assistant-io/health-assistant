from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import logging
import httpx
import asyncio
import json

from app.integrations.base import (
    BaseHealthProvider as CoreBaseHealthProvider,
    BaseConfigFlow as CoreBaseConfigFlow
)
from app.schemas.fhir.observation import ObservationCreate
from app.models.user_integration import UserIntegration
from .observation_builder import ObservationBuilder
from .exceptions import IntegrationAuthError, IntegrationRateLimitError

logger = logging.getLogger(__name__)

class BaseHealthProvider(CoreBaseHealthProvider, ABC):
    """Enhanced base class for health data providers with robust HTTP handling."""
    
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.{self.domain}")
        # Shared HTTP client for connection pooling
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        )

    async def close(self):
        """Cleanup resources. Should be called when the provider is no longer needed."""
        await self._http_client.aclose()

    # --- State / Cursor Management ---
    
    def get_sync_cursor(self, integration: UserIntegration, key: str, default: Any = None) -> Any:
        """Retrieve a saved cursor (e.g., last timestamp) to perform delta syncs."""
        if not integration.user_config:
            return default
        state = integration.user_config.get("_sync_state", {})
        val = state.get(key)
        return val if val is not None else default

    def set_sync_cursor(self, integration: UserIntegration, key: str, value: Any) -> None:
        """Save a cursor. Changes will be committed by the background sync worker."""
        # Create a new dict to ensure SQLAlchemy detects the JSONB mutation
        new_config = dict(integration.user_config) if integration.user_config else {}
        
        if "_sync_state" not in new_config:
            new_config["_sync_state"] = {}
            
        new_config["_sync_state"][key] = value
        integration.user_config = new_config

    # --- Custom Actions ---

    def get_custom_actions(self) -> List[Dict[str, str]]:
        """Override to expose custom action buttons to the UI.
        Returns a list of dicts: [{"id": "action_id", "label": "Button Label", "style": "primary|danger|warning|default"}]
        """
        return []

    async def execute_custom_action(self, integration: UserIntegration, action_id: str, **kwargs) -> Dict[str, Any]:
        """Override to handle custom action execution triggered from the UI."""
        raise NotImplementedError(f"Action '{action_id}' is not implemented by {self.domain}.")

    # --- Debugging ---
    
    def log_debug_payload(self, integration: UserIntegration, title: str, payload: Any):
        """Dumps raw payloads for debugging if the user enabled debug mode."""
        if integration.user_config and integration.user_config.get("debug_mode"):
            try:
                dump = json.dumps(payload, indent=2)
                # Using INFO instead of DEBUG so it appears in standard console output 
                # when the user explicitly checks the "Enable Debug Mode" box.
                self.logger.info(f"[{self.domain}] DEBUG PAYLOAD ({title}) for user {integration.user_id}:\n{dump}")
            except Exception:
                self.logger.info(f"[{self.domain}] DEBUG PAYLOAD ({title}) for user {integration.user_id}: {payload}")

    # --- Data Building ---

    def create_observation_builder(self, integration: UserIntegration) -> ObservationBuilder:
        """Helper to create an ObservationBuilder for the current integration."""
        return ObservationBuilder(
            tenant_id=integration.tenant_id,
            patient_id=integration.patient_id
        )

    @abstractmethod
    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        """Fetch data and return Pydantic models."""
        pass

    async def fetch_json(
        self, 
        integration: UserIntegration,
        url: str, 
        headers: Optional[Dict[str, str]] = None, 
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 3
    ) -> Any:
        """Robust HTTP GET with exponential backoff, rate limit handling, and auto-debugging."""
        attempt = 0
        backoff_factor = 2

        while attempt < max_retries:
            try:
                response = await self._http_client.get(url, headers=headers, params=params)
                
                # Handle rate limits specifically
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", backoff_factor ** attempt))
                    self.logger.warning(f"Rate limited by {url}. Waiting {retry_after}s.")
                    await asyncio.sleep(retry_after)
                    attempt += 1
                    continue
                    
                # Handle Auth Errors
                if response.status_code in (401, 403):
                    raise IntegrationAuthError(f"Authentication failed for {url}. Token may be expired.")
                    
                response.raise_for_status()
                data = response.json()
                
                # Zero-config auto-debugging!
                self.log_debug_payload(integration, f"HTTP GET {url}", data)
                
                return data

                
            except httpx.HTTPStatusError as e:
                # Don't retry on client errors (4xx) except 429 and Auth errors which are handled above
                if 400 <= e.response.status_code < 500:
                    self.logger.error(f"Client error {e.response.status_code} fetching {url}: {e.response.text}")
                    raise
                
                # Retry on server errors (5xx)
                self.logger.warning(f"Server error fetching {url} (Attempt {attempt+1}/{max_retries}): {e}")
                
            except (httpx.RequestError, httpx.TimeoutException) as e:
                self.logger.warning(f"Network error fetching {url} (Attempt {attempt+1}/{max_retries}): {e}")
            
            attempt += 1
            if attempt < max_retries:
                sleep_time = backoff_factor ** attempt
                await asyncio.sleep(sleep_time)

        self.logger.error(f"Failed to fetch data from {url} after {max_retries} attempts.")
        raise IntegrationRateLimitError(f"Failed to fetch from {url} after multiple retries.")

class BaseConfigFlow(CoreBaseConfigFlow, ABC):
    """Enhanced base class for integration configuration flows."""
    pass
