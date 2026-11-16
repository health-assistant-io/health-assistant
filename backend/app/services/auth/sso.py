"""Per-tenant OIDC SSO — the link-only flow (ADR 0054 D2, program Phase 12B).

One tenant, one provider binding (:class:`~app.models.tenant_sso_config.
TenantSSOConfig`): discovery → authorization-code + PKCE (S256) → verified
ID token → **link-only** resolution against
:class:`~app.models.user_identity.UserIdentity`.

The law this module enforces (D2, and identity-auth §14 as extended):

- ``email_verified`` is required — an unverified IdP address never signs in
  or links (typed refusal, not a warning).
- ``(issuer, subject)`` is the only identity key. An existing link signs its
  user in; there is no merge path in either direction.
- A verified address matching an existing local account that has **no link
  yet** answers the typed conflict ``sso_link_required`` (409) and mints a
  short-lived, single-use **link intent**: the local user confirms the link
  while signed in (:func:`confirm_link`). Accounts are never merged, and a
  link is never created by the callback itself in that case.
- JIT provisioning is capped twice: the address's domain must be in the
  config's ``allowed_domains`` (case-blind) and ``jit_enabled`` must be on.
  The provisioned role is the first group the ``group_role_map`` names,
  else ``default_role`` — both validated to the non-SYSTEM_ADMIN vocabulary
  (SSO is never a path to SYSTEM_ADMIN).
- Group→role re-mapping runs on **every** login of a linked user: the first
  mapped group wins, otherwise the role falls back to ``default_role``;
  every actual change is audited (``sso_role_change``).
- Internal sessions are minted by the product
  (:func:`app.services.auth.auth_session_service.issue_session`,
  ``auth_mode="oidc"``) — the IdP never signs an internal API token (§8;
  RS256/JWKS verifies only the external ID token).

Failure posture: every refusal is typed (``SSOError`` with a stable code),
audited, and leaves the local password path untouched — a disabled,
misconfigured, or unreachable IdP can only fail the SSO flow, never the
instance (break-glass local admin retained).

Retention: no state lives here — flow state is the token store's
(single-use, TTL-bounded); links are rows.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from nx_auth.secrets import redact_secret
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenant_context, token_store
from app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.security import AUTH_MODE_OIDC
from app.models.enums import Role
from app.models.tenant_model import TenantModel
from app.models.tenant_sso_config import TenantSSOConfig
from app.models.user_identity import UserIdentity
from app.models.user_model import UserModel
from app.services.audit.audit_service import OUTCOME_DENIED, log_audit_action
from app.services.auth import sso_secrets
from app.services.auth.amr import SECOND_FACTOR_AMR, amr_evidences_second_factor
from app.services.auth.auth_session_service import issue_session
from app.services.tenancy.user_service import get_user_by_email

logger = logging.getLogger(__name__)

#: Flow-state TTL in the token store (state / nonce / PKCE verifier travel
#: together in one payload under the state key).
SSO_STATE_TTL_SECONDS = 10 * 60

#: One-time link-intent TTL — the local user confirms while signed in.
SSO_LINK_INTENT_TTL_SECONDS = 10 * 60

#: Discovery / JWKS cache (per process). Short TTL: a rotated IdP key heals
#: itself within minutes without a manual cache clear.
_DISCOVERY_TTL_SECONDS = 600

_DISCOVERY_CACHE: dict[str, tuple[float, dict]] = {}
_JWKS_CACHE: dict[str, tuple[float, dict]] = {}

DEFAULT_SCOPES = ["openid", "email", "profile"]

#: The ID-token claim the group→role map reads (kept a module constant until
#: a tenant needs it configurable — then it moves to the config row).
GROUPS_CLAIM = "groups"


class SSOError(Exception):
    """A typed SSO refusal — ``code`` is the stable, machine-readable key.

    Service-layer (no HTTP coupling); the endpoint maps ``status_code`` to
    the response and never renders a raw provider error (messages are
    redacted upstream with the resolved client secret — see
    :func:`nx_auth.secrets.redact_secret`).
    """

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LinkRequiredConflict(ConflictError):
    """The typed 409 carrying the one-time link intent (D2 — no merge).

    Raised when a verified IdP address matches an existing local account
    with no link yet: the callback never links by itself — the local user
    confirms while signed in (:func:`confirm_link`) — and accounts are
    never merged.
    """

    def __init__(self, link_token: str):
        super().__init__(
            "This email already belongs to a local account. Sign in with your "
            "password and confirm the link to use SSO (accounts are never merged)."
        )
        self.code = "sso_link_required"
        self.link_token = link_token


def _now() -> datetime:
    return datetime.now(UTC)


# --- outbound HTTP (the mock-IdP seam; tests patch these three) --------------


async def _fetch_json(url: str) -> dict:
    """One bounded GET returning JSON (discovery, JWKS)."""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def _exchange_code(
    token_endpoint: str,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    code_verifier: str,
) -> dict:
    """The authorization-code token exchange (form-encoded POST)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return response.json()


