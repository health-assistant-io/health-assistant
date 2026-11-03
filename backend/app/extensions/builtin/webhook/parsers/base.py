from abc import ABC, abstractmethod
from typing import Any

from app.extensions.sdk.observation_builder import ObservationBuilder
from app.schemas.fhir.observation import ObservationCreate


class BaseWebhookParser(ABC):
    """Base class for all webhook payload parsers."""

    @abstractmethod
    def parse(
        self, payload: Any, config: dict[str, Any], builder: ObservationBuilder
    ) -> list[ObservationCreate]:
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
