from abc import ABC, abstractmethod
from typing import Any, Dict, List
from app.models.fhir import Observation
from app.models.user_integration import UserIntegration

class BaseHealthProvider(ABC):
    domain: str

    async def setup(self, config: dict):
        """Called when the integration is loaded."""
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
        
    async def get_auth_url(self, state: str) -> str:
        """Return the OAuth redirect URL if applicable."""
        return ""

    async def exchange_token(self, code: str) -> Dict[str, Any]:
        """Exchange the auth code for access/refresh tokens if applicable."""
        return {}
        
    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Get a new access token using a refresh token if applicable."""
        return {}

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
