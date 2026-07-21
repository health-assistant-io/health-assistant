"""Config flow for the Dev Dummy reference integration.

This config flow demonstrates the full set of :class:`BaseConfigFlow` hooks:

* JSON-schema-driven UI (``get_schema``) — every field type the renderer
  supports (string / integer / boolean / enum with descriptions).
* Input validation (``validate_input``) with user-facing error messages.
* Per-user instance cap (``max_instances_per_user``) — the endpoint enforces
  this on create without knowing which domain.
* Secret-field lifecycle (``get_secret_fields`` + the
  ``prepare_for_storage`` / ``prepare_for_read`` / ``decrypt_for_use`` trio
  inherited from :class:`integrations.sdk.base.BaseConfigFlow`). The
  ``webhook_secret`` field is stored Fernet-encrypted at rest, masked as
  ``"***"`` on read, and decrypted on demand by the provider when it
  validates inbound webhook signatures.

The defaults intentionally turn every capability ON so that a freshly
configured instance exercises the whole SDK on its first sync.
"""
from typing import List

from integrations.sdk import BaseConfigFlow


class DevDummyConfigFlow(BaseConfigFlow):
    """Reference config flow exercising every BaseConfigFlow hook."""

    domain = "dev_dummy"

    # ------------------------------------------------------------------
    # Capability flags
    # ------------------------------------------------------------------

    # Demonstrate the per-user instance cap. The integrations endpoint
    # enforces this on create only (not on update), without any
    # per-domain code.
    max_instances_per_user = 3

    # ------------------------------------------------------------------
    # Secret-field lifecycle
    # ------------------------------------------------------------------

    def get_secret_fields(self) -> List[str]:
        """Declare ``webhook_secret`` as a Fernet-encrypted field.

        The platform endpoint:
        * Calls :meth:`prepare_for_storage` before persisting — encrypts
          the value via ``INTEGRATION_SECRET_KEY``.
        * Calls :meth:`prepare_for_read` before returning config to the
          UI — masks the value as ``"***"``.
        The provider calls :meth:`decrypt_for_use` (or
        ``decrypt_fields``) when it needs the plaintext to validate an
        HMAC signature.
        """
        return ["webhook_secret"]

    # ``prepare_for_storage`` / ``prepare_for_read`` / ``decrypt_for_use``
    # all delegate to the SDK defaults (encrypt_fields / mask_fields /
    # decrypt_fields). Override only if you need extra transforms — call
    # ``super()`` first/last to keep encryption working.

    # ------------------------------------------------------------------
    # Schema + validation
    # ------------------------------------------------------------------

    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure Dev Dummy",
            "description": (
                "Reference integration demonstrating every SDK capability. "
                "Toggle features below — every enabled one emits sample "
                "data on the next sync."
            ),
            "data_schema": {
                "type": "object",
                "properties": {
                    # Multi-instance integrations should always include
                    # ``instance_name``. The endpoint pops it out of the
                    # config and stores it as ``UserIntegration.instance_name``.
                    "instance_name": {
                        "type": "string",
                        "title": "Instance Name",
                        "description": "Friendly label for this instance (e.g. 'Test patient').",
                        "default": "Dev Dummy",
                    },
                    # Reserved key — the worker reads this to set the
                    # per-instance sync cadence (default 15 min).
                    "sync_interval": {
                        "type": "integer",
                        "title": "Sync Interval (Minutes)",
                        "default": 15,
                        "minimum": 1,
                        "maximum": 1440,
                    },
                    # Secret field. Demonstrates the encrypt/mask/decrypt
                    # round-trip — used by ``handle_webhook`` to validate
                    # inbound HMAC signatures.
                    "webhook_secret": {
                        "type": "string",
                        "title": "Webhook Signing Secret",
                        "description": (
                            "Optional. If set, the webhook handler requires "
                            "an ``X-DevDummy-Signature`` header = "
                            "HMAC-SHA256(secret, body)."
                        ),
                    },
                    # ---- Metric generation toggles (pull_data) ----
                    "generate_heart_rate": {
                        "type": "boolean",
                        "title": "Generate Heart Rate (bpm)",
                        "default": True,
                    },
                    "generate_blood_pressure": {
                        "type": "boolean",
                        "title": "Generate Blood Pressure (mmHg)",
                        "default": True,
                    },
                    "generate_weight": {
                        "type": "boolean",
                        "title": "Generate Body Weight (kg)",
                        "default": False,
                    },
                    # Demonstrates ``ObservationBuilder.set_value_string``
                    # — categorical (FHIR ``valueString``) observations.
                    "generate_mood": {
                        "type": "boolean",
                        "title": "Generate Mood (categorical: good/ok/bad)",
                        "default": True,
                    },
                    # ---- Error simulation (pull_data) ----
                    "simulate_auth_error": {
                        "type": "boolean",
                        "title": "Simulate Auth Error",
                        "description": "Raises IntegrationAuthError → status flips to ERROR.",
                        "default": False,
                    },
                    "simulate_rate_limit": {
                        "type": "boolean",
                        "title": "Simulate Rate Limit",
                        "description": "Raises IntegrationRateLimitError → sync skipped, status unchanged.",
                        "default": False,
                    },
                    "simulate_sensor_glitch": {
                        "type": "boolean",
                        "title": "Simulate Sensor Glitch",
                        "description": (
                            "5% chance per sync of an out-of-range heart "
                            "rate (>200 bpm) — exercises the critical "
                            "sensor-malfunction notification path."
                        ),
                        "default": False,
                    },
                    # ---- Opt-in hook toggles ----
                    # Each toggle enables a different SDK opt-in so you
                    # can see exactly what each one does in isolation.
                    "enable_clinical_events": {
                        "type": "boolean",
                        "title": "Pull Clinical Events",
                        "description": "Opt into supports_clinical_events / pull_clinical_events.",
                        "default": True,
                    },
                    "enable_examinations": {
                        "type": "boolean",
                        "title": "Pull Examinations",
                        "description": "Opt into supports_examinations / pull_examinations.",
                        "default": True,
                    },
                    "enable_catalog_proposals": {
                        "type": "boolean",
                        "title": "Auto-apply Catalog Proposals",
                        "description": "Opt into supports_catalog_proposals (auto-applies a biomarker).",
                        "default": True,
                    },
                    "enable_hitl_proposals": {
                        "type": "boolean",
                        "title": "Queue HITL Proposals",
                        "description": "Opt into supports_hitl_proposals (queues a concept for review).",
                        "default": True,
                    },
                    "enable_documents": {
                        "type": "boolean",
                        "title": "Pull Documents",
                        "description": "Opt into supports_documents (synthesises a small text lab report).",
                        "default": True,
                    },
                    "enable_tools": {
                        "type": "boolean",
                        "title": "Expose Chat Tools",
                        "description": "Opt into supports_tools / get_tools (synthesises 2 dummy LangChain tools).",
                        "default": True,
                    },
                },
                "required": ["sync_interval", "instance_name"],
            },
        }

    async def validate_input(self, user_input: dict) -> dict:
        """Validate the submitted config. Raise ``ValueError`` on error.

        The endpoint translates ``ValueError`` into a 400 with the message
        surfaced to the user.
        """
        if not isinstance(user_input.get("sync_interval"), int) or user_input["sync_interval"] <= 0:
            raise ValueError("Sync interval must be a positive integer.")
        if not user_input.get("instance_name", "").strip():
            raise ValueError("Instance name is required.")
        return user_input
