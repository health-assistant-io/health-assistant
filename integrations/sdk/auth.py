"""OAuth2 + PKCE + SMART-on-FHIR auth for cloud integrations.

Reusable primitives for any integration that needs an OAuth2 Authorization Code
flow with PKCE (Fitbit, Withings, Apple Health cloud, ...). On top of that, a
SMART-on-FHIR layer provides:

- ``discover_smart`` — read ``/.well-known/smart-configuration``.
- ``register_client`` — Dynamic Client Registration (RFC 7591) so the user only
  needs to enter the server URL (no pre-registered client_id).
- ``build_authorize_url`` — assemble the standalone-launch authorize URL.

Token + state storage:

- :class:`OAuthTokenStore` persists tokens inside ``UserIntegration.user_config
  ["_oauth"]`` with ``access_token``/``refresh_token`` encrypted via the SDK
  Fernet cipher (:mod:`integrations.sdk.secrets`). The caller (endpoint/worker)
  persists the ``user_config`` mutation.
- :class:`OAuthStateStore` holds the short-lived ``state`` + PKCE verifier in
  Redis (CSRF + callback correlation, ~10-min TTL).

HTTP is performed via an injected ``httpx.AsyncClient`` (the provider's pooled
client) so the module is decoupled from the provider and unit-testable with
mocked responses. OAuth/DCR/token responses are validated; auth failures raise
:class:`~integrations.sdk.exceptions.IntegrationAuthError`, malformed server
responses raise :class:`~integrations.sdk.exceptions.IntegrationDataError`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx

from app.models.user_integration import UserIntegration
from .exceptions import IntegrationAuthError, IntegrationDataError
from .http import _retry_request
from .secrets import SecretCipher

logger = logging.getLogger(__name__)

OAUTH_CONFIG_KEY = "_oauth"
STATE_TTL_SECONDS = 600
DEFAULT_SCOPES = "openid fhirUser launch patient/*.read offline_access"
PUSH_SCOPES = "openid fhirUser launch patient/*.read patient/*.write offline_access"


# ---------------------------------------------------------------------------
# PKCE (RFC 7636)
# ---------------------------------------------------------------------------

def generate_pkce(verifier_length: int = 64) -> Tuple[str, str, str]:
    """Return ``(code_verifier, code_challenge, "S256")``.

    ``code_verifier`` is a high-entropy URL-safe string of 43-128 chars;
    ``code_challenge`` is the base64url(SHA256) of it (padding stripped).
    """
    verifier = secrets.token_urlsafe(verifier_length)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge, "S256"


def generate_state() -> str:
    """Opaque CSRF ``state`` value for the authorize round-trip."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Low-level HTTP helper (raises SDK exceptions on auth/data errors)
# ---------------------------------------------------------------------------

