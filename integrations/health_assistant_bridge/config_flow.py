from integrations.sdk import BaseConfigFlow


class HealthAssistantBridgeConfigFlow(BaseConfigFlow):
    """Config flow for the Health Assistant Bridge.

    Security model: the two-way API proxy exposes ``/api/<id>/{status,map,sync}``
    endpoints. By default the UUID-in-URL is the only secret (acceptable for a
    self-hosted box on a trusted/LAN). For a bridge exposed to the public
    internet, the user should set ``api_secret`` — when present the platform
    endpoint verifies an HMAC-SHA256 signature (``X-Api-Signature`` +
    ``X-Api-Timestamp``, ±5 min replay window) on **every** path (including
    ``/status``) via :func:`integrations.sdk.webhook_security.verify_canonical_signature`
    before dispatch reaches the provider. The secret is Fernet-encrypted at
    rest and masked on read.
    """

    domain = "health_assistant_bridge"

    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure Health Assistant Bridge",
            "description": (
                "Configure the bridge integration for browser extensions or "
                "mobile apps. Each instance generates a unique secure URL bound "
                "to the selected patient. For an internet-exposed instance, "
                "set an API Secret — when set, /map and /sync require an "
                "HMAC-SHA256 signature (X-Api-Signature + X-Api-Timestamp)."
            ),
            "data_schema": {
                "type": "object",
                "properties": {
                    "instance_name": {
                        "type": "string",
                        "title": "Instance Name",
                        "description": "A name for this connection (e.g., 'My Health Portal App', 'Son's NHS Extension').",
                    },
                    "api_secret": {
                        "type": "string",
                        "format": "password",
                        "title": "API Secret (optional, recommended for internet-exposed instances)",
                        "description": (
                            "When set, /map and /sync requests must be signed "
                            "with HMAC-SHA256 (X-Api-Signature over "
                            "METHOD\\n<path>\\n<timestamp>\\n<body> + "
                            "X-Api-Timestamp epoch seconds, ±5 min window)."
                        ),
                    },
                },
                "required": ["instance_name"],
            },
        }

    async def validate_input(self, user_input: dict) -> dict:
        if not user_input.get("instance_name"):
            raise ValueError("Instance name is required.")
        # api_secret is optional; empty/whitespace means "UUID-only mode".
        secret = user_input.get("api_secret")
        if secret is not None:
            secret = secret.strip()
            if secret:
                if len(secret) < 16:
                    raise ValueError(
                        "API Secret must be at least 16 characters for adequate "
                        "HMAC strength."
                    )
                user_input["api_secret"] = secret
            else:
                # An empty/whitespace secret clears any previously-set one.
                user_input.pop("api_secret", None)
        return user_input

    def get_secret_fields(self) -> list[str]:
        # Fernet-encrypted at rest + masked on read. Leave it out of the list
        # when unset so the encrypt path skips it (no empty-string ciphertext).
        return ["api_secret"]