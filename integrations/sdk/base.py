from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import logging
import httpx
import asyncio
import json

from integrations.base import (
    BaseHealthProvider as CoreBaseHealthProvider,
    BaseConfigFlow as CoreBaseConfigFlow
)
from app.schemas.fhir.observation import ObservationCreate
from app.models.user_integration import UserIntegration
from .observation_builder import ObservationBuilder
from .exceptions import IntegrationAuthError, IntegrationRateLimitError
from .secrets import encrypt_fields, decrypt_fields, mask_fields

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
        """Cleanup resources. Should be called when the provider is no longer needed.

        Integrations that hold extra resources (e.g. MCP subprocess pools)
        override this to tear those down in addition to the shared httpx client.
        """
        await self._http_client.aclose()

    async def revoke(self, integration) -> None:
        """Best-effort token revocation on disconnect (default no-op).

        OAuth providers override this to call the remote revocation endpoint
        (RFC 7009). Non-OAuth providers leave the default — nothing to revoke.
        """
        return

    # --- Tool Exposure (for the Chat Assistant) ---

    def supports_tools(self) -> bool:
        """Whether this integration exposes tools to the chat assistant.

        Override to return ``True`` and implement :meth:`get_tools` to make
        the integration's capabilities available as LangChain tools in chat.
        Default: ``False`` (no tools).
        """
        return False

    async def get_tools(self, integration: UserIntegration) -> List[Any]:
        """Return LangChain-compatible tools for this instance.

        Only called by the platform tool aggregator when
        :meth:`supports_tools` is ``True``. Tools should be standard
        LangChain ``StructuredTool`` / ``@tool`` objects so the existing
        ``llm.bind_tools`` + reasoning loop works unchanged.

        Implementations should swallow per-instance errors and return ``[]``
        on failure so one bad instance doesn't break the whole chat turn.
        Default: ``[]``.
        """
        return []

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
    
    async def log_debug_payload(self, integration: UserIntegration, title: str, payload: Any, level: str = "info"):
        """Dumps raw payloads for debugging and saves to DB if the user enabled debug mode."""
        if integration.is_debug_enabled:
            try:
                dump = json.dumps(payload, indent=2)
                self.logger.info(f"[{self.domain}] DEBUG PAYLOAD ({title}) for user {integration.user_id}:\n{dump}")
            except Exception:
                dump = str(payload)
                self.logger.info(f"[{self.domain}] DEBUG PAYLOAD ({title}) for user {integration.user_id}: {dump}")

            try:
                from app.core.database import AsyncSessionLocal
                from app.models.user_integration import IntegrationDebugLog
                async with AsyncSessionLocal() as db:
                    debug_log = IntegrationDebugLog(
                        integration_id=integration.id,
                        tenant_id=integration.tenant_id,
                        level=level,
                        title=title,
                        payload=payload if isinstance(payload, (dict, list)) else {"raw": dump}
                    )
                    db.add(debug_log)
                    await db.commit()
            except Exception as e:
                self.logger.error(f"Failed to save debug log to DB: {e}")

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

    async def handle_webhook(self, integration: UserIntegration, payload: Any, request: Any = None) -> List[ObservationCreate]:
        """Process inbound webhook payloads and return Pydantic models.
        Override this method for push-based integrations.
        
        Args:
            integration: The UserIntegration instance.
            payload: The parsed JSON payload from the webhook request.
            request: The raw FastAPI Request object, useful for validating headers or HMAC signatures.
        """
        return []

    async def handle_api_request(self, integration: UserIntegration, path: str, method: str, request: Any) -> Dict[str, Any]:
        """Handle custom two-way API requests for this integration.
        Override this method for integrations requiring dynamic interactions with headless clients.
        
        Args:
            integration: The UserIntegration instance.
            path: The relative path called by the client.
            method: The HTTP method (GET, POST, PUT).
            request: The raw FastAPI Request object.
        """
        raise NotImplementedError(f"API requests are not supported by this integration.")

    # --- OAuth round-trip (opt-in; only when the config flow sets is_oauth=True) ---

    async def begin_oauth(
        self,
        integration: UserIntegration,
        redirect_uri: str,
        *,
        extra_state: Optional[dict] = None,
    ):
        """Start the OAuth Authorization Code flow for this instance.

        Override for OAuth integrations. Returns ``(authorize_url, state)``. The
        platform stores ``extra_state`` (e.g. ``integration_id``/``user_id``)
        alongside any provider-specific PKCE/endpoint data under ``state`` (short
        TTL), then redirects the user to ``authorize_url``. Default raises
        ``NotImplementedError`` — non-OAuth integrations are unaffected.
        """
        raise NotImplementedError(f"{self.domain} does not implement OAuth.")

    async def complete_oauth(
        self, integration: UserIntegration, pending: dict, code: str
    ) -> dict:
        """Finish the OAuth flow: exchange ``code`` for tokens and persist them.

        ``pending`` is the already-consumed state payload (the platform consumes
        it once and passes it through). Override for OAuth integrations. Default
        raises ``NotImplementedError``.
        """
        raise NotImplementedError(f"{self.domain} does not implement OAuth.")

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
                await self.log_debug_payload(integration, f"HTTP GET {url}", data)
                
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
    """Enhanced base class for integration configuration flows.

    Integrations declare their capabilities by overriding the hooks below;
    the platform endpoint calls them generically (no per-domain code). All
    hooks have safe defaults so existing integrations are unaffected.
    """

    # Per-user instance cap. ``None`` = unlimited. The platform endpoint
    # enforces this on create (not update) without knowing which domain.
    max_instances_per_user: Optional[int] = None

    # Whether this integration connects via an OAuth round-trip (e.g. SMART-on-FHIR,
    # Fitbit, Withings). When ``True`` the platform exposes the
    # ``/{domain}/oauth/start`` + ``/{domain}/oauth/callback`` routes for this
    # domain and the UI shows an "Authorize" button on the instance. Default
    # ``False`` keeps existing (non-OAuth) integrations untouched.
    is_oauth: bool = False

    def get_secret_fields(self) -> List[str]:
        """Field names in ``user_config`` that are secret.

        The platform encrypts these before storage (Fernet) and masks them
        as ``"***"`` on read. Default: ``[]`` (no secrets, no key required).
        """
        return []

    async def prepare_for_storage(self, config: dict) -> dict:
        """Hook called by the endpoint before persisting ``user_config``.

        Default: encrypts :meth:`get_secret_fields` via the platform Fernet
        key (``INTEGRATION_SECRET_KEY``). Override to add custom transforms
        (e.g. strip read-only fields), then call super() to keep encryption.
        """
        return encrypt_fields(config, self.get_secret_fields())

    def prepare_for_read(self, config: dict) -> dict:
        """Hook called by the endpoint before returning config to the UI.

        Default: masks :meth:`get_secret_fields` as ``"***"`` so secrets
        never leave the server in plaintext. Override to add custom
        transforms, then call super() to keep masking.
        """
        return mask_fields(config, self.get_secret_fields())

    def decrypt_for_use(self, config: dict) -> dict:
        """Hook called by the integration's own provider when it needs the
        plaintext config (e.g. to build an MCP client).

        Default: decrypts :meth:`get_secret_fields`. Not called by the
        platform endpoint — only by the integration itself.
        """
        return decrypt_fields(config, self.get_secret_fields())
