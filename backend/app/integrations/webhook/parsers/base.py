from abc import ABC, abstractmethod
from typing import Any, List, Dict
from app.schemas.fhir.observation import ObservationCreate
from app.models.user_integration import UserIntegration
from app.integrations.sdk.observation_builder import ObservationBuilder

class BaseWebhookParser(ABC):
    """Base class for all webhook payload parsers."""
    
    @abstractmethod
    def parse(self, payload: Any, config: Dict[str, Any], builder: ObservationBuilder) -> List[ObservationCreate]:
        """
        Parse the incoming payload and return a list of FHIR Observations.
        
        Args:
            payload: The raw JSON dictionary received from the webhook.
            config: The user's configuration dictionary for this integration.
            builder: An ObservationBuilder instance pre-configured for this user/tenant.
            
        Returns:
            A list of ObservationCreate objects.
        """
        pass
