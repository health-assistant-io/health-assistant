"""Token minting / verification + password + role dependencies.

Family token contract (identity-auth §8) — every kind is a JWT (HS256)
carrying ``iss`` (product slug), ``sub``, ``token_kind``, ``iat``/``exp``;
session/refresh additionally carry ``jti``, ``ver`` (``users.token_version``)
and ``auth_mode``. Kinds are mutually exclusive: verifiers reject mismatches.

Claims per kind (product truth, plan 16 H1):

===========  =========================  ===========================================
kind         sub                        extras
===========  =========================  ===========================================
session      user id                    jti, ver, auth_mode, email, user_id (mirror),
                                        tenant_id, role, fid, switched-set, …
refresh      user id                    jti, ver, auth_mode, fid (family row)
api          client id (client principal) jti, client_id, tenant_id, scope, aud,
                                        bound_patient_id
invite       invited email (or "")      jti (single-use, consumed), email,
                                        tenant_id, role
download     requesting user id (or "") jti, doc_id (presign pattern, short TTL)
mfa_challenge user id (password-      jti (consumed on the first successful
             verified, pre-session)   /auth/mfa/verify), user_id
pat          owner user id            opaque, NOT a JWT: prefix + sha256
             (resolved from          against ``api_tokens``; carries the
             ``api_tokens``)         owner's tenant/role mirror, the
                                     intersected permission set and the
                                     patient scope
===========  =========================  ===========================================

Signing keys are resolved per purpose through ``app.core.keys`` (the
kit-style key ring, identity-auth §8): session-family kinds (session,
api, invite, download) sign with ``HA_SESSION_KEY``, refresh with
``HA_REFRESH_KEY`` — the families are mutually exclusive, and the
``HA_DATA_KEY`` Fernet family never signs anything. Cross-family tokens
(a session token signed by the refresh key) fail verification.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from uuid import uuid4

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from nx_auth.opaque import parse

from app.core import tenant_context
from app.core.config import settings
from app.core.keys import key_for, verification_keys
from app.models.enums import Role
from app.schemas.user import TokenData

logger = logging.getLogger(__name__)


# --- contract constants (identity-auth §8) ---------------------------------
PRODUCT_SLUG = "health"

SESSION_TOKEN_KIND = "session"
REFRESH_TOKEN_KIND = "refresh"
API_TOKEN_KIND = "api"
INVITE_TOKEN_KIND = "invite"
DOWNLOAD_TOKEN_KIND = "download"
MFA_CHALLENGE_TOKEN_KIND = "mfa_challenge"
PAT_TOKEN_KIND = "pat"

#: The opaque prefix every personal access token starts with (ADR 0054
#: Phase 6, program 12C2B). PATs are **not** JWTs — they resolve by
#: prefix + sha256 against ``api_tokens`` and are never minted here.
PAT_TOKEN_PREFIX = "ha_pat_"

#: The opaque prefix every integration/extension principal secret starts with
#: (ADR 0054 D8, Phase 13). Like a PAT it is not a JWT: it resolves by
#: prefix + sha256 against ``integration_principals``.
INTEGRATION_PRINCIPAL_TOKEN_PREFIX = "ha_int_"

#: Credential-management surfaces a scoped machine credential (PAT or
#: integration principal) must never reach: minting another credential,
#: rotating a password, adding a factor, revoking a device or stepping up
#: would be privilege escalation from a bearer secret. Scoped credentials
#: authenticate **domain** routes only.
SESSION_ONLY_SCOPED_DENIED_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth",
    "/api/v1/me/password",
    "/api/v1/me/mfa",
    "/api/v1/me/passkeys",
    "/api/v1/me/sessions",
    "/api/v1/me/api-tokens",
    "/api/v1/me/integration-principals",
    "/api/v1/integration-principals",
    "/api/v1/me/access",
    "/api/v1/setup",
    "/api/v1/scim",
)

AUTH_MODE_LOCAL_BOOT = "local-boot"
AUTH_MODE_PASSWORD = "password"
AUTH_MODE_OIDC = "oidc"
AUTH_MODE_DEMO = "demo"


def _fit_bcrypt(password: str) -> bytes:
    """Encode a password for bcrypt, truncating to bcrypt's 72-byte limit.

    bcrypt silently truncates (<=3.x) or raises (>=4.1/5.x) on longer
    input; both hash and verify must use identical fitting so behavior is
    uniform across library versions. UTF-8 multibyte passwords are cut on
    a byte boundary within the limit.
    """
    return password.encode("utf-8")[:72]


_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    """A fixed bcrypt hash verified against when the user does not exist.

    Keeps login timing uniform between "unknown email" and "wrong
    password" so response time cannot enumerate registered accounts.
    """
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = get_password_hash("timing-equalizer-dummy-password")
    return _DUMMY_HASH


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    try:
        # bcrypt expects bytes
        return bcrypt.checkpw(_fit_bcrypt(plain_password), hashed_password.encode("utf-8"))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(_fit_bcrypt(password), salt).decode("utf-8")


# --- minting ---------------------------------------------------------------

_STANDARD_CLAIMS = ("iss", "sub", "token_kind", "iat", "exp", "jti")


def _mint(kind: str, data: dict, ttl: timedelta) -> tuple[str, str]:
    """Build the contract claim set for ``kind`` and sign it.

    ``data`` carries the product claims (``user_id``, ``tenant_id``,
    ``role``, ``ver``, ``email``, ``auth_mode``, ``fid``, switched-set…);
    the standard claims above are owned by this function — passing them
    in ``data`` is an error (same guard as the auth-kit's ``mint_token``).
    """
    for claim in _STANDARD_CLAIMS:
        if claim in data:
            raise ValueError(f"extra claims may not override {claim!r}")

    now = datetime.now(UTC)
    jti = uuid4().hex
    to_encode = {k: v for k, v in data.items() if v is not None}
    to_encode.update(
        {
            "iss": PRODUCT_SLUG,
            "token_kind": kind,
            "iat": now,
            "exp": now + ttl,
            "jti": jti,
        }
    )

    if kind in (SESSION_TOKEN_KIND, REFRESH_TOKEN_KIND):
        user_id = data.get("user_id")
        if not user_id:
            raise ValueError("session/refresh tokens require a user_id claim")
        to_encode["sub"] = str(user_id)
        to_encode.setdefault("ver", 1)
        to_encode.setdefault("auth_mode", AUTH_MODE_PASSWORD)
    elif kind == API_TOKEN_KIND:
        to_encode.setdefault("sub", str(data.get("client_id", "")))
    elif kind == INVITE_TOKEN_KIND:
        to_encode.setdefault("sub", str(data.get("email") or ""))
    elif kind == DOWNLOAD_TOKEN_KIND:
        to_encode.setdefault("sub", str(data.get("user_id") or ""))
    elif kind == MFA_CHALLENGE_TOKEN_KIND:
        user_id = data.get("user_id")
        if not user_id:
            raise ValueError("mfa_challenge tokens require a user_id claim")
        to_encode["sub"] = str(user_id)
    elif kind == PAT_TOKEN_KIND:
        raise ValueError("pat tokens are opaque secrets — never minted as a JWT")
    else:
        raise ValueError(f"unknown token kind {kind!r}")

    token = jwt.encode(to_encode, key_for(kind), algorithm=settings.JWT_ALGORITHM)
    return token, jti


def create_session_access_token(
    data: dict, expires_delta: timedelta | None = None
) -> tuple[str, str]:
    """Mint a ``session`` access JWT. Returns ``(token, jti)``.

    Contract claims (§8): ``iss``/``sub``(user id)/``token_kind``/``jti``/
    ``ver``/``auth_mode`` + product claims (``user_id`` mirror, ``email``,
    ``tenant_id``, ``role``, ``fid``, switched-set). The caller must
    register the jti via ``token_store.register_session`` for logout /
    revocation to work.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.HA_AUTH_ACCESS_TTL_MINUTES)
    return _mint(SESSION_TOKEN_KIND, dict(data), expires_delta)


