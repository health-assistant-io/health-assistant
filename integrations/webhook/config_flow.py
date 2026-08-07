from integrations.sdk import BaseConfigFlow
import os

class WebhookConfigFlow(BaseConfigFlow):
    """Config flow for the generic webhook integration.

    Security model: the webhook ingest route is unauthenticated by design
    (third-party services POST to it). Historically the UUID-in-URL was the
    only credential, which is too weak — UUIDs leak via logs, browser
    history, and JSON responses. The ``webhook_secret`` is now **required**:
    the platform endpoint verifies an HMAC-SHA256 signature over the raw
    body (``X-Webhook-Signature`` / ``X-Webhook-Signature-256`` /
    ``X-Hub-Signature-256``) before dispatch reaches the parser. The secret
    is Fernet-encrypted at rest and masked on read.
    """

    domain = "webhook"

    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure Webhook",
            "description": (
                "Create a new webhook instance. After saving, view details to "
                "get your unique URL and your signing secret. Configure the "
                "same secret in the third-party service so its payloads are "
                "HMAC-signed (X-Webhook-Signature = HMAC-SHA256 of the raw body)."
            ),
            "data_schema": {
                "type": "object",
                "required": ["instance_name", "parser_type", "webhook_secret"],
                "properties": {
                    "instance_name": {
                        "type": "string",
                        "title": "Instance Name",
                        "description": "Give this webhook connection a friendly name (e.g., 'My Scale', 'Phone Tracker')",
                        "default": "Universal Webhook"
                    },
                    "parser_type": {
                        "type": "string",
                        "title": "Payload Format Parser",
                        "description": "Select how the incoming JSON data should be parsed.",
                        "enum": ["life_dashboard", "basic", "home_assistant", "custom"],
                        "enum_descriptions": {
                            "life_dashboard": "Extracts Steps, Heart Rate (Active/Resting), Body Weight, and Sleep Duration from the open-source Life Dashboard Android app.",
                            "basic": "Expects a simple flat JSON payload. Valid 'type' values are 'heart_rate', 'steps', and 'weight'.",
                            "home_assistant": "Parses Home Assistant state-change events. Matches 'entity_id' for 'heart_rate' and 'steps'.",
                            "custom": "Provides a text area below to map custom JSONPath expressions for incoming data."
                        },
                        "default": "life_dashboard"
                    },
                    "webhook_secret": {
                        "type": "string",
                        "format": "password",
                        "title": "Webhook Signing Secret",
                        "description": (
                            "Required. Configure this same value in the "
                            "third-party service; inbound payloads must carry "
                            "X-Webhook-Signature (or -256 / X-Hub-Signature-256) "
                            "= HMAC-SHA256(secret, raw_body). Min 16 characters."
                        ),
                    },
                    "custom_mapping_json": {
                        "type": "string",
                        "format": "json",
                        "title": "Custom JSONPath Mapping",
                        "description": "Provide a JSON configuration to map incoming fields to Health Assistant metrics.",
                        "default": '{\n  "heart_rate": {\n    "value_path": "$.vitals.hr",\n    "timestamp_path": "$.timestamp",\n    "timestamp_format": "unix_ms"\n  }\n}'
                    },
                    "track_heart_rate": {
                        "type": "boolean",
                        "title": "Track Heart Rate",
                        "default": True
                    },
                    "track_steps": {
                        "type": "boolean",
                        "title": "Track Steps",
                        "default": True
                    },
                    "track_sleep": {
                        "type": "boolean",
                        "title": "Track Sleep",
                        "default": True
                    },
                    "track_weight": {
                        "type": "boolean",
                        "title": "Track Weight",
                        "default": True
                    }
                }
            }
        }

    async def validate_input(self, user_input: dict) -> dict:
        secret = (user_input.get("webhook_secret") or "").strip()
        if not secret:
            raise ValueError(
                "A webhook signing secret is required. Configure the same "
                "value in the third-party service so payloads are HMAC-signed."
            )
        if len(secret) < 16:
            raise ValueError(
                "Webhook signing secret must be at least 16 characters for "
                "adequate HMAC strength."
            )
        user_input["webhook_secret"] = secret
        return user_input

    def get_secret_fields(self) -> list[str]:
        # Fernet-encrypted at rest + masked on read.
        return ["webhook_secret"]
