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
===========  =========================  ===========================================

Signing keys are resolved per purpose through ``app.core.keys`` (the
kit-style key ring, identity-auth §8): session-family kinds (session,
api, invite, download) sign with ``HA_SESSION_KEY``, refresh with
``HA_REFRESH_KEY`` — the families are mutually exclusive, and the
``HA_DATA_KEY`` Fernet family never signs anything. Cross-family tokens
(a session token signed by the refresh key) fail verification.
"""

import logging

import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from typing import List, Optional
from app.core.config import settings
from app.core.keys import key_for, verification_keys
from app.models.enums import Role
from app.schemas.user import TokenData
from fastapi import HTTPException, status, Header, Depends, Request

logger = logging.getLogger(__name__)


# --- contract constants (identity-auth §8) ---------------------------------
PRODUCT_SLUG = "health"

SESSION_TOKEN_KIND = "session"
REFRESH_TOKEN_KIND = "refresh"
API_TOKEN_KIND = "api"
INVITE_TOKEN_KIND = "invite"
DOWNLOAD_TOKEN_KIND = "download"
MFA_CHALLENGE_TOKEN_KIND = "mfa_challenge"

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
        return bcrypt.checkpw(
            _fit_bcrypt(plain_password), hashed_password.encode("utf-8")
        )
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

    now = datetime.now(timezone.utc)
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


def create_refresh_token(
    data: dict, expires_delta: timedelta | None = None
) -> tuple[str, str]:
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
        bool(payload)
        and payload.get("iss") == PRODUCT_SLUG
        and payload.get("token_kind") == kind
    )


def verify_access_token(token: str) -> dict:
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
    from app.core import token_store
    from app.core import instance_state

    def _deny(detail: str = "Could not validate credentials") -> None:
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
        logger.debug(
            "demo token rejected: instance_settings.demo_mode is false (§13/S-7)"
        )
        _deny("Invalid or expired token")
    if (
        auth_mode == AUTH_MODE_LOCAL_BOOT
        and instance_state.effective_auth_mode(state) != instance_state.AUTH_MODE_OPEN
    ):
        # §4.3: local-boot tokens are minted only in open desktop mode;
        # health is a server product and never runs open.
        _deny("Invalid or expired token")

    if not await token_store.is_session_active(str(user_id), str(jti)):
        _deny("Token has been revoked")

    try:
        token_data = TokenData(**payload)
    except Exception:
        _deny()

    # §5/§18.9 + §8 — verified against the live user row.
    from app.services.user_service import get_user_by_id

    user = await get_user_by_id(str(user_id))
    if user is None or not getattr(user, "is_active", False):
        _deny("Invalid or expired token")
    try:
        user_ver = int(user.token_version or 1)
    except (TypeError, ValueError):
        user_ver = -1
    if user_ver != int(ver):
        # token_version bump = global sign-out (logout-all, admin change).
        _deny("Token has been revoked")

    return token_data


async def get_current_user(token: str = Depends(get_token)):
    """Get current user from the session JWT (identity-auth §12).

    Session tokens only; verified against live instance state and the live
    user row via :func:`authenticate_session_token`.
    """
    return await authenticate_session_token(token)


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

    def __init__(self, allowed_roles: List[Role]):
        self.allowed_roles = [
            r.value if isinstance(r, Role) else r for r in allowed_roles
        ]

    def __call__(self, current_user: TokenData = Depends(get_current_user)):
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
    (ADMIN/SYSTEM_ADMIN — see ``UserModel.is_admin``).
    """
    if current_user.role not in (Role.ADMIN.value, Role.SYSTEM_ADMIN.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def get_current_user_id(token: str = Depends(get_token)) -> str:
    """Get current user ID from JWT token"""
    payload = await get_current_user(token)
    return str(payload.user_id)


# --- download (presign) tokens --------------------------------------------


def create_presigned_token(document_id: str, user_id: str | None = None) -> str:
    """Create a short-lived ``download`` token for one file (presign pattern).

    ``token_kind="download"`` + ``doc_id`` binding, 5-minute TTL. The
    Class S presign semantics are unchanged (not single-use consumed —
    the frontend <img> preview may re-fetch); only the claims moved to the
    contract shape.
    """
    token, _ = _mint(
        DOWNLOAD_TOKEN_KIND,
        {
            "doc_id": document_id,
            "user_id": str(user_id) if user_id else None,
        },
        timedelta(minutes=5),
    )
    return token


def verify_presigned_token(token: str, expected_doc_id: str) -> bool:
    """Verify a short-lived ``download`` token"""
    payload = _decode_with(token, key_for(DOWNLOAD_TOKEN_KIND))
    if not _kind_is(payload, DOWNLOAD_TOKEN_KIND):
        return False
    if payload.get("doc_id") != expected_doc_id:
        return False
    return True


# --- MFA challenge tokens (plan 16 H5) -------------------------------------

#: Challenge TTL — the window a login MFA challenge stays answerable.
MFA_CHALLENGE_TTL_SECONDS = 5 * 60


def create_mfa_challenge_token(
    user_id: str, expires_delta: timedelta | None = None
) -> tuple[str, str]:
    """Mint a short-lived ``mfa_challenge`` JWT. Returns ``(token, jti)``.

    Issued by ``POST /auth/login`` after the password verifies but a
    TOTP/recovery code is still owed. Signed with the session key family
    (§8 — a first-party, short-lived credential) yet **mutually
    exclusive** with session tokens: ``verify_access_token`` rejects it,
    so a challenge can never ride the domain API. Binds ``sub`` to the
    user id; the caller must register the jti via
    ``token_store.register_mfa_challenge`` (single-use — consumed by the
    first successful verify) and the TTL is 5 minutes.
    """
    if expires_delta is None:
        expires_delta = timedelta(seconds=MFA_CHALLENGE_TTL_SECONDS)
    return _mint(
        MFA_CHALLENGE_TOKEN_KIND,
        {"user_id": str(user_id)},
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
            "bound_patient_id": (
                str(bound_patient_id) if bound_patient_id is not None else None
            ),
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
    from app.schemas.user import TokenData
    from app.core import token_store

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
        return TokenData(**payload)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_session_token(
    authorization: Optional[str] = Header(None),
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