async def _request_json(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """OAuth / DCR / token-exchange HTTP helper.

    Delegates retry / backoff / jitter / status-mapping to
    :func:`integrations.sdk.http._retry_request` — the same retry contract
    every other SDK HTTP call uses (no more single-shot OAuth requests that
    fail loudly on a transient 429/5xx). 401/403 →
    :class:`IntegrationAuthError`, 429-after-retries →
    :class:`IntegrationRateLimitError`, other 4xx / non-JSON →
    :class:`IntegrationDataError`.
    """
    response = await _retry_request(
        lambda: http.request(method, url, headers=headers, data=data, json=json_body),
        url=url,
        method=method,
        max_retries=max_retries,
    )
    if response.status_code >= 400:
        # 401/403 already raised by _retry_request; remaining 4xx are
        # non-retryable OAuth / DCR errors.
        raise IntegrationDataError(
            f"{method} {url} -> {response.status_code}: {response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError as e:
        raise IntegrationDataError(
            f"Non-JSON response from {url}: {response.text[:300]}"
        ) from e


# ---------------------------------------------------------------------------
# SMART-on-FHIR discovery + Dynamic Client Registration
# ---------------------------------------------------------------------------

async def discover_smart(fhir_base_url: str, http: httpx.AsyncClient) -> Dict[str, Any]:
    """Read ``{fhir_base}/.well-known/smart-configuration``.

    Returns the SMART config dict (``authorization_endpoint``,
    ``token_endpoint``, ``registration_endpoint`` if DCR is supported,
    ``scopes_supported``, ``capabilities``). Raises
    :class:`IntegrationDataError` if the server has no SMART metadata.
    """
    base = fhir_base_url.rstrip("/")
    url = f"{base}/.well-known/smart-configuration"
    config = await _request_json(http, "GET", url)
    if not config.get("authorization_endpoint") or not config.get("token_endpoint"):
        raise IntegrationDataError(
            f"{url} is missing authorization_endpoint/token_endpoint; not a SMART server."
        )
    return config


async def register_client(
    registration_endpoint: str,
    redirect_uris: list,
    client_name: str,
    *,
    scopes: str = DEFAULT_SCOPES,
    http: httpx.AsyncClient,
) -> Dict[str, Any]:
    """Dynamic Client Registration (RFC 7591).

    Registers a **public** client (``token_endpoint_auth_method="none"``) so the
    self-hosted instance needs no client secret. Returns the registration
    response; the ``client_id`` is what's used in the authorize/token calls.
    Raises :class:`IntegrationDataError` if no ``client_id`` comes back.
    """
    body = {
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": scopes,
    }
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    reg = await _request_json(
        http, "POST", registration_endpoint, headers=headers, json_body=body
    )
    if not reg.get("client_id"):
        raise IntegrationDataError(f"DCR response missing client_id: {reg}")
    return reg


def build_authorize_url(
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    *,
    scope: str = DEFAULT_SCOPES,
    aud: Optional[str] = None,
) -> str:
    """Assemble the standalone-launch authorize URL (response_type=code, PKCE S256)."""
    params: Dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": scope,
    }
    if aud:
        params["aud"] = aud
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{urlencode(params)}"


# ---------------------------------------------------------------------------
# Token exchange + refresh
# ---------------------------------------------------------------------------

async def exchange_code(
    token_endpoint: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
    *,
    client_secret: Optional[str] = None,
    http: httpx.AsyncClient,
) -> Dict[str, Any]:
    """Exchange an authorization code for a token dict.

    Returns the raw token response plus a computed ``expires_at`` (ISO 8601) and
    the resolved ``patient`` id (SMART standalone launch returns it). Raises
    :class:`IntegrationAuthError` on a 401/400 from the token endpoint.
    """
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    if client_secret:
        form["client_secret"] = client_secret
    token = await _request_json(
        http, "POST", token_endpoint, headers={"Accept": "application/json"}, data=form
    )
    if not token.get("access_token"):
        raise IntegrationAuthError(f"Token endpoint returned no access_token: {token}")
    return _normalize_token(token)


async def refresh_token(
    token_endpoint: str,
    refresh_token_value: str,
    client_id: str,
    *,
    client_secret: Optional[str] = None,
    http: httpx.AsyncClient,
) -> Dict[str, Any]:
    """Refresh an access token. Returns a normalized token dict (see :func:`exchange_code`)."""
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_value,
        "client_id": client_id,
    }
    if client_secret:
        form["client_secret"] = client_secret
    token = await _request_json(
        http, "POST", token_endpoint, headers={"Accept": "application/json"}, data=form
    )
    if not token.get("access_token"):
        raise IntegrationAuthError(f"Refresh returned no access_token: {token}")
    return _normalize_token(token)