def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> tuple[str, str]:
    """Mint a ``refresh`` JWT. Returns ``(token, jti)``.

    ``token_kind="refresh"`` (the old ``type="refresh"`` claim is gone) plus
    a random ``jti``; the caller must register it via
    ``token_store.register_refresh`` and link it to an ``auth_sessions``
    family row (``fid`` claim) for rotation / reuse detection.
    """
    if expires_delta is None:
        expires_delta = timedelta(days=settings.HA_AUTH_REFRESH_TTL_DAYS)
    return _mint(REFRESH_TOKEN_KIND, dict(data), expires_delta)


# --- decoding / verification ----------------------------------------------


def _decode_with(token: str, key: str) -> dict | None:
    try:
        return jwt.decode(
            token,
            key,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        return None


def decode_token(token: str) -> dict | None:
    """Decode + signature/expiration-validate a JWT of **any** kind.

    Peek path for revocation (oauth) — kind-specific verification happens
    in the callers below, each under its own ring key (§8 key separation).
    ``verify_aud`` is disabled because both session JWTs (no ``aud`` claim,
    frontend) and api tokens (``aud`` set, facade) flow through here.
    """
    for key in verification_keys():
        payload = _decode_with(token, key)
        if payload is not None:
            return payload
    return None


def _kind_is(payload: dict | None, kind: str) -> bool:
    return (
        payload is not None
        and payload.get("iss") == PRODUCT_SLUG
        and payload.get("token_kind") == kind
    )


def verify_access_token(token: str) -> dict | None:
    """Verify a **session** access token and return its payload.

    Rejects every other kind — refresh tokens must never be usable as a
    bearer credential on the domain API (their 7-day lifetime would make
    logout meaningless — audit 2026-08 H3), and api/invite/download kinds
    are mutually exclusive with session (§8).
    """
    payload = _decode_with(token, key_for(SESSION_TOKEN_KIND))
    if not _kind_is(payload, SESSION_TOKEN_KIND):
        return None
    return payload


def decode_refresh_token(token: str) -> dict | None:
    """Decode a refresh token, enforcing ``token_kind="refresh"``.

    Returns the payload or None if the token is invalid, expired, or not a
    refresh token (prevents an access token from being reused at /refresh).
    """
    payload = _decode_with(token, key_for(REFRESH_TOKEN_KIND))
    if not _kind_is(payload, REFRESH_TOKEN_KIND):
        return None
    return payload


def get_token(request: Request, authorization: str = Header(None)):
    """Extract the session credential: **cookie first**, then Bearer (§9/§10).

    Browsers ride the HttpOnly ``nx_access`` cookie (the double-submit
    CSRF middleware gates non-safe cookie requests); §9 user clients —
    the Android app, CLI/scripts — keep ``Authorization: Bearer``. When
    both are present the cookie wins.
    """
    from app.core.cookies import access_cookie_candidates

    for name in access_cookie_candidates():
        token = request.cookies.get(name)
        if token:
            return token

    # Check header second
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token format",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return authorization[7:]

    # Removed insecure query parameter fallback

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def is_pat_token(token: str | None) -> bool:
    """Whether a presented bearer is a PAT-shape opaque secret."""
    return token is not None and token.startswith(PAT_TOKEN_PREFIX)


def is_integration_principal_token(token: str | None) -> bool:
    """Whether a presented bearer is an integration-principal opaque secret."""
    return token is not None and token.startswith(INTEGRATION_PRINCIPAL_TOKEN_PREFIX)


def is_scoped_credential(value: TokenData) -> bool:
    """Whether a resolved principal is a scoped machine credential."""
    return isinstance(getattr(value, "pat_id", None), str) or isinstance(
        getattr(value, "integration_principal_id", None), str
    )


def _scoped_denied_on_path(path: str | None) -> bool:
    if not path:
        return False
    for prefix in SESSION_ONLY_SCOPED_DENIED_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def reject_scoped_principal_on_session_only_surface(
    principal: TokenData, request: Request | None
) -> None:
    """Refuse a scoped credential on credential-management surfaces.

    A PAT or integration principal authenticates domain routes; letting it
    rotate its owner's password, enroll a factor, revoke devices, step up or
    mint another credential would turn one bounded bearer into permanent
    account control. The refusal is explicit (403) rather than a generic 401
    so the client can tell an operator what to use instead.
    """
    if not is_scoped_credential(principal):
        return
    if request is None:
        return
    path = getattr(getattr(request, "url", None), "path", None)
    if _scoped_denied_on_path(path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Scoped credentials cannot be used on credential-management endpoints",
        )


async def authenticate_pat_token(token: str) -> TokenData:
    """Resolve an opaque ``pat`` bearer to its scoped principal.

    The lookup is prefix + sha256 against ``api_tokens``; the service
    enforces active/expiry/owner/tenant and re-intersects the permission
    set with the owner's live role. Invalid credentials answer the same
    401 as a failed session verification and write one ``api_token.denied``
    audit row (fail-closed). The audit prefix comes from the kit's strict
    ``parse``: a bearer that cannot have been minted in the PAT shape
    records ``token_prefix: null`` instead of a junk segment.
    """
    from app.services.auth import api_tokens

    principal = await api_tokens.resolve_principal(token)
    if principal is None:
        presented = parse(token.strip(), brand=api_tokens.PAT_TOKEN_BRAND) if token else None
        await api_tokens.audit_denied(
            reason="invalid_token", prefix=presented[0] if presented else None
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


async def authenticate_integration_principal_token(token: str) -> TokenData:
    """Resolve an opaque ``ha_int_`` bearer to its scoped principal.

    Same shape as the PAT path: prefix + sha256 against
    ``integration_principals``, owner/tenant pairing verified, the scope set
    re-intersected with the owner's live role and the extension declaration.
    Failures answer 401 and write one ``integration_principal.denied`` row.
    """
    from app.services.integration import integration_principals

    principal = await integration_principals.resolve_principal(token)
    if principal is None:
        await integration_principals.audit_denied(
            reason="invalid_token", prefix=integration_principals.secret_prefix_of(token)
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


async def authenticate_bearer_token(token: str) -> TokenData:
    """Dispatch a bearer by shape: opaque credentials, else the session JWT.

    Neither opaque kind mints or refreshes anything — they carry their own
    permission and patient scope and are re-intersected on every use.
    """
    if is_pat_token(token):
        return await authenticate_pat_token(token)
    if is_integration_principal_token(token):
        return await authenticate_integration_principal_token(token)
    return await authenticate_session_token(token)


async def authenticate_session_token(token: str) -> TokenData:
    """The single session-verification path (identity-auth §4/§7/§8).

    Used by ``get_current_user`` and ``get_session_user_ws`` — HTTP and WS
    must never implement their own rules. Raises ``HTTPException(401)`` on
    any failure; the WS wrapper maps it to a handshake rejection.

    Enforcement order:
    1. signature / expiry / ``iss`` / ``token_kind=="session"`` (kinds are
       mutually exclusive — api/invite/download/refresh are refused);
    2. instance state (DB fact, per request): a ``demo`` token is valid
       only while ``instance_settings.demo_mode=true`` (S-7); unknown
       modes fail closed to ``authenticated``;
    3. the Redis jti store is the hot revocation layer (logout / role
       changes kill tokens immediately);
    4. the live user row: it must exist, be ``is_active`` (§5/§18.9), and
       its ``token_version`` must equal the ``ver`` claim (§8 — bump =
       global sign-out).
    """
    from app.core import instance_state, token_store

    def _deny(detail: str = "Could not validate credentials") -> NoReturn:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_access_token(token)
    if not payload:
        _deny("Invalid or expired token")

    user_id = payload.get("sub") or payload.get("user_id")
    jti = payload.get("jti")
    auth_mode = payload.get("auth_mode")
    ver = payload.get("ver")
    if not user_id or not jti or ver is None or auth_mode is None:
        _deny()

    # RLS context from the signature-verified claims (ADR 0054 D4) — the
    # verifier's own lookups below (the live user row) run under the
    # planted context. A switched SYSTEM_ADMIN's user row lives in its
    # home tenant (``original_tenant_id``), so that claim drives the
    # planted tenant GUC (the system flag stays role-driven — full reach);
    # the authoritative set after the live-user check plants the same
    # context again (idempotent). A malformed claim set clears the context
    # and the verifier rejects below.
    try:
        tenant_context.set_principal(TokenData(**payload))
    except Exception:
        tenant_context.clear()

    if auth_mode not in (
        AUTH_MODE_LOCAL_BOOT,
        AUTH_MODE_PASSWORD,
        AUTH_MODE_OIDC,
        AUTH_MODE_DEMO,
    ):
        _deny()

    # §4 state-derived instance facts — never a boot-time snapshot.
    state = await instance_state.get_state()
    if auth_mode == AUTH_MODE_DEMO and not state.demo_mode:
        # S-7: demo tokens die the moment the instance stops being a demo.
        # Debug-level on purpose: once demo_mode flips off every demo-token
        # request lands here, and the loud signals (audit rows) live on the
        # login/refresh paths where the frequency is bounded.
        logger.debug("demo token rejected: instance_settings.demo_mode is false (§13/S-7)")
        _deny("Invalid or expired token")
    if (
        auth_mode == AUTH_MODE_LOCAL_BOOT
        and instance_state.effective_auth_mode(state) != instance_state.AUTH_MODE_OPEN
    ):
        # §4.3: local-boot tokens are minted only in open desktop mode;
        # health is a server product and never runs open.
        _deny("Invalid or expired token")

    if not await token_store.is_session_active(
        str(user_id), str(jti), str(payload.get("fid")) if payload.get("fid") else None
    ):
        _deny("Token has been revoked")

    try:
        token_data = TokenData(**payload)
    except Exception:
        _deny()

    # §5/§18.9 + §8 — verified against the live user row.
    from app.services.tenancy.user_service import get_user_by_id

    user = await get_user_by_id(str(user_id))
    if user is None or not getattr(user, "is_active", False):
        _deny("Invalid or expired token")
    try:
        ver_raw = user.token_version
        user_ver = int(ver_raw) if ver_raw is not None else 1
    except (TypeError, ValueError):
        user_ver = -1
    if user_ver != int(ver):
        # token_version bump = global sign-out (logout-all, admin change).
        _deny("Token has been revoked")

    # Tenant security policy (ADR 0054 Phase 3) — read per request, never
    # token-cached: a policy flip applies mid-session. The idle/max-age
    # window rides the family's Redis anchor (sesspol:{fid}) and degrades
    # open on a Redis outage; the refresh path additionally enforces the
    # max-age cap from the authoritative auth_sessions row.
    from app.services.auth import security_policy

    policy = await security_policy.tenant_security_policy_for(str(user.tenant_id))
    family_id = payload.get("fid")
    denial = await security_policy.check_session_window(
        policy, str(family_id) if family_id else None
    )
    if denial is not None:
        _deny(denial)

    if family_id:
        # Device-list activity (0019): throttled to one write per minute per
        # family, best-effort. Bookkeeping must never fail a verified
        # request, so the helper swallows its own errors.
        from app.services.auth import auth_session_service

        await auth_session_service.touch_last_seen(str(family_id))

    # Authoritative context (ADR 0054 D4): the operation tenant rides the
    # token claim for authorization (the switch target when switched — the
    # live user row carries the home tenant), the system flag the resolved
    # role; set_principal plants the home tenant on the GUC for a switched
    # principal (identity-honesty — see its docstring).
    tenant_context.set_principal(token_data)
    return token_data


async def get_current_user(request: Request = None, token: str = Depends(get_token)):  # type: ignore[assignment]
    """Resolve the caller principal: session JWT **or** opaque PAT.

    Session tokens run the family verification path
    (:func:`authenticate_session_token`); a ``ha_pat_`` bearer resolves
    through :func:`authenticate_pat_token` and carries its own permission
    and patient scope. PATs are refused on credential-management surfaces.
    When the central route gate (``app.core.authz.enforce``) already
    verified this request's token, the cached principal is returned — one
    verification per request (and the same surface refusal applies).

    ``request: Request = None`` mirrors the FastAPI DI constraint documented
    in ``app.core.authz.enforce.get_optional_session_user``: the exact
    ``Request`` annotation is what FastAPI injects against, and the None
    default serves the direct callers below (e.g.
    ``get_current_user_id``) that run outside a request.
    """
    if request is not None:
        cached = getattr(request.state, "session_user", None)
        if cached is not None:
            reject_scoped_principal_on_session_only_surface(cached, request)
            return cached
    token_data = await authenticate_bearer_token(token)
    reject_scoped_principal_on_session_only_surface(token_data, request)
    if request is not None:
        request.state.session_user = token_data
    return token_data


async def get_session_user_ws(token: str):
    """Get current user for a WebSocket handshake (identity-auth §12).

    Same single verification path as :func:`get_current_user`; raises
    ``Exception`` so the WS layer maps it to a handshake rejection.
    """
    try:
        return await authenticate_session_token(token)
    except HTTPException as e:
        raise Exception(e.detail) from e


# Legacy alias removed on purpose (identity-auth §12 name is
# ``get_session_user_ws``); H3 wires the cookie + Origin gate on top.


class RoleChecker:
    """Class S role gate (identity-auth §17 — roles replace Class D's
    is_admin boolean). SYSTEM_ADMIN passes every gate."""

    def __init__(self, allowed_roles: list[Role]):
        self.allowed_roles = [r.value if isinstance(r, Role) else r for r in allowed_roles]

    def __call__(self, current_user: TokenData = Depends(get_current_user)):
        if is_scoped_credential(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Scoped credentials cannot satisfy a role gate",
            )
        if (
            current_user.role not in self.allowed_roles
            and current_user.role != Role.SYSTEM_ADMIN.value
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {current_user.role} is not authorized to access this resource",
            )
        return current_user


async def require_admin(
    current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Identity-auth §12 dependency name.

    Health's ``is_admin`` is derived from the Class S role enum
    (ADMIN/SYSTEM_ADMIN — see ``UserModel.is_admin``). A scoped machine
    credential never satisfies a role gate: its authorization is its own
    permission set, checked by the central route gate.
    """
    if is_scoped_credential(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Scoped credentials cannot satisfy a role gate",
        )
    if current_user.role not in (Role.ADMIN.value, Role.SYSTEM_ADMIN.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def get_current_user_id(token: str = Depends(get_token)) -> str:
    """Get current user ID from JWT token"""
    # No request → no request.state cache; the None default above is the
    # documented direct-call convention (FastAPI DI constraint), hence the
    # targeted ignore.
    payload = await get_current_user(None, token)  # type: ignore[arg-type]
    return str(payload.user_id)


# --- download (presign) tokens --------------------------------------------


def create_presigned_token(
    document_id: str,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> str:
    """Create a short-lived ``download`` token for one file (presign pattern).

    ``token_kind="download"`` + ``doc_id``/``tenant_id`` binding, 5-minute
    TTL (S8). The Class S presign semantics are unchanged (not single-use
    consumed — the frontend <img> preview may re-fetch). ``tenant_id`` is
    verified against the document's tenant at download/preview time, so a
    token minted by one tenant never serves another tenant's file, and the
    ``user_id`` claim is re-checked against the live user row (active,
    same id) — deactivating or deleting a user kills their outstanding
    tokens within the TTL window.
    """
    token, _ = _mint(
        DOWNLOAD_TOKEN_KIND,
        {
            "doc_id": document_id,
            "user_id": str(user_id) if user_id else None,
            "tenant_id": str(tenant_id) if tenant_id else None,
        },
        timedelta(minutes=5),
    )
    return token


def verify_presigned_token(token: str, expected_doc_id: str) -> dict | None:
    """Verify a short-lived ``download`` token; return its claims or ``None``.

    Checks the JWT signature, the ``token_kind == "download"`` claim, the
    ``exp`` window and the ``doc_id`` binding. The ``tenant_id``/``user_id``
    claims are validated against the loaded document and the live user row
    by the download/preview endpoints
    (``app.api.v1.endpoints.documents._validate_presign_claims``) — the
    token alone proves issuance, not authorization.
    """
    payload = _decode_with(token, key_for(DOWNLOAD_TOKEN_KIND))
    if not _kind_is(payload, DOWNLOAD_TOKEN_KIND):
        return None
    if payload is None or payload.get("doc_id") != expected_doc_id:
        return None
    return payload


# --- MFA challenge tokens (plan 16 H5) -------------------------------------

#: Challenge TTL — the window a login MFA challenge stays answerable.
MFA_CHALLENGE_TTL_SECONDS = 5 * 60


def create_mfa_challenge_token(
    user_id: str,
    expires_delta: timedelta | None = None,
    *,
    origin: str | None = None,
) -> tuple[str, str]:
    """Mint a short-lived ``mfa_challenge`` JWT. Returns ``(token, jti)``.

    Issued by ``POST /auth/login`` (and the SSO callback, program 12C1)
    after the first factor verifies but a TOTP/recovery code is still owed.
    Signed with the session key family (§8 — a first-party, short-lived
    credential) yet **mutually exclusive** with session tokens:
    ``verify_access_token`` rejects it, so a challenge can never ride the
    domain API. Binds ``sub`` to the user id; the caller must register the
    jti via ``token_store.register_mfa_challenge`` (single-use — consumed by
    the first successful verify) and the TTL is 5 minutes.

    ``origin`` records which first factor produced the challenge
    (``"sso"`` for the OIDC callback). The verify endpoint mints the
    session with the matching ``auth_mode``, so an SSO sign-in never
    downgrades to a password-mode session at the MFA step — and a failed
    SSO MFA challenge can never fall back to the local password path.
    """
    if expires_delta is None:
        expires_delta = timedelta(seconds=MFA_CHALLENGE_TTL_SECONDS)
    return _mint(
        MFA_CHALLENGE_TOKEN_KIND,
        {"user_id": str(user_id), "origin": origin},
        expires_delta,
    )


def verify_mfa_challenge_token(token: str) -> dict | None:
    """Decode + validate an ``mfa_challenge`` token.

    Returns the payload or None (invalid signature, wrong kind, expired).
    Single-use consumption and the live user check are the verify
    endpoint's job (it needs the async token store).
    """
    payload = _decode_with(token, key_for(MFA_CHALLENGE_TOKEN_KIND))
    if not _kind_is(payload, MFA_CHALLENGE_TOKEN_KIND):
        return None
    return payload


# --- invite tokens (Class S) ----------------------------------------------


def create_invite_token(
    tenant_id: str,
    email: str | None = None,
    role: str = "USER",
    expires_days: int = 7,
) -> tuple[str, str]:
    """Mint a tenant-scoped, **single-use** invite token.

    Returns ``(token, jti)``. The caller must register the jti via
    ``token_store.register_invite``; the register endpoint consumes it
    atomically on first use so a leaked invite cannot onboard an
    unlimited number of accounts (audit 2026-08 M3).

    Used by ``POST /auth/invite`` (admin-only) to onboard a new member into
    the admin's tenant. The token:

    - ``token_kind="invite"`` (§8 — kinds are mutually exclusive) and
      ``sub`` = the invited email (or ``""`` when unbound).
    - ``tenant_id`` binds the token to the issuing tenant; the register
      endpoint re-checks it against the request body's ``tenant_id``.
    - ``role`` (optional) lets the admin pre-assign a role (USER/ADMIN/
      MANAGER). SYSTEM_ADMIN is forbidden here — bootstrap is the only
      path that grants SYSTEM_ADMIN.
    - Default TTL is 7 days; the issuing admin can shorten via the
      ``expires_days`` arg (capped at 30 by the endpoint).
    """
    if role == Role.SYSTEM_ADMIN.value:
        raise ValueError("SYSTEM_ADMIN cannot be granted via invite token")
    expires_days = max(1, min(int(expires_days), 30))
    token, jti = _mint(
        INVITE_TOKEN_KIND,
        {
            "tenant_id": str(tenant_id),
            "role": role,
            "email": (str(email).strip().lower() if email else None),
        },
        timedelta(days=expires_days),
    )
    return token, jti


def _decode_invite(token: str) -> dict | None:
    payload = _decode_with(token, key_for(INVITE_TOKEN_KIND))
    if not _kind_is(payload, INVITE_TOKEN_KIND):
        return None
    return payload


def invite_jti(token: str) -> str | None:
    """Return the single-use ``jti`` of an invite token, if present."""
    payload = _decode_invite(token)
    return payload.get("jti") if payload else None


def verify_invite_token(
    token: str,
    expected_tenant_id: str,
    expected_email: str | None = None,
) -> tuple[bool, str | None]:
    """Verify a tenant invite token (signature + scope only).

    Single-use consumption is the register endpoint's job (it needs the
    async token store): call ``token_store.consume_invite(invite_jti(token))``
    after this returns ok. Legacy tokens without a jti are still
    signature-verified; they simply cannot be single-use.
    """
    payload = _decode_invite(token)
    if not payload:
        return (False, None)
    if payload.get("tenant_id") != str(expected_tenant_id):
        return (False, None)
    if expected_email and payload.get("email") not in (None, expected_email):
        return (False, None)
    role = payload.get("role") or Role.USER.value
    if role == Role.SYSTEM_ADMIN.value:
        # Defense in depth — bootstrap is the only SYSTEM_ADMIN grantor.
        role = Role.USER.value
    return (True, role)


# ---------------------------------------------------------------------------
# OAuth2 client-credentials — api tokens for the FHIR R4 facade
# ---------------------------------------------------------------------------
# The facade is the external-only interop surface (see docs/API_LAYERS.md).
# External systems authenticate with the OAuth2 client-credentials grant
# (RFC 6749 §4.4) and receive a short-lived JWT carrying SMART-on-FHIR scopes.
# Session JWTs (frontend/mobile) are rejected on the facade; api tokens are
# rejected on the domain REST API (require_session_token guard).


def _aud_matches(aud_claim, expected: str) -> bool:
    """True if the JWT ``aud`` claim contains the expected audience."""
    if not aud_claim:
        return False
    if isinstance(aud_claim, str):
        return aud_claim == expected
    if isinstance(aud_claim, (list, tuple)):
        return expected in aud_claim
    return False


def create_api_access_token(
    *,
    client_id: str,
    tenant_id: str,
    scopes: list[str],
    bound_patient_id: str | None = None,
    expires_delta: timedelta | None = None,
) -> tuple[str, str]:
    """Mint an OAuth2 client-credentials access token (JWT).

    Returns ``(token, jti)``. The token carries ``token_kind="api"``, the
    SMART ``scope`` string, the OAuth ``aud`` claim, and the client's
    ``tenant_id``. There is no ``user_id`` / ``role`` — an api token is a
    client principal (``sub`` = client id), not a user. ``bound_patient_id``
    (for ``patient/`` scoped clients) is embedded so the facade can enforce
    the patient compartment without a DB lookup per request. The caller may
    register the ``jti`` for revocation via ``token_store``.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.OAUTH_ACCESS_TOKEN_TTL_MINUTES)
    token, jti = _mint(
        API_TOKEN_KIND,
        {
            "client_id": client_id,
            "tenant_id": str(tenant_id),
            "scope": " ".join(scopes),
            "aud": settings.OAUTH_AUDIENCE,
            "bound_patient_id": (str(bound_patient_id) if bound_patient_id is not None else None),
        },
        expires_delta,
    )
    return token, jti


async def get_api_principal(
    token: str = Depends(get_token),
) -> "TokenData":
    """Facade auth dependency — OAuth2 api tokens only.

    The facade is the external-only surface; session JWTs (frontend) are
    rejected with 401. Validates the token, enforces ``token_kind="api"``,
    checks the ``aud`` claim matches ``OAUTH_AUDIENCE`` (defense in depth),
    and consults the api-token revocation list (``token_store``). Returns a
    ``TokenData`` whose ``scope_set`` drives SMART scope enforcement
    (Phase 2). The principal carries ``tenant_id`` (client-bound) and no
    ``user_id``/``role``.
    """
    from app.core import token_store
    from app.schemas.user import TokenData

    payload = decode_token(token)
    if not payload or payload.get("iss") != PRODUCT_SLUG:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("token_kind") != API_TOKEN_KIND:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "The FHIR facade is an external API; session tokens are not "
                "accepted. Obtain an OAuth2 client token via POST /api/v1/oauth/token."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _aud_matches(payload.get("aud"), settings.OAUTH_AUDIENCE):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token audience",
            headers={"WWW-Authenticate": "Bearer"},
        )
    jti = payload.get("jti")
    if jti and await token_store.is_api_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        principal = TokenData(**payload)
    except Exception:
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # RLS context from the verified facade principal (ADR 0054 D4): the
    # client's tenant, its patient binding for ``patient/``-scoped
    # clients, never the system flag.
    tenant_context.set_principal(principal)
    return principal


async def require_session_token(
    authorization: str | None = Header(None),
) -> None:
    """Guard dependency: block api tokens on session-only (domain) routes.

    The domain REST API (``/api/v1/*`` except the facade + OAuth) is for
    first-party clients holding a session JWT. This guard peeks at the
    ``Authorization`` header and rejects any ``token_kind="api"`` token with
    401 — api tokens must use the FHIR facade. Anonymous requests (no header)
    and session tokens pass through unchanged; each endpoint's own
    ``get_current_user`` dependency still enforces authentication where needed.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return  # Anonymous — the endpoint's own auth dependency handles it.
    token = authorization[7:]
    payload = decode_token(token)
    if payload and payload.get("token_kind") == API_TOKEN_KIND:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "API tokens are not accepted on this endpoint; use the FHIR "
                "facade (/api/v1/fhir/R4/*)."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