async def _discovery(issuer: str, *, refresh: bool = False) -> dict:
    """The provider's OpenID configuration, cached per issuer (short TTL)."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    cached = _DISCOVERY_CACHE.get(url)
    if not refresh and cached and cached[0] > time.monotonic():
        return cached[1]
    doc = await _fetch_json(url)
    _DISCOVERY_CACHE[url] = (time.monotonic() + _DISCOVERY_TTL_SECONDS, doc)
    return doc


async def _jwks(jwks_uri: str, *, refresh: bool = False) -> dict:
    cached = _JWKS_CACHE.get(jwks_uri)
    if not refresh and cached and cached[0] > time.monotonic():
        return cached[1]
    doc = await _fetch_json(jwks_uri)
    _JWKS_CACHE[jwks_uri] = (time.monotonic() + _DISCOVERY_TTL_SECONDS, doc)
    return doc


def clear_provider_cache() -> None:
    """Drop the discovery/JWKS caches (test seam; test-connection refresh)."""
    _DISCOVERY_CACHE.clear()
    _JWKS_CACHE.clear()


# --- PKCE (RFC 7636 S256) ----------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    """``(verifier, code_challenge)`` — S256; the challenge rides the
    authorization URL, the verifier stays server-side in the state payload."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


# --- config resolution -------------------------------------------------------


def normalize_domain(domain: str) -> str:
    """Lowercased, stripped email domain (the ``allowed_domains`` shape)."""
    return domain.strip().lower().rstrip(".")


def normalize_allowed_domains(domains: list[str] | None) -> list[str]:
    """Normalized, de-duplicated, bounded domain list (service validation)."""
    out: list[str] = []
    for domain in domains or []:
        normalized = normalize_domain(str(domain))
        if normalized and normalized not in out:
            out.append(normalized)
    return out[:64]


def validate_group_role_map(group_role_map: dict | None) -> dict[str, str]:
    """Validated ``group → role`` map (SSO vocabulary — never SYSTEM_ADMIN)."""
    from app.models.tenant_sso_config import SSO_ASSIGNABLE_ROLES

    if not group_role_map:
        return {}
    if not isinstance(group_role_map, dict):
        raise ValidationError("group_role_map must be an object of group → role")
    if len(group_role_map) > 128:
        raise ValidationError("group_role_map holds at most 128 entries")
    validated: dict[str, str] = {}
    for group, role in group_role_map.items():
        name = str(group).strip()
        value = str(role).strip().upper()
        if not name:
            raise ValidationError("group_role_map keys must be non-empty group names")
        if value not in SSO_ASSIGNABLE_ROLES:
            raise ValidationError(
                f"group_role_map role {value!r} is not assignable via SSO "
                f"(allowed: {', '.join(SSO_ASSIGNABLE_ROLES)})"
            )
        validated[name] = value
    return validated


async def get_tenant_config(db: AsyncSession, tenant_id, config_id) -> TenantSSOConfig | None:
    """One config row by (tenant, id)."""
    result = await db.execute(
        select(TenantSSOConfig).where(
            TenantSSOConfig.tenant_id == tenant_id,
            TenantSSOConfig.id == config_id,
        )
    )
    return result.scalar_one_or_none()


#: Transaction-scoped advisory-lock key serializing the domain-claim check.
#: Without it two concurrent enables (create or flip) can both pass the scan
#: before either commits and end up claiming the same domain. The scan and
#: the lock are cheap; the key is arbitrary but stable ("SSODOMAI" as int64).
_DOMAIN_CLAIM_LOCK = 0x53534F444F4D4149


async def _claimed_domains(db: AsyncSession, *, exclude_id=None) -> dict[str, TenantSSOConfig]:
    """Normalized domain → the enabled config that claims it, **across tenants**.

    An **enabled** config claims its domains for the login-page resolution
    (``find_enabled_config_for_domain`` walks every enabled config), so two
    enabled configs must never claim the same domain: the second sign-in
    would be routed to whichever row happened to sort first.

    The scan is system-side on purpose: row-level security scopes the
    caller's session to its own tenant, so a tenant-admin check that stayed
    on that wall would let a second tenant claim another tenant's domain.
    The admin request's transaction is already open (and its GUCs planted),
    hence the explicit re-plant inside and after the scope.
    """
    rows: list[TenantSSOConfig] = []
    try:
        with tenant_context.system_scope("auth:sso-domain-claim"):
            await tenant_context.plant_on_session(db)
            result = await db.execute(
                select(TenantSSOConfig).where(TenantSSOConfig.enabled.is_(True))
            )
            rows = list(result.scalars().all())
    finally:
        try:
            await tenant_context.plant_on_session(db)
        except Exception:  # an aborted transaction rolls back; nothing leaks
            logger.warning("sso: could not restore the RLS context after the domain scan")
    claimed: dict[str, TenantSSOConfig] = {}
    for config in rows:
        if exclude_id is not None and str(config.id) == str(exclude_id):
            continue
        for domain in normalize_allowed_domains(config.allowed_domains or []):
            claimed.setdefault(domain, config)
    return claimed


