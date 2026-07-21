from abc import ABC, abstractmethod
from typing import Any, Dict, List
from app.models.fhir import Observation
from app.models.user_integration import UserIntegration

class BaseHealthProvider(ABC):
    domain: str

    async def setup(self, config: dict | None = None) -> None:
        """Called once when the integration is loaded by the registry.

        Item 5 of the integrations-sdk-improvements plan: the registry
        now passes the ``SystemIntegration.global_config`` JSONB (when
        present) so providers can do one-time resource setup keyed off
        system-level configuration — bootstrap HTTP pools, fetch
        well-known endpoints, validate entitlements, etc.

        Default no-op. Override and call ``super().setup(config)`` for
        forward-compat. Failures bubble up to the registry's loader,
        which logs them and excludes the integration from the active
        set (rather than crashing the whole startup loop).
        """
        pass

    async def pull_data(self, integration: UserIntegration) -> List[Observation]:
        """For integrations that fetch data. Returns FHIR observations."""
        return []

    async def push_data(self, integration: UserIntegration, data: Any):
        """For integrations that send data outwards."""
        pass

    async def handle_webhook(self, integration: UserIntegration, payload: Any, request: Any = None) -> List[Observation]:
        """Process inbound webhook payloads and return FHIR observations."""
        return []

    async def handle_api_request(self, integration: UserIntegration, path: str, method: str, request: Any) -> Dict[str, Any]:
        """Handle custom two-way API requests for this integration."""
        raise NotImplementedError(f"API requests are not supported by this integration.")

    # OAuth on the core base was a pre-SmartOAuth stub trio
    # (get_auth_url / exchange_token / refresh_access_token) that returned
    # "" / {} / {} silently and was never called by any engine path. It has
    # been removed; providers that need OAuth subclass the SDK base
    # (integrations/sdk/base.py) which declares the canonical
    # ``begin_oauth`` / ``complete_oauth`` pair, and use ``SmartOAuth``
    # (integrations/sdk/auth.py) for the SMART-on-FHIR round-trip.

class BaseConfigFlow(ABC):
    domain: str

    @abstractmethod
    async def get_schema(self) -> dict:
        """Return the JSON schema defining the setup UI."""
        pass

    @abstractmethod
    async def validate_input(self, user_input: dict) -> dict:
        """Validate user input from the UI setup. Raises ValueError on error."""
        pass
