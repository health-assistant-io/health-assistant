"""Withings OAuth2 client — the token round-trip over the guarded transport.

Authorization Code flow against the Withings Health Solutions API:

- **authorize** — ``account.withings.com/oauth2_user/authorize2`` with
  ``response_type=code`` (Withings does not implement PKCE; the CSRF
  ``state`` is the binding, consumed one-shot from the platform state
  store).
- **token exchange / refresh** — ``wbsapi.withings.net/v2/oauth2``, form
  encoded, answering the Withings envelope ``{"status": <int>, "body":
  {...}}`` (``status != 0`` is a typed rejection, with two special
  statuses below).

Every HTTP call rides :func:`app.extensions.runtime.http.guarded_request`
(the SSRF-guarded, per-hop revalidated, pinned-address runtime) — never a
bare ``httpx`` call. OAuth-shaped 4xx bodies (RFC 6749 §5.2 ``error``) and
credential-class envelope statuses raise the charter ``auth`` code so the
engine flips the instance to ERROR with a reconnect prompt instead of
failing silently every sync.

Secrets never live here: the caller passes the client secret from the
sealed ``integration_credentials`` slot (kind ``oauth2``) and hands the
token response back for resealing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.extensions.errors import ExtensionError
from app.extensions.runtime.http import guarded_request

AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"

#: ``user.metrics`` covers the measure categories this connector pulls.
SCOPES = "user.metrics"

#: Access tokens are short-lived; the response carries ``expires_in``.
ACCESS_TTL_FALLBACK_SECONDS = 3 * 3600
#: Withings refresh tokens are long-lived but the token response carries no
#: explicit expiry. A conservative bounded default keeps the credential
#: row's ``refresh_expires_at`` column meaningful (the fail-closed gate
#: reads it) instead of silently immortal.
REFRESH_TTL_FALLBACK_SECONDS = 365 * 24 * 3600

#: Envelope statuses the Withings docs classify as credential problems:
#: 190 user not on this partner app, 247/250 userid absent or mismatched,
#: 283 access revoked, 304 authorization code spent, 342 credentials
#: rejected, 343 access token expired/invalid. Everything else non-zero is
#: a protocol rejection (or one of the two special statuses below).
AUTH_ENVELOPE_STATUSES = frozenset({190, 247, 250, 283, 304, 342, 343})

#: ``status: 100`` on the measure endpoint means "no data for the window" —
#: an EMPTY pull, never an error (a quiet scale must not fail the sync).
ENVELOPE_NO_DATA_STATUS = 100

#: ``status: 601`` is Withings' in-envelope rate limit (next to the HTTP
#: 429 the guarded transport already honors, Retry-After included).
ENVELOPE_RATE_LIMIT_STATUS = 601


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    *,
    scope: str = SCOPES,
) -> str:
    """Assemble the Withings authorize URL (response_type=code)."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": scope,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def _token_request(
    client: httpx.AsyncClient,
    form: dict[str, str],
) -> dict[str, Any]:
    """POST the token endpoint behind the guarded transport and unwrap the
    Withings envelope.

    A 4xx body carrying an RFC 6749 ``error`` field (``invalid_grant``,
    ``invalid_client``, …) is the typed ``auth`` error — the exchange
    material is wrong or spent, and retrying cannot fix it.
    """
    response = await guarded_request(
        client,
        "POST",
        TOKEN_URL,
        headers={"Accept": "application/json"},
        data=form,
    )
    if response.status_code >= 400:
        try:
            body = response.json()
        except (ValueError, TypeError):
            body = None
        if isinstance(body, dict) and body.get("error"):
            raise ExtensionError(
                "auth",
                f"Withings token endpoint returned {response.status_code}: "
                f"OAuth error {body.get('error')!r}",
                status=response.status_code,
            )
        raise ExtensionError(
            "protocol",
            f"Withings token endpoint returned {response.status_code}",
            status=response.status_code,
        )
    try:
        envelope = response.json()
    except ValueError as exc:
        raise ExtensionError(
            "protocol", "non-JSON response from the Withings token endpoint"
        ) from exc
    if not isinstance(envelope, dict):
        raise ExtensionError("protocol", "malformed response from the Withings token endpoint")
    status = envelope.get("status")
    body = envelope.get("body")
    if status != 0 or not isinstance(body, dict):
        detail = body.get("error_description") if isinstance(body, dict) else None
        if status == ENVELOPE_RATE_LIMIT_STATUS:
            raise ExtensionError("rate-limited", "Withings rate-limited the token request")
        if status in AUTH_ENVELOPE_STATUSES:
            raise ExtensionError(
                "auth",
                "Withings rejected the credential"
                + (f" ({detail})" if isinstance(detail, str) and detail else ""),
            )
        raise ExtensionError(
            "protocol",
            f"Withings token request failed (status {status!r})"
            + (f": {detail}" if isinstance(detail, str) and detail else ""),
        )
    if not body.get("access_token"):
        raise ExtensionError("auth", "Withings token response carries no access_token")
    return body


