"""Config flow for the ``fhir_client`` connector (plan 8 W4 item 2).

Collects the connection triple — FHIR base URL, auth mode, patient binding —
plus the generic sync knobs. The API key never persists in ``user_config``:
the form value is routed to a transient slot here and sealed into
``integration_credentials`` (kind ``api_key``, context-bound to the owning
instance) by :meth:`FhirClientConfigFlow.store_credentials`, which the
config-flow endpoint calls after the instance row exists and before
commit. Re-submitting with a new key rotates the sealed slot in place.

SMART-on-FHIR oauth2 is deliberately not offered: the ``fhir_server``
builtin owns that round-trip for hospital two-way sync. A keyed, read-only
pull client with a second OAuth stack would be half-wiring, not a feature.
"""

from app.extensions.sdk import BaseConfigFlow
from app.services.integration.integration_credentials import seal_credential

_PENDING_CREDENTIAL_KEY = "_pending_credential"


class FhirClientConfigFlow(BaseConfigFlow):
    domain = "fhir_client"

    is_oauth = False

    def get_secret_fields(self) -> list:
        return []

    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Connect a FHIR Source",
            "description": (
                "Pull records from an external FHIR R4 server that issues a "
                "static access token (bearer/API key). The token is sealed "
                "into the encrypted credential store — it never appears in "
                "the instance configuration."
            ),
            "data_schema": {
                "type": "object",
                "properties": {
                    "instance_name": {
                        "type": "string",
                        "title": "Instance Name",
                        "default": "My FHIR Source",
                    },
                    "fhir_base_url": {
                        "type": "string",
                        "title": "FHIR Base URL",
                        "description": "e.g. https://fhir.example.org/r4",
                    },
                    "auth_mode": {
                        "type": "string",
                        "title": "Authorization",
                        "enum": ["api_key", "none"],
                        "enum_descriptions": {
                            "api_key": (
                                "Static bearer/API key — sealed into the "
                                "credential store after saving"
                            ),
                            "none": (
                                "None / tokenless — only for servers that "
                                "allow unauthenticated reads"
                            ),
                        },
                        "default": "api_key",
                    },
                    "api_key": {
                        "type": "string",
                        "title": "Access Token (API key)",
                        "description": (
                            "The static token the server accepts as a bearer "
                            "credential. Stored ONLY in the encrypted "
                            "credential store; leave blank when updating an "
                            "instance to keep the current token."
                        ),
                        "x-secret": True,
                    },
                    "remote_patient_id": {
                        "type": "string",
                        "title": "Remote FHIR Patient ID",
                        "description": (
                            "The patient on the remote server whose records "
                            "to pull. Every search is scoped to this "
                            "patient — this connector never pulls unscoped."
                        ),
                    },
                    "sync_interval": {
                        "type": "integer",
                        "title": "Sync Interval (Minutes)",
                        "default": 60,
                        "minimum": 5,
                        "maximum": 1440,
                    },
                    "time_window_months": {
                        "type": "integer",
                        "title": "Initial Pull Window (Months)",
                        "default": 12,
                        "minimum": 1,
                        "maximum": 120,
                    },
                },
                "required": ["fhir_base_url", "instance_name", "auth_mode", "remote_patient_id"],
            },
        }

    async def validate_input(self, user_input: dict) -> dict:
        url = (user_input.get("fhir_base_url") or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            raise ValueError("FHIR Base URL must be a valid http(s) URL.")
        auth_mode = user_input.get("auth_mode", "api_key")
        if auth_mode not in ("api_key", "none"):
            raise ValueError("Authorization must be 'api_key' or 'none'.")
        api_key = user_input.get("api_key")
        if auth_mode == "api_key" and api_key is not None and not str(api_key).strip():
            raise ValueError("The access token must be a non-empty string.")
        remote_patient = (user_input.get("remote_patient_id") or "").strip()
        if not remote_patient:
            raise ValueError(
                "Remote FHIR Patient ID is required — pulls are always patient-scoped."
            )
        window = user_input.get("time_window_months")
        if window is not None and (not isinstance(window, int) or window < 1):
            raise ValueError("Initial Pull Window must be a positive integer.")
        user_input["fhir_base_url"] = url.rstrip("/")
        user_input["auth_mode"] = auth_mode
        user_input["remote_patient_id"] = remote_patient
        return user_input

    async def prepare_for_storage(self, config: dict, *, context: str | None = None) -> dict:
        """Route the API key out of the stored config.

        The plaintext moves to a transient slot that
        :meth:`store_credentials` seals into ``integration_credentials``
        and strips before the instance row commits — ``user_config`` never
        carries credential material at rest.
        """
        out = dict(config)
        api_key = out.pop("api_key", None)
        if isinstance(api_key, str) and api_key.strip():
            out[_PENDING_CREDENTIAL_KEY] = {
                "kind": "api_key",
                "payload": {"api_key": api_key.strip()},
            }
        return out

    async def store_credentials(self, db, integration, validated_config: dict) -> None:
        """Seal the transient slot into the credential table (rotation-safe).

        Called by the config-flow endpoint with the instance row present
        and uncommitted. A blank token on update keeps the current sealed
        slot untouched; a new value rotates it in place (new ``kid``,
        revocation cleared).
        """
        pending = validated_config.pop(_PENDING_CREDENTIAL_KEY, None)
        if not pending:
            return
        kind = pending.get("kind")
        payload = pending.get("payload")
        if kind != "api_key" or not isinstance(payload, dict):
            raise ValueError("Unsupported credential slot submitted.")
        await seal_credential(db, integration, kind, payload)
        integration.user_config = dict(validated_config)

    def prepare_for_read(self, config: dict) -> dict:
        """Defense in depth: the transient slot never reaches the UI."""
        out = dict(config or {})
        out.pop(_PENDING_CREDENTIAL_KEY, None)
        return out
