import logging
from typing import Any

from app.extensions.sdk import BaseHealthProvider
from app.models.user_integration import UserIntegration
from app.schemas.fhir.observation import ObservationCreate

from .parsers import get_parser

logger = logging.getLogger(__name__)


class WebhookProvider(BaseHealthProvider):
    domain = "webhook"

    async def handle_webhook(
        self, integration: UserIntegration, payload: Any, request: Any = None
    ) -> list[ObservationCreate]:
        """Process inbound webhook payloads."""

        # Log the payload for debugging if enabled by the user
        await self.log_debug_payload(integration, "Webhook Payload", payload)

        config = integration.user_config or {}
        parser_type = config.get("parser_type", "life_dashboard")

        parser = get_parser(parser_type)
        builder = self.create_observation_builder(integration)

        try:
            observations = parser.parse(payload, config, builder)
            return observations
        except Exception as e:
            logger.error(f"[{self.domain}] Parser {parser_type} failed: {e}", exc_info=True)
            return []

    def get_custom_actions(self) -> list[dict[str, str]]:
        return [{"id": "test_webhook", "label": "Get Webhook URL", "style": "primary"}]

    async def execute_custom_action(
        self, integration: UserIntegration, action_id: str, **kwargs
    ) -> dict[str, Any]:
        if action_id == "test_webhook":
            webhook_url = f"/api/v1/integrations/webhook/webhook/{integration.id}"
            msg = "Webhook handler active. Configure your app to point to your backend domain: "
            return {"message": msg + webhook_url}

        raise NotImplementedError(f"Action '{action_id}' is not supported.")
