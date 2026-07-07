from abc import ABC, abstractmethod
from typing import Dict, Any, List
from datetime import datetime
from app.models.user_integration import UserIntegration


class BaseHealthProvider(ABC):
    provider_id: str

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    @abstractmethod
    async def get_auth_url(self, state: str) -> str:
        """Return the OAuth redirect URL for the user to authorize."""
        pass

    @abstractmethod
    async def exchange_token(self, code: str) -> Dict[str, Any]:
        """Exchange the authorization code for an access token."""
        pass

    @abstractmethod
    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh the access token when it expires."""
        pass

    @abstractmethod
    async def fetch_data(
        self, integration: UserIntegration, start_date: datetime, end_date: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fetch raw data from the provider's API.
        Must return a list of parsed observation-like dicts ready to be saved into the DB.
        """
        pass
