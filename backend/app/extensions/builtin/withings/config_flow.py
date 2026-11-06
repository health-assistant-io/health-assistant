"""Config flow for the ``withings`` connector (OAuth wearable).

Collects the Withings partner app credentials — client id (non-secret,
stays in ``user_config``) and client secret (never touches config) — plus
the generic sync knobs. The secret moves to a transient slot here and is
sealed into ``integration_credentials`` (kind ``oauth2``, context-bound to
the owning instance) by :meth:`WithingsConfigFlow.store_credentials`,
which the config-flow endpoint calls after the instance row exists and
before commit. Re-submitting with a new secret rotates the sealed slot in
place.

The OAuth access/refresh tokens join the SAME ``oauth2`` slot when the
authorization round-trip completes (``WithingsProvider.complete_oauth``):
one sealed slot holds the whole credential set, and the row's
``access_expires_at``/``refresh_expires_at`` columns carry the expiry the
refresh-before-pull gate reads.
"""

from app.extensions.sdk import BaseConfigFlow
from app.services.integration.integration_credentials import seal_credential

_PENDING_CREDENTIAL_KEY = "_pending_credential"


class WithingsConfigFlow(BaseConfigFlow):
    domain = "withings"

    is_oauth = True

    def get_secret_fields(self) -> list:
        return []

    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Connect Withings",
            "description": (
                "Connect a Withings account through your own Withings "
                "partner app. The client secret is sealed into the "
                "encrypted credential store — it never appears in the "
                "instance configuration. After saving, use Authorize to "
                "run the Withings consent round-trip."
            ),
            "data_schema": {
                "type": "object",
                "properties": {
                    "instance_name": {
                        "type": "string",
                        "title": "Instance Name",
                        "default": "My Withings",
                    },
                    "withings_client_id": {
                        "type": "string",
                        "title": "Withings Client ID",
                        "description": (
                            "The client id of your Withings partner app "
                            "(partner.withings.com — non-secret)."
                        ),
                    },
                    "withings_client_secret": {
                        "type": "string",
                        "title": "Withings Client Secret",
                        "description": (
                            "Stored ONLY in the encrypted credential "
                            "store; leave blank when updating an instance "
                            "to keep the current secret."
                        ),
                        "x-secret": True,
                    },
                    "sync_interval": {
                        "type": "integer",
                        "title": "Sync Interval (Minutes)",
                        "default": 60,
                        "minimum": 5,
                        "maximum": 1440,
                    },
                    "initial_window_days": {
                        "type": "integer",
                        "title": "Initial Pull Window (Days)",
                        "default": 90,
                        "minimum": 1,
                        "maximum": 365,
                    },
                },
                "required": ["instance_name", "withings_client_id"],
            },
        }

    async def validate_input(self, user_input: dict) -> dict:
        client_id = (user_input.get("withings_client_id") or "").strip()
        if not client_id:
            raise ValueError("Withings Client ID is required.")
        secret = user_input.get("withings_client_secret")
        if secret is not None and not str(secret).strip():
            raise ValueError("The client secret must be a non-empty string.")
        window = user_input.get("initial_window_days")
        if window is not None and (not isinstance(window, int) or window < 1):
            raise ValueError("Initial Pull Window must be a positive integer.")
        user_input["withings_client_id"] = client_id
        return user_input

    async def prepare_for_storage(self, config: dict, *, context: str | None = None) -> dict:
        """Route the client secret out of the stored config.

        The plaintext moves to a transient slot that
        :meth:`store_credentials` seals into ``integration_credentials``
        and strips before the instance row commits — ``user_config`` never
        carries credential material at rest.
        """
        out = dict(config)
        secret = out.pop("withings_client_secret", None)
        if isinstance(secret, str) and secret.strip():
            out[_PENDING_CREDENTIAL_KEY] = {
                "kind": "oauth2",
                "payload": {"client_secret": secret.strip()},
            }
        return out

    async def store_credentials(self, db, integration, validated_config: dict) -> None:
        """Seal the transient slot into the credential table (rotation-safe).

        Called by the config-flow endpoint with the instance row present
        and uncommitted. A blank secret on update keeps the current sealed
        slot untouched; a new value rotates it in place (new ``kid``,
        revocation cleared). The OAuth token payload joins this slot when
        the authorization round-trip completes.
        """
        pending = validated_config.pop(_PENDING_CREDENTIAL_KEY, None)
        if not pending:
            return
        kind = pending.get("kind")
        payload = pending.get("payload")
        if kind != "oauth2" or not isinstance(payload, dict):
            raise ValueError("Unsupported credential slot submitted.")
        await seal_credential(db, integration, "oauth2", payload)
        integration.user_config = dict(validated_config)

    def prepare_for_read(self, config: dict) -> dict:
        """Defense in depth: the transient slot never reaches the UI."""
        out = dict(config or {})
        out.pop(_PENDING_CREDENTIAL_KEY, None)
        return out