def _normalize_token(token: Dict[str, Any]) -> Dict[str, Any]:
    """Add ``expires_at`` (ISO) from ``expires_in``; carry over ``patient``/``scope``."""
    out = dict(token)
    expires_in = token.get("expires_in")
    if isinstance(expires_in, (int, float)):
        out["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        ).isoformat()
    return out


# ---------------------------------------------------------------------------
# Token storage (encrypted inside user_config["_oauth"])
# ---------------------------------------------------------------------------

_TOKEN_SECRET_FIELDS = ["access_token", "refresh_token", "client_secret"]
_CONNECTION_FIELDS = (
    "access_token",
    "refresh_token",
    "client_secret",
    "expires_at",
    "patient",
    "scope",
    "token_endpoint",
    "revocation_endpoint",
    "client_id",
    "fhir_base_url",
)


class OAuthTokenStore:
    """Read/write the OAuth token blob from ``integration.user_config["_oauth"]``.

    ``access_token`` and ``refresh_token`` are Fernet-encrypted at rest (the
    cipher comes from ``INTEGRATION_SECRET_KEY``). Other fields (``expires_at``,
    ``patient``, ``scope``, ``client_id``) are stored in plaintext. The caller is
    responsible for persisting the ``user_config`` mutation (the SDK's
    ``set_sync_cursor`` / the endpoint's ``flag_modified`` + commit).
    """

    def __init__(self, cipher: Optional[SecretCipher] = None) -> None:
        self._cipher = cipher

    def _get_cipher(self) -> SecretCipher:
        if self._cipher is None:
            self._cipher = SecretCipher.from_settings()
        return self._cipher

    def store(self, integration: UserIntegration, token: Dict[str, Any]) -> None:
        """Persist a (normalized) token dict, encrypting the secret fields.

        Merges into the existing ``_oauth`` blob (a refresh keeps connection
        metadata like ``token_endpoint``/``client_id`` if the response omits
        them). Replaces the ``user_config`` dict so SQLAlchemy detects the JSONB
        mutation.
        """
        existing = dict(integration.user_config or {})
        oauth = dict(existing.get(OAUTH_CONFIG_KEY) or {})

        for k in _CONNECTION_FIELDS:
            if k in token:
                oauth[k] = token[k]
        oauth = self._encrypt_secrets(oauth)

        existing[OAUTH_CONFIG_KEY] = oauth
        integration.user_config = existing

    def _encrypt_secrets(self, oauth: Dict[str, Any]) -> Dict[str, Any]:
        cipher = self._get_cipher()
        out = dict(oauth)
        for field in _TOKEN_SECRET_FIELDS:
            val = out.get(field)
            if val not in (None, "", {}, []):
                out[field] = cipher.encrypt_value(val)
        return out

    def _decrypt_secrets(self, oauth: Dict[str, Any]) -> Dict[str, Any]:
        cipher = self._get_cipher()
        out = dict(oauth)
        for field in _TOKEN_SECRET_FIELDS:
            if field in out:
                out[field] = cipher.decrypt_value(out[field])
        return out

    def _read(self, integration: UserIntegration) -> Dict[str, Any]:
        oauth = (integration.user_config or {}).get(OAUTH_CONFIG_KEY) or {}
        return self._decrypt_secrets(oauth)

    def get_access_token(self, integration: UserIntegration) -> Optional[str]:
        return self._read(integration).get("access_token")

    def get_refresh_token(self, integration: UserIntegration) -> Optional[str]:
        return self._read(integration).get("refresh_token")

    def get_patient(self, integration: UserIntegration) -> Optional[str]:
        return self._read(integration).get("patient")

    def get_scope(self, integration: UserIntegration) -> Optional[str]:
        return self._read(integration).get("scope")

    def is_expired(self, integration: UserIntegration, leeway_seconds: int = 60) -> bool:
        oauth = self._read(integration)
        expires_at = oauth.get("expires_at")
        if not expires_at:
            return True
        try:
            expiry = datetime.fromisoformat(str(expires_at))
        except ValueError:
            return True
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= (expiry - timedelta(seconds=leeway_seconds))

    async def refresh_if_needed(
        self,
        integration: UserIntegration,
        http: httpx.AsyncClient,
        *,
        token_endpoint: str,
        client_id: str,
        client_secret: Optional[str] = None,
    ) -> str:
        """Return a live access token, refreshing first if expired.

        Raises :class:`IntegrationAuthError` if there is no refresh token or the
        refresh fails.
        """
        if not self.is_expired(integration):
            return self.get_access_token(integration)

        refresh = self.get_refresh_token(integration)
        if not refresh:
            raise IntegrationAuthError(
                f"Integration {integration.id} token expired and no refresh_token stored."
            )
        token = await refresh_token(
            token_endpoint, refresh, client_id, client_secret=client_secret, http=http
        )
        self.store(integration, token)
        return token["access_token"]


# ---------------------------------------------------------------------------
# OAuth state store (Redis, short-lived, one-shot consume)
# ---------------------------------------------------------------------------

class OAuthStateStore:
    """Redis-backed store for the OAuth ``state`` + PKCE verifier (CSRF).

    ``issue`` writes ``state -> payload`` with a TTL; ``consume`` atomically
    reads-and-deletes so a state can only be used once. The Redis client is
    injectable (tests pass ``fakeredis``); the default is the platform
    ``app.core.redis.redis_client`` (``decode_responses=True``).
    """

    KEY_PREFIX = "oauth:state:"

    def __init__(self, redis_client: Any = None, ttl_seconds: int = STATE_TTL_SECONDS) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    def _client(self) -> Any:
        if self._redis is None:
            from app.core.redis import redis_client

            self._redis = redis_client
        return self._redis

    async def issue(self, state: str, payload: Dict[str, Any]) -> None:
        client = self._client()
        await client.set(
            f"{self.KEY_PREFIX}{state}", json.dumps(payload), ex=self._ttl
        )

    async def consume(self, state: str) -> Optional[Dict[str, Any]]:
        """One-shot atomic read: returns the payload and deletes the key, or ``None``.

        Uses ``GETDEL`` (Redis ≥ 6.2) so the read-and-delete is a single atomic
        operation — no TOCTOU window where two concurrent callbacks could both
        read the same state. Falls back to a Lua-script ``GETDEL`` polyfill for
        older Redis versions.
        """
        client = self._client()
        key = f"{self.KEY_PREFIX}{state}"
        try:
            raw = await client.execute_command("GETDEL", key)
        except Exception:
            # GETDEL not supported (Redis < 6.2) — fall back to a Lua polyfill.
            try:
                raw = await client.eval(
                    "local v=redis.call('GET',KEYS[1]);"
                    "if v then redis.call('DEL',KEYS[1]) end;"
                    "return v",
                    1, key,
                )
            except Exception:
                # Last resort: non-atomic GET + DELETE (the historical path).
                raw = await client.get(key)
                if raw is not None:
                    await client.delete(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as e:
            logger.warning("Malformed OAuth state payload for %s: %s", state, e)
            return None


# ---------------------------------------------------------------------------
# Composed helper
# ---------------------------------------------------------------------------

class SmartOAuth:
    """High-level SMART-on-FHIR Authorization Code + PKCE flow.

    Ties discovery → DCR → authorize URL → token exchange together, with token
    persistence handled by :class:`OAuthTokenStore`. Both the config flow
    (connect) and the provider (refresh-on-use) consume this.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        token_store: Optional[OAuthTokenStore] = None,
        state_store: Optional[OAuthStateStore] = None,
        cipher: Optional[SecretCipher] = None,
    ) -> None:
        self.http = http
        self.tokens = token_store or OAuthTokenStore(cipher=cipher)
        self.states = state_store or OAuthStateStore()

    async def begin_connect(
        self,
        fhir_base_url: str,
        redirect_uri: str,
        client_name: str,
        *,
        scopes: str = DEFAULT_SCOPES,
        push_enabled: bool = False,
        extra_state: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """Discover + DCR-register + build the authorize URL.

        Returns ``(authorize_url, state)``. The caller issues the redirect; the
        PKCE verifier + SMART config + client_id are stored under ``state``
        (merged with ``extra_state`` so the platform can correlate the callback
        to its own context — e.g. ``integration_id``/``user_id``). The ``state``
        is returned so the caller knows which key was used.

        When ``push_enabled`` is True, ``PUSH_SCOPES`` is used instead of
        ``DEFAULT_SCOPES`` so the SMART consent screen includes
        ``patient/*.write`` (H1).
        """
        if push_enabled and scopes == DEFAULT_SCOPES:
            scopes = PUSH_SCOPES
        config = await discover_smart(fhir_base_url, self.http)
        client_id: Optional[str] = None
        client_secret: Optional[str] = None
        reg_endpoint = config.get("registration_endpoint")
        if reg_endpoint:
            reg = await register_client(
                reg_endpoint, [redirect_uri], client_name, scopes=scopes, http=self.http
            )
            client_id = reg.get("client_id")
            client_secret = reg.get("client_secret")
            if not client_id:
                raise IntegrationAuthError(
                    f"Server advertised a registration_endpoint ({reg_endpoint}) but "
                    "DCR returned no client_id. The SMART server may be misconfigured."
                )
        state = generate_state()
        verifier, challenge, _ = generate_pkce()
        payload: Dict[str, Any] = {
            "code_verifier": verifier,
            "code_challenge": challenge,
            "fhir_base_url": fhir_base_url.rstrip("/"),
            "authorization_endpoint": config["authorization_endpoint"],
            "token_endpoint": config["token_endpoint"],
            "revocation_endpoint": config.get("revocation_endpoint"),
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "scope": scopes,
        }
        if extra_state:
            payload.update(extra_state)
        await self.states.issue(state, payload)
        authorize_url = build_authorize_url(
            config["authorization_endpoint"],
            client_id or "",
            redirect_uri,
            state,
            challenge,
            scope=scopes,
            aud=fhir_base_url.rstrip("/"),
        )
        return authorize_url, state

    async def complete_connect(
        self, integration: UserIntegration, pending: Dict[str, Any], code: str
    ) -> Dict[str, Any]:
        """Callback handler: exchange code for tokens and persist them.

        ``pending`` is the already-consumed state payload (the platform route
        consumes it once via :meth:`OAuthStateStore.consume` and passes it
        through). It must contain the PKCE verifier + SMART endpoints +
        ``client_id`` written by :meth:`begin_connect`. Raises
        :class:`IntegrationAuthError` if the payload is empty/malformed.
        """
        if not pending or "code_verifier" not in pending:
            raise IntegrationAuthError("Unknown or expired OAuth state.")
        token = await exchange_code(
            pending["token_endpoint"],
            code,
            pending["code_verifier"],
            pending["redirect_uri"],
            pending["client_id"],
            client_secret=pending.get("client_secret"),
            http=self.http,
        )
        token["token_endpoint"] = pending["token_endpoint"]
        token["client_id"] = pending["client_id"]
        if pending.get("client_secret"):
            token["client_secret"] = pending["client_secret"]
        token["fhir_base_url"] = pending.get("fhir_base_url")
        if pending.get("revocation_endpoint"):
            token["revocation_endpoint"] = pending["revocation_endpoint"]
        self.tokens.store(integration, token)
        return token

    async def get_live_token(self, integration: UserIntegration) -> str:
        """Refresh-on-use: a valid access token for the integration.

        Reads the SMART endpoints/client_id from ``user_config["_oauth"]``
        (populated by :meth:`complete_connect`-adjacent persistence in the
        endpoint). Raises :class:`IntegrationAuthError` if the token can't be
        refreshed.
        """
        oauth = self.tokens._read(integration)
        if not oauth.get("access_token"):
            raise IntegrationAuthError(f"Integration {integration.id} is not connected.")
        if not self.tokens.is_expired(integration):
            return oauth["access_token"]
        token = await refresh_token(
            oauth.get("token_endpoint"),
            oauth.get("refresh_token"),
            oauth.get("client_id"),
            client_secret=oauth.get("client_secret"),
            http=self.http,
        )
        self.tokens.store(integration, token)
        return token["access_token"]

    async def force_refresh(self, integration: UserIntegration) -> str:
        """Force a token refresh regardless of expiry (e.g. after a 401 race).

        Used by FHIR/HTTP helpers when a request fails with 401 despite
        :meth:`get_live_token` returning a token (the server revoked/rotated it
        between the expiry check and the call). Raises
        :class:`IntegrationAuthError` if there is no refresh token.
        """
        oauth = self.tokens._read(integration)
        if not oauth.get("refresh_token"):
            raise IntegrationAuthError(
                f"Integration {integration.id} has no refresh_token to force-refresh."
            )
        token = await refresh_token(
            oauth.get("token_endpoint"),
            oauth.get("refresh_token"),
            oauth.get("client_id"),
            client_secret=oauth.get("client_secret"),
            http=self.http,
        )
        self.tokens.store(integration, token)
        return token["access_token"]

    async def revoke(self, integration: UserIntegration) -> None:
        """Best-effort token revocation (RFC 7009).

        Reads the ``revocation_endpoint`` from the stored ``_oauth`` blob
        (captured during ``begin_connect`` from the SMART discovery config) and
        POSTs the ``refresh_token`` + ``access_token`` to it. Network errors
        and missing endpoints are swallowed — the integration is deleted
        regardless. This prevents stale tokens from lingering on the remote
        server after the user disconnects.
        """
        try:
            oauth = self.tokens._read(integration)
            revoke_url = oauth.get("revocation_endpoint")
            if not revoke_url:
                return  # Server didn't advertise a revocation endpoint.
            form_data: Dict[str, str] = {}
            if oauth.get("refresh_token"):
                form_data["token"] = oauth["refresh_token"]
                form_data["token_type_hint"] = "refresh_token"
            elif oauth.get("access_token"):
                form_data["token"] = oauth["access_token"]
                form_data["token_type_hint"] = "access_token"
            else:
                return  # No tokens to revoke.
            if oauth.get("client_id"):
                form_data["client_id"] = oauth["client_id"]
            if oauth.get("client_secret"):
                form_data["client_secret"] = oauth["client_secret"]
            resp = await self.http.post(revoke_url, data=form_data)
            if resp.status_code >= 400:
                logger.warning(
                    "Token revocation for %s returned HTTP %s — tokens may "
                    "remain live on the remote server.",
                    integration.id, resp.status_code,
                )
            else:
                logger.info("Token revocation successful for %s", integration.id)
        except Exception as e:
            logger.warning(
                "Token revocation failed for %s (best-effort — integration "
                "will still be deleted): %s",
                integration.id, e,
            )