async def exchange_code(
    client: httpx.AsyncClient,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Exchange the authorization code for the Withings token body."""
    return await _token_request(
        client,
        {
            "action": "requesttoken",
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )


async def refresh_tokens(
    client: httpx.AsyncClient,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Refresh an access token; returns the Withings token body.

    Withings rotates the refresh token on refresh — the caller must reseal
    the NEW one (the old becomes invalid 8 hours after the next issuance).
    """
    return await _token_request(
        client,
        {
            "action": "requesttoken",
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
    )


async def fetch_measuregrps(
    client: httpx.AsyncClient,
    access_token: str,
    form: dict[str, str],
) -> dict[str, Any]:
    """One ``measure`` API call (``action=getmeas``) behind the guarded
    transport; returns the unwrapped ``body`` (``measuregrps`` + paging).

    ``status: 100`` (no data in the window) returns an empty body — a quiet
    scale is a successful empty pull, never a failure. A credential-status
    envelope (expired/invalid access token) raises the typed ``auth`` error
    — the pull fails closed instead of producing zero data forever; an
    in-envelope rate limit raises ``rate-limited`` (the engine records the
    ``next_retry_at`` backoff as data).
    """
    response = await guarded_request(
        client,
        "POST",
        MEASURE_URL,
        headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
        data={"action": "getmeas", **form},
    )
    if response.status_code >= 400:
        raise ExtensionError(
            "protocol",
            f"Withings measure endpoint returned {response.status_code}",
            status=response.status_code,
        )
    try:
        envelope = response.json()
    except ValueError as exc:
        raise ExtensionError(
            "protocol", "non-JSON response from the Withings measure endpoint"
        ) from exc
    if not isinstance(envelope, dict):
        raise ExtensionError("protocol", "malformed response from the Withings measure endpoint")
    status = envelope.get("status")
    body = envelope.get("body")
    if status == ENVELOPE_NO_DATA_STATUS:
        return {}
    if status != 0 or not isinstance(body, dict):
        if status == ENVELOPE_RATE_LIMIT_STATUS:
            raise ExtensionError("rate-limited", "Withings rate-limited the measure pull")
        if status in AUTH_ENVELOPE_STATUSES:
            raise ExtensionError("auth", "Withings rejected the access token for the measure pull")
        raise ExtensionError("protocol", f"Withings measure request failed (status {status!r})")
    return body


def normalize_token(body: dict[str, Any]) -> dict[str, Any]:
    """Reduce a Withings token body to the sealed-slot payload + expiry pair.

    Returns ``(payload, access_expires_at, refresh_expires_at)``. The
    payload carries exactly the secret material the ``oauth2`` slot holds:
    the access/refresh token pair plus the client secret (carried by the
    caller — a refresh must keep it). Expiry columns are stamped from
    ``expires_in`` (access) and the conservative refresh default.
    """
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise ExtensionError("auth", "Withings token body carries no usable access_token")
    now = datetime.now(UTC)
    expires_in = body.get("expires_in")
    ttl = int(expires_in) if isinstance(expires_in, (int, float)) else ACCESS_TTL_FALLBACK_SECONDS
    access_expires_at = now + timedelta(seconds=ttl)
    refresh_expires_at = now + timedelta(seconds=REFRESH_TTL_FALLBACK_SECONDS)
    payload: dict[str, Any] = {"access_token": access}
    if isinstance(refresh, str) and refresh:
        payload["refresh_token"] = refresh
    scope = body.get("scope")
    if isinstance(scope, str) and scope:
        payload["scope"] = scope
    return payload, access_expires_at, refresh_expires_at