async def _assert_domain_claims_available(
    db: AsyncSession,
    *,
    domains: list[str],
    enabled: bool,
    exclude_id=None,
) -> None:
    """Refuse a domain already claimed by another enabled configuration.

    The check runs under a transaction-scoped advisory lock so two
    concurrent enables cannot both pass the scan before either commits
    (the read-then-write TOCTOU); the lock releases at commit/rollback.
    """
    if not enabled or not domains:
        return
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _DOMAIN_CLAIM_LOCK})
    claimed = await _claimed_domains(db, exclude_id=exclude_id)
    conflicts = sorted({domain for domain in domains if domain in claimed})
    if conflicts:
        raise ConflictError(
            "An enabled SSO configuration already claims the email domain(s): "
            + ", ".join(conflicts)
            + ". A domain can route to one tenant only."
        )


async def find_enabled_config_for_tenant(
    db: AsyncSession, tenant_slug: str
) -> TenantSSOConfig | None:
    """The first enabled config of an active tenant (the explicit-slug path)."""
    result = await db.execute(
        select(TenantSSOConfig)
        .join(TenantModel, TenantModel.id == TenantSSOConfig.tenant_id)
        .where(
            TenantModel.slug == tenant_slug,
            TenantSSOConfig.enabled.is_(True),
            TenantModel.is_active.is_(True),
        )
        .order_by(TenantSSOConfig.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_enabled_config_for_domain(db: AsyncSession, email: str) -> TenantSSOConfig | None:
    """Tenant resolution by email domain (the login-page entry point).

    Pre-auth bootstrap lookup — runs system-side (ADR 0054 Phase 5). Only
    ``enabled`` configs of ``active`` tenants resolve; a disabled config is
    indistinguishable from no config (the caller answers ``sso_unavailable``
    and the local login stays the only door).
    """
    domain = email.rsplit("@", 1)[1] if "@" in (email or "") else ""
    if not domain:
        return None
    lowered = normalize_domain(domain)
    result = await db.execute(
        select(TenantSSOConfig)
        .join(TenantModel, TenantModel.id == TenantSSOConfig.tenant_id)
        .where(
            TenantSSOConfig.enabled.is_(True),
            TenantModel.is_active.is_(True),
        )
        .order_by(TenantSSOConfig.created_at.asc())
    )
    for config in result.scalars().all():
        if lowered in {normalize_domain(d) for d in (config.allowed_domains or [])}:
            return config
    return None


# --- begin (authorization redirect) ------------------------------------------


@dataclass
class BeginResult:
    """What ``begin_sso`` hands the endpoint: the redirect and its binding."""

    config: TenantSSOConfig
    authorization_url: str
    state: str


async def begin_sso(
    db: AsyncSession,
    config: TenantSSOConfig,
    *,
    redirect_uri: str,
) -> BeginResult:
    """Build the provider's authorization URL (code + PKCE + state + nonce).

    The flow binding (nonce, PKCE verifier, tenant/config) travels under the
    single-use ``state`` key in the token store — the callback consumes it
    atomically, so a replayed state finds nothing (fail-closed).
    """
    if not config.enabled:
        raise SSOError("sso_unavailable", "SSO is not enabled for this tenant", 404)
    client_secret = await sso_secrets.resolve_secret_slot_async(config.client_secret_ref)
    if not client_secret:
        raise SSOError(
            "sso_client_secret_missing",
            "The SSO client secret slot is not provisioned on this host",
            503,
        )
    try:
        discovery = await _discovery(config.issuer)
    except Exception as exc:
        raise SSOError(
            "sso_provider_unavailable",
            "The identity provider could not be reached",
            503,
        ) from exc
    authorization_endpoint = discovery.get("authorization_endpoint")
    if not authorization_endpoint:
        raise SSOError("sso_provider_misconfigured", "Provider discovery is incomplete", 503)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    await token_store.register_sso_state(
        state,
        json.dumps(
            {
                "nonce": nonce,
                "verifier": verifier,
                "tenant_id": str(config.tenant_id),
                "config_id": str(config.id),
                "issuer": config.issuer,
            }
        ),
        ttl_seconds=SSO_STATE_TTL_SECONDS,
    )

    scopes = [s for s in (config.scopes or []) if s] or list(DEFAULT_SCOPES)
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    separator = "&" if "?" in authorization_endpoint else "?"
    return BeginResult(
        config=config,
        authorization_url=authorization_endpoint + separator + urlencode(params),
        state=state,
    )


# --- callback (ID token → link / JIT) ----------------------------------------


@dataclass
class IdentityClaims:
    """The verified ID-token facts the flow acts on.

    ``amr`` / ``acr`` / ``auth_time`` are the provider's authentication
    context (RFC 8176 / OIDC core §2): they decide whether the IdP already
    asserted a second factor for this sign-in (program 12C1 — an SSO login
    must never bypass the tenant MFA policy or an enrolled local secret).
    A provider that asserts nothing leaves them empty, and the flow
    answers the local ``mfa_required`` challenge.
    """

    subject: str
    email: str
    email_verified: bool
    groups: list[str] = field(default_factory=list)
    full_name: str = ""
    amr: tuple[str, ...] = ()
    acr: str | None = None
    auth_time: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def second_factor_asserted(claims: IdentityClaims) -> bool:
    """Whether the IdP asserts a second factor for this authentication.

    Delegates to :func:`app.services.auth.amr.amr_evidences_second_factor`
    (the one vocabulary the step-up service also reads): an ``amr`` entry
    from the RFC 8176 second-factor set, or an ``acr`` naming multi-factor /
    a numeric level ≥ 2 (Keycloak/Entra/Authentik style). Absence is
    **not** evidence: no assertion means the local MFA challenge applies.
    """
    return amr_evidences_second_factor(claims.amr, claims.acr)


async def _verify_id_token(
    id_token: str,
    *,
    config: TenantSSOConfig,
    expected_nonce: str,
) -> IdentityClaims:
    """RS256/JWKS verification of the external ID token (identity-auth §8).

    Signature (kid → JWKS), ``iss``, ``aud`` (client_id), ``exp`` (PyJWT)
    and ``nonce`` (state payload) all must hold. ``email_verified`` is
    required — the typed refusal covers the "unverified email" case.
    """
    try:
        discovery = await _discovery(config.issuer)
        jwks_uri = discovery["jwks_uri"]
    except SSOError:
        raise
    except Exception as exc:
        raise SSOError(
            "sso_provider_misconfigured", "Provider discovery is incomplete", 503
        ) from exc
    try:
        header = jwt.get_unverified_header(id_token)
        keys = (await _jwks(jwks_uri)).get("keys", [])
        jwk = next((k for k in keys if k.get("kid") == header.get("kid")), None)
        if jwk is None:
            jwks_doc = await _jwks(jwks_uri, refresh=True)
            keys = jwks_doc.get("keys", [])
            jwk = next((k for k in keys if k.get("kid") == header.get("kid")), None)
        if jwk is None:
            raise SSOError("sso_token_invalid", "ID token key not recognized", 401)
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=config.client_id,
            issuer=config.issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise SSOError("sso_token_invalid", "ID token failed verification", 401) from exc
    except SSOError:
        raise
    except Exception as exc:
        raise SSOError(
            "sso_provider_unavailable", "The identity provider could not be reached", 503
        ) from exc

    if claims.get("nonce") != expected_nonce:
        raise SSOError("sso_nonce_mismatch", "ID token nonce does not match the flow", 401)
    subject = claims.get("sub")
    email = claims.get("email")
    if not subject or not email:
        raise SSOError(
            "sso_claims_incomplete",
            "The ID token does not carry the required subject and email claims",
            401,
        )
    verified = claims.get("email_verified")
    if verified is not True:
        raise SSOError(
            "sso_email_unverified",
            "The identity provider has not verified this email address",
            403,
        )
    groups = claims.get(GROUPS_CLAIM) or []
    if isinstance(groups, str):
        groups = [g for g in groups.split() if g]
    amr = claims.get("amr") or []
    if isinstance(amr, str):
        amr = amr.split()
    acr = claims.get("acr")
    auth_time = claims.get("auth_time")
    try:
        auth_time_value = int(auth_time) if auth_time is not None else None
    except (TypeError, ValueError):
        auth_time_value = None
    return IdentityClaims(
        subject=str(subject),
        email=str(email).strip().lower(),
        email_verified=True,
        groups=[str(g) for g in groups],
        full_name=str(claims.get("name") or "").strip()[:200],
        amr=tuple(str(value) for value in amr),
        acr=str(acr) if acr is not None else None,
        auth_time=auth_time_value,
        raw=claims,
    )


@dataclass
class CallbackResult:
    """One completed callback: the signed-in user (and how it got there)."""

    user: UserModel
    identity: UserIdentity
    issued: Any = None
    provisioning: str = "link"
    role_changed_to: str | None = None
    link_token: str | None = None
    claims: IdentityClaims | None = None


async def _audit_sso(
    action: str,
    *,
    tenant_id,
    user_id=None,
    outcome: str = "ok",
    detail: dict | None = None,
    session: AsyncSession | None = None,
) -> None:
    """One fail-closed audit row for an SSO decision (§17).

    Success-path callers pass ``session`` so the row commits **with** the
    action (one chain append per request — a second, lane-side append of the
    same chain would deadlock on the head-row lock). Denied outcomes stay on
    the dedicated lane: they must survive the refusal's rollback.
    """
    await log_audit_action(
        required=True,
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        resource_type="auth",
        outcome=outcome,
        new_value=detail or {},
        session=session,
    )


def _cap_role(role: str | None) -> str:
    """Clamp a computed SSO role into the assignable vocabulary.

    Config validation already refuses SYSTEM_ADMIN; the clamp is defense in
    depth for rows written before a validation change (the invariant is the
    vocabulary, never trust in one caller).
    """
    from app.models.tenant_sso_config import SSO_ASSIGNABLE_ROLES

    value = str(role or Role.USER.value).strip().upper()
    return value if value in SSO_ASSIGNABLE_ROLES else Role.USER.value


def _computed_role(config: TenantSSOConfig, claims: IdentityClaims) -> str:
    """First mapped group wins; otherwise the config's ``default_role``."""
    mapped = config.role_for_groups(claims.groups)
    return _cap_role(mapped or config.default_role)


async def _load_user_in_session(db: AsyncSession, user_id, tenant_id) -> UserModel | None:
    """The linked user, loaded on the caller's session (mutable there)."""
    result = await db.execute(
        select(UserModel).where(
            UserModel.id == user_id,
            UserModel.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


async def _apply_role_on_login(
    db: AsyncSession,
    config: TenantSSOConfig,
    user: UserModel,
    claims: IdentityClaims,
) -> str | None:
    """Group→role re-mapping on login. Returns the new role when it changed.

    The computed role is capped to the SSO vocabulary; an actual change is
    written and audited (``sso_role_change``) in the caller's transaction.

    Any actual change also bumps ``users.token_version`` — the same
    immediate-effect rule the admin and SCIM surfaces apply (ADR 0054
    Phase 3 / identity-auth §8): a demoted member's outstanding access
    tokens die now, not at their next refresh. The caller revokes the Redis
    hot layer once the transaction commits (the new session is minted after
    that, so it is unaffected).
    """
    computed = _computed_role(config, claims)
    current = getattr(user.role, "value", user.role)
    if computed == current:
        return None
    if current == Role.SYSTEM_ADMIN.value:
        return None
    user.role = Role(computed)
    user.token_version = int(getattr(user, "token_version", 1) or 1) + 1
    db.add(user)
    await db.flush()
    await log_audit_action(
        required=True,
        tenant_id=user.tenant_id,
        user_id=user.id,
        action="sso_role_change",
        resource_type="user",
        resource_id=user.id,
        session=db,
        old_value={"role": current},  # gate-allow: *, audit diff
        new_value={"role": computed, "issuer": config.issuer, "token_version_bumped": True},
    )
    return computed


async def callback_sso(
    db: AsyncSession,
    *,
    code: str | None,
    state: str | None,
    redirect_uri: str,
) -> CallbackResult:
    """Consume the callback: verify, resolve the link, JIT or refuse.

    Pre-auth bootstrap path — the whole resolution runs system-side (the
    identity lookup spans tenants; the user/identity writes are the auth
    layer's provisioning writes). A refused callback never mints a session,
    and the local password login keeps working regardless of the outcome.
    """
    if not state or not code:
        raise SSOError("sso_state_invalid", "The SSO flow state is missing or invalid", 400)
    raw_state = await token_store.consume_sso_state(state)
    if not raw_state:
        raise SSOError(
            "sso_state_invalid",
            "The SSO flow state is unknown, expired, or already used",
            400,
        )
    try:
        flow = json.loads(raw_state)
    except ValueError as exc:
        raise SSOError("sso_state_invalid", "The SSO flow state is unreadable", 400) from exc

    with tenant_context.system_scope("auth:sso-callback"):
        config = await get_tenant_config(db, flow.get("tenant_id"), flow.get("config_id"))
        if config is None or not config.enabled or config.issuer != flow.get("issuer"):
            raise SSOError(
                "sso_config_unavailable", "The SSO configuration is no longer active", 404
            )
        client_secret = await sso_secrets.resolve_secret_slot_async(config.client_secret_ref)
        if not client_secret:
            raise SSOError(
                "sso_client_secret_missing",
                "The SSO client secret slot is not provisioned on this host",
                503,
            )

        try:
            discovery = await _discovery(config.issuer)
            token_endpoint = discovery["token_endpoint"]
        except SSOError:
            raise
        except Exception as exc:
            raise SSOError(
                "sso_provider_misconfigured", "Provider discovery is incomplete", 503
            ) from exc
        try:
            tokens = await _exchange_code(
                token_endpoint,
                code=code,
                redirect_uri=redirect_uri,
                client_id=config.client_id,
                client_secret=client_secret,
                code_verifier=flow["verifier"],
            )
        except Exception as exc:
            raise SSOError(
                "sso_provider_unavailable",
                "The identity provider rejected the authorization code",
                503,
            ) from exc
        id_token = tokens.get("id_token")
        if not id_token:
            raise SSOError("sso_token_missing", "The provider did not return an ID token", 503)

        claims = await _verify_id_token(id_token, config=config, expected_nonce=flow.get("nonce"))
        result = await _resolve_identity(db, config, claims)
        result.claims = claims
        return result


async def _resolve_identity(
    db: AsyncSession,
    config: TenantSSOConfig,
    claims: IdentityClaims,
) -> CallbackResult:
    """Link-only resolution: existing link → sign in; else JIT or 409 intent."""
    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.issuer == config.issuer,
            UserIdentity.subject == claims.subject,
        )
    )
    identity = result.scalar_one_or_none()
    if identity is not None:
        return await _sign_in_linked(db, config, identity, claims)

    existing = await get_user_by_email(claims.email)
    if existing is not None and str(existing.tenant_id) == str(config.tenant_id):
        link_token = await _mint_link_intent(config, claims)
        await _audit_sso(
            "auth.sso_link_required",
            tenant_id=config.tenant_id,
            user_id=existing.id,
            outcome=OUTCOME_DENIED,
            detail={
                "code": "sso_link_required",
                "issuer": config.issuer,
                "subject": claims.subject,
            },
        )
        raise LinkRequiredConflict(link_token)
    if existing is not None:
        await _audit_sso(
            "auth.sso_login",
            tenant_id=config.tenant_id,
            outcome=OUTCOME_DENIED,
            detail={"code": "sso_email_conflict", "issuer": config.issuer},
        )
        raise SSOError(
            "sso_email_conflict",
            "This email address already belongs to an account of another tenant",
            409,
        )

    if not config.domain_allowed(claims.email):
        await _audit_sso(
            "auth.sso_login",
            tenant_id=config.tenant_id,
            outcome=OUTCOME_DENIED,
            detail={"code": "sso_domain_not_allowed", "issuer": config.issuer},
        )
        raise SSOError(
            "sso_domain_not_allowed",
            "This email domain is not allowed to sign in here",
            403,
        )
    if not config.jit_enabled:
        await _audit_sso(
            "auth.sso_login",
            tenant_id=config.tenant_id,
            outcome=OUTCOME_DENIED,
            detail={"code": "sso_jit_disabled", "issuer": config.issuer},
        )
        raise SSOError(
            "sso_jit_disabled",
            "Self-service sign-up is disabled for this tenant; ask an administrator",
            403,
        )
    return await _provision_jit(db, config, claims)


async def _mint_link_intent(config: TenantSSOConfig, claims: IdentityClaims) -> str:
    """A single-use, TTL-bounded intent the local user confirms in-app."""
    link_token = secrets.token_urlsafe(32)
    await token_store.register_sso_state(
        f"link:{link_token}",
        json.dumps(
            {
                "kind": "sso_link_intent",
                "issuer": config.issuer,
                "subject": claims.subject,
                "email": claims.email,
                "tenant_id": str(config.tenant_id),
                "config_id": str(config.id),
            }
        ),
        ttl_seconds=SSO_LINK_INTENT_TTL_SECONDS,
    )
    return link_token


async def _sign_in_linked(
    db: AsyncSession,
    config: TenantSSOConfig,
    identity: UserIdentity,
    claims: IdentityClaims,
) -> CallbackResult:
    """Existing link: verify the account, re-map the role, mint the session."""
    user = await _load_user_in_session(db, identity.user_id, identity.tenant_id)
    if user is None:
        raise SSOError("sso_link_broken", "The linked account no longer exists in this tenant", 401)
    if not user.is_active:
        raise SSOError("sso_account_disabled", "This account has been deactivated", 401)
    role_changed = await _apply_role_on_login(db, config, user, claims)
    await _audit_sso(
        "auth.sso_login",
        tenant_id=user.tenant_id,
        user_id=user.id,
        detail={"issuer": config.issuer, "provisioning": "link"},
        session=db,
    )
    return CallbackResult(
        user=user,
        identity=identity,
        provisioning="link",
        role_changed_to=role_changed,
    )


async def _provision_jit(
    db: AsyncSession,
    config: TenantSSOConfig,
    claims: IdentityClaims,
) -> CallbackResult:
    """First sign-in with JIT on: create the account + the link, one role cap."""
    role_value = _computed_role(config, claims)
    from app.services.tenancy.user_service import create_user as service_create_user

    user = await service_create_user(
        email=claims.email,
        password_hash=None,
        tenant_id=config.tenant_id,
        role=role_value,
        full_name=claims.full_name,
        session=db,
    )
    identity = await create_identity(
        db,
        tenant_id=config.tenant_id,
        user_id=user.id,
        issuer=config.issuer,
        subject=claims.subject,
        email=claims.email,
    )
    await _audit_sso(
        "auth.sso_jit_provisioned",
        tenant_id=config.tenant_id,
        user_id=user.id,
        detail={"issuer": config.issuer, "role": role_value, "groups": claims.groups[:16]},
        session=db,
    )
    return CallbackResult(user=user, identity=identity, provisioning="jit")


async def create_identity(
    db: AsyncSession,
    *,
    tenant_id,
    user_id,
    issuer: str,
    subject: str,
    email: str,
    email_verified: bool = True,
) -> UserIdentity:
    """Insert one link row (the writers keep the verified-email law true)."""
    if not email_verified:
        raise ValidationError("Only a verified email address can be linked")
    identity = UserIdentity(
        tenant_id=tenant_id,
        user_id=user_id,
        issuer=issuer.strip(),
        subject=subject.strip(),
        email=str(email).strip().lower(),
        email_verified=True,
    )
    db.add(identity)
    try:
        await db.flush()
    except Exception as exc:
        raise ConflictError(
            "This identity is already linked to another account (link-only, no merge)."
        ) from exc
    return identity


async def confirm_link(db: AsyncSession, *, actor, link_token: str) -> UserIdentity:
    """The authenticated link flow: the local user confirms while signed in.

    The actor must be signed in, in the intent's tenant, and own the intent's
    email address — a link is a *person's* decision, recorded under their
    account (``auth.sso_link``, fail-closed), never a callback side effect.
    The intent is consumed single-use; the identity insert enforces the
    global ``(issuer, subject)`` uniqueness (no second link, no merge).
    """
    raw = await token_store.peek_sso_state(f"link:{link_token}")
    if not raw:
        raise ValidationError("The link request is unknown, expired, or already used")
    try:
        intent = json.loads(raw)
    except ValueError as exc:
        raise ValidationError("The link request is unreadable") from exc
    if intent.get("kind") != "sso_link_intent":
        raise ValidationError("The link request is unknown, expired, or already used")

    tenant_id = str(intent["tenant_id"])
    if str(actor.tenant_id) != tenant_id:
        raise AuthorizationError("The link request belongs to a different tenant")
    actor_email = str(getattr(actor, "email", "") or "").strip().lower()
    if actor_email != str(intent["email"]).strip().lower():
        raise AuthorizationError(
            "Sign in with the local account that owns this email address to confirm the link"
        )

    existing = await get_user_by_email(actor_email)
    if existing is None or str(existing.tenant_id) != tenant_id:
        raise AuthorizationError("The local account for this email no longer exists")

    identity = await create_identity(
        db,
        tenant_id=tenant_id,
        user_id=existing.id,
        issuer=intent["issuer"],
        subject=intent["subject"],
        email=actor_email,
    )
    await token_store.drop_sso_state(f"link:{link_token}")
    await _audit_sso(
        "auth.sso_link",
        tenant_id=tenant_id,
        user_id=existing.id,
        detail={"issuer": intent["issuer"], "subject": intent["subject"]},
        session=db,
    )
    return identity


# --- admin config CRUD (the tenant-admin surface; audited) -------------------


async def list_sso_configs(db: AsyncSession, *, tenant_id) -> list[TenantSSOConfig]:
    """The tenant's bindings (oldest first; secrets stay slot references)."""
    result = await db.execute(
        select(TenantSSOConfig)
        .where(TenantSSOConfig.tenant_id == tenant_id)
        .order_by(TenantSSOConfig.created_at.asc())
    )
    return list(result.scalars().all())


async def create_sso_config(
    db: AsyncSession,
    *,
    tenant_id,
    actor_id,
    issuer: str,
    client_id: str,
    client_secret_ref: str,
    allowed_domains: list[str],
    default_role,
    group_role_map: dict,
    jit_enabled: bool = True,
    enabled: bool = False,
    scopes: list[str] | None = None,
) -> TenantSSOConfig:
    """Insert one binding (unique per (tenant, issuer); audited)."""
    duplicate = await db.execute(
        select(TenantSSOConfig).where(
            TenantSSOConfig.tenant_id == tenant_id,
            TenantSSOConfig.issuer == issuer.strip(),
        )
    )
    if duplicate.scalar_one_or_none() is not None:
        raise ConflictError("An SSO configuration for this issuer already exists")
    if not sso_secrets.is_valid_slot(client_secret_ref):
        raise ValidationError(
            "client_secret_ref must name a keyring/env slot "
            "(letters, digits and . _ - / only), never carry a secret value"
        )
    resolved_domains = normalize_allowed_domains(allowed_domains)
    await _assert_domain_claims_available(db, domains=resolved_domains, enabled=bool(enabled))
    config = TenantSSOConfig(
        tenant_id=tenant_id,
        issuer=issuer.strip(),
        client_id=client_id.strip(),
        client_secret_ref=client_secret_ref.strip(),
        allowed_domains=resolved_domains,
        default_role=Role(str(default_role).strip().upper()),
        group_role_map=validate_group_role_map(group_role_map),
        jit_enabled=bool(jit_enabled),
        enabled=bool(enabled),
        scopes=list(scopes or []),
        created_by=actor_id,
    )
    db.add(config)
    await db.flush()
    await db.refresh(config)
    await _audit_config_action(
        "sso_config_created",
        config=config,
        actor_id=actor_id,
        detail={"issuer": config.issuer, "enabled": config.enabled},
    )
    return config


async def update_sso_config(
    db: AsyncSession,
    *,
    tenant_id,
    config_id,
    actor_id,
    changes: dict,
) -> TenantSSOConfig:
    """Partial update (audited with the changed keys — never secret values)."""
    config = await get_tenant_config(db, tenant_id, config_id)
    if config is None:
        raise NotFoundError("SSO configuration not found")
    if "issuer" in changes and changes["issuer"] is not None:
        candidate = str(changes["issuer"]).strip()
        if candidate != config.issuer and config.enabled:
            raise ConflictError(
                "The issuer cannot change while the configuration is enabled: "
                "existing identity links are keyed on (issuer, subject) and would "
                "stop resolving. Disable the configuration first."
            )
    if "client_secret_ref" in changes and changes["client_secret_ref"] is not None:
        if not sso_secrets.is_valid_slot(changes["client_secret_ref"]):
            raise ValidationError(
                "client_secret_ref must name a keyring/env slot "
                "(letters, digits and . _ - / only), never carry a secret value"
            )
        changes["client_secret_ref"] = str(changes["client_secret_ref"]).strip()
    before = {key: config.to_dict().get(key) for key in changes}
    if "allowed_domains" in changes and changes["allowed_domains"] is not None:
        changes["allowed_domains"] = normalize_allowed_domains(changes["allowed_domains"])
    if "group_role_map" in changes and changes["group_role_map"] is not None:
        changes["group_role_map"] = validate_group_role_map(changes["group_role_map"])
    if "default_role" in changes and changes["default_role"] is not None:
        changes["default_role"] = Role(str(changes["default_role"]).strip().upper())
    await _assert_domain_claims_available(
        db,
        domains=normalize_allowed_domains(
            changes.get("allowed_domains")
            if changes.get("allowed_domains") is not None
            else (config.allowed_domains or [])
        ),
        enabled=bool(changes.get("enabled", config.enabled)),
        exclude_id=config.id,
    )
    for key, value in changes.items():
        if not hasattr(config, key):
            raise ValidationError(f"Unknown SSO configuration field {key!r}")
        setattr(config, key, value)
    config.updated_by = actor_id
    db.add(config)
    await db.flush()
    await db.refresh(config)
    await _audit_config_action(
        "sso_config_updated",
        config=config,
        actor_id=actor_id,
        detail={"fields": sorted(changes.keys()), "before": before},
    )
    return config


async def delete_sso_config(db: AsyncSession, *, tenant_id, config_id, actor_id) -> None:
    """Unbind one provider (audited; links keep their rows)."""
    config = await get_tenant_config(db, tenant_id, config_id)
    if config is None:
        raise NotFoundError("SSO configuration not found")
    detail = {"issuer": config.issuer}
    await db.delete(config)
    await db.flush()
    await _audit_config_action(
        "sso_config_deleted", config=config, actor_id=actor_id, detail=detail
    )


async def _audit_config_action(
    action: str, *, config: TenantSSOConfig, actor_id, detail: dict
) -> None:
    """One fail-closed audit row per config mutation (§17)."""
    await log_audit_action(
        required=True,
        tenant_id=config.tenant_id,
        user_id=actor_id,
        action=action,
        resource_type="tenant_sso_config",
        resource_id=config.id,
        new_value=detail,
    )


async def test_connection(config: TenantSSOConfig) -> dict:
    """Discovery + secret-slot probe for the admin surface (never echoes).

    Answers one bit per check; a failed provider fetch is a bounded,
    redacted message — no token, no discovery dump.
    """
    secret = await sso_secrets.resolve_secret_slot_async(config.client_secret_ref)
    result: dict = {
        "secret_resolved": bool(secret),
        "discovery_ok": False,
        "authorization_endpoint": None,
        "token_endpoint": None,
    }
    try:
        discovery = await _discovery(config.issuer, refresh=True)
        result["discovery_ok"] = True
        result["authorization_endpoint"] = discovery.get("authorization_endpoint")
        result["token_endpoint"] = discovery.get("token_endpoint")
    except Exception as exc:
        result["error"] = redact_secret(str(exc), secret)
    return result


async def issue_sso_session(
    user: UserModel,
    *,
    client_label: str = "sso",
    ip: str | None = None,
    user_agent: str | None = None,
    amr: tuple[str, ...] = (),
    auth_time: int | None = None,
):
    """Mint the internal session through the one issuance path (§8).

    The IdP's authentication context rides the session as ``amr`` /
    ``auth_time`` (product claims, §8 allows additions). They are evidence,
    never the authorization: the tenant MFA/IP policy was already enforced
    by the callback, and both claims are dropped at refresh so freshness
    cannot outlive the sign-in.
    """
    extra: dict = {}
    if amr:
        extra["amr"] = list(amr)
    if auth_time is not None:
        extra["auth_time"] = int(auth_time)
    return await issue_session(
        user,
        auth_mode=AUTH_MODE_OIDC,
        client_label=client_label,
        extra_claims=extra or None,
        ip=ip,
        user_agent=user_agent,
    )


async def find_user_for_tenant(db: AsyncSession, tenant_id, email: str) -> UserModel | None:
    """Tenant-scoped user lookup (SCIM/admin surfaces)."""
    result = await db.execute(
        select(UserModel).where(
            UserModel.tenant_id == tenant_id,
            UserModel.email == email.strip().lower(),
        )
    )
    return result.scalar_one_or_none()


__all__ = [
    "DEFAULT_SCOPES",
    "GROUPS_CLAIM",
    "SECOND_FACTOR_AMR",
    "SSO_STATE_TTL_SECONDS",
    "IdentityClaims",
    "LinkRequiredConflict",
    "SSOError",
    "begin_sso",
    "callback_sso",
    "clear_provider_cache",
    "confirm_link",
    "create_identity",
    "create_sso_config",
    "delete_sso_config",
    "find_enabled_config_for_domain",
    "find_enabled_config_for_tenant",
    "find_user_for_tenant",
    "get_tenant_config",
    "issue_sso_session",
    "list_sso_configs",
    "normalize_allowed_domains",
    "normalize_domain",
    "second_factor_asserted",
    "test_connection",
    "update_sso_config",
    "validate_group_role_map",
]
