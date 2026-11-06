"""Deployment OAuth client configuration (ADR-0052 §3 / plan 52 P2).

Providers are *data* (a catalog row); a registered OAuth client is
*deployment configuration*. Two env maps, both JSON objects keyed by
provider id:

``CALENDAR_OAUTH_CLIENT_IDS``
    The public client id. Required to start a flow; a provider missing one
    is a typed ``needs-config`` refusal, never a crash.

``CALENDAR_OAUTH_CLIENT_SECRETS``
    The client secret, for providers whose registration is a **confidential
    web application** (Google's "Web application" type, which a server-side
    callback redirect forces). It is sent only to the provider's token
    endpoint and is never persisted in the binding slot: the secret belongs
    to this deployment, not to the user, so a binding carries tokens only.

Providers that accept a public client (Microsoft "Mobile and desktop") need
no entry; omitting it keeps the exchange pure PKCE.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def _json_map(env_name: str) -> dict[str, str]:
    raw = os.getenv(env_name, "") or ""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("%s is not valid JSON — treating as unset", env_name)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("%s is not a JSON object — treating as unset", env_name)
        return {}
    return {str(k): str(v) for k, v in parsed.items() if str(v).strip()}


def client_ids() -> dict[str, str]:
    """provider id → registered public client id."""
    return _json_map("CALENDAR_OAUTH_CLIENT_IDS")


def client_secrets() -> dict[str, str]:
    """provider id → client secret, for confidential (web) clients only."""
    return _json_map("CALENDAR_OAUTH_CLIENT_SECRETS")


def client_secret_for(provider_id: str) -> str | None:
    """The deployment's secret for this provider, or None (public client)."""
    return client_secrets().get(provider_id) or None
