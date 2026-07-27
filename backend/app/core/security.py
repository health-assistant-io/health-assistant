import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from typing import List, Optional
from app.core.config import settings
from app.models.enums import Role
from app.schemas.user import TokenData
from fastapi import HTTPException, status, Header, Depends, Request


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    try:
        # bcrypt expects bytes
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            hours=settings.JWT_EXPIRATION_HOURS
        )

    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )

    return encoded_jwt


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT access token.

    ``verify_aud`` is disabled here because both session JWTs (no ``aud``
    claim, frontend) and api tokens (``aud`` set, facade) flow through this
    decoder. The api-token audience is enforced explicitly in
    :func:`get_api_principal` via ``_aud_matches``; the domain REST API blocks
    api tokens via the :func:`require_session_token` guard.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
        return payload
    except jwt.PyJWTError:
        return None


# Refresh tokens carry a ``type=refresh`` claim + a unique ``jti`` so they can
# be rotated and revoked server-side (audit A5). A plain access token must not
# be accepted at /auth/refresh.
REFRESH_TOKEN_TYPE = "refresh"
REFRESH_TOKEN_DAYS = 7


def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> tuple[str, str]:
    """Create a refresh JWT. Returns ``(token, jti)``.

    The token embeds ``type="refresh"`` and a random ``jti``; the caller must
    register the jti via ``token_store.register_refresh`` for it to be valid.
    """
    to_encode = data.copy()
    if expires_delta is None:
        expires_delta = timedelta(days=REFRESH_TOKEN_DAYS)
    expire = datetime.now(timezone.utc) + expires_delta
    jti = uuid4().hex
    to_encode.update(
        {"exp": expire, "iat": datetime.now(timezone.utc), "type": REFRESH_TOKEN_TYPE, "jti": jti}
    )
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt, jti


def decode_refresh_token(token: str) -> dict | None:
    """Decode a refresh token, enforcing the ``type=refresh`` claim.

    Returns the payload or None if the token is invalid, expired, or not a
    refresh token (prevents an access token from being reused at /refresh).
    """
    payload = decode_access_token(token)
    if not payload:
        return None
    if payload.get("type") != REFRESH_TOKEN_TYPE:
        return None
    return payload


def verify_access_token(token: str) -> dict:
    """Verify access token and return payload"""
    payload = decode_access_token(token)
    if not payload:
        return None

    exp = payload.get("exp")
    if exp and datetime.now(timezone.utc).timestamp() > float(exp):
        return None

    return payload


def get_token(request: Request, authorization: str = Header(None)):
    """Extract token from Authorization header or query parameter"""
    # Check header first
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


def get_current_user(token: str = Depends(get_token)):
    """Get current user from JWT token"""
    from app.schemas.user import TokenData

    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        token_data = TokenData(**payload)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token_data


class RoleChecker:
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


def get_current_user_id(token: str = Depends(get_token)) -> str:
    """Get current user ID from JWT token"""
    payload = get_current_user(token)
    return str(payload.user_id)


def create_presigned_token(document_id: str) -> str:
    """Create a short-lived token specifically for downloading a file"""
    to_encode = {
        "sub": "download",
        "doc_id": document_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),  # 5 minutes only
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_presigned_token(token: str, expected_doc_id: str) -> bool:
    """Verify a short-lived download token"""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        if payload.get("sub") != "download":
            return False
        if payload.get("doc_id") != expected_doc_id:
            return False
        return True
    except jwt.PyJWTError:
        return False


def create_invite_token(
    tenant_id: str,
    email: str | None = None,
    role: str = "USER",
    expires_days: int = 7,
) -> str:
    """Mint a tenant-scoped invite token.

    Used by ``POST /auth/invite`` (admin-only) to onboard a new member into
    the admin's tenant. The token:

    - ``sub = "invite"`` so it cannot be confused with a session JWT.
    - ``tenant_id`` binds the token to the issuing tenant; the register
      endpoint re-checks it against the request body's ``tenant_id``.
    - ``role`` (optional) lets the admin pre-assign a role (USER/ADMIN/
      MANAGER). SYSTEM_ADMIN is forbidden here — bootstrap is the only
      path that grants SYSTEM_ADMIN.
    - Default TTL is 7 days; the issuing admin can shorten via the
      ``expires_days`` arg.
    """
    if role == Role.SYSTEM_ADMIN.value:
        raise ValueError("SYSTEM_ADMIN cannot be granted via invite token")
    to_encode = {
        "sub": "invite",
        "tenant_id": str(tenant_id),
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=expires_days),
        "iat": datetime.now(timezone.utc),
    }
    if email:
        to_encode["email"] = email
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_invite_token(
    token: str,
    expected_tenant_id: str,
    expected_email: str | None = None,
) -> tuple[bool, str | None]:
    """Verify a tenant invite token.

    Returns ``(ok, role)``: ``ok`` is True iff the token is well-formed,
    unexpired, scoped to ``expected_tenant_id``, and (if ``expected_email``
    is supplied) bound to that email. ``role`` is the role to grant
    (defaults to ``USER``); SYSTEM_ADMIN is never returned.
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        return (False, None)
    if payload.get("sub") != "invite":
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


async def get_current_user_ws(token: str):
    """Get current user for WebSocket connection"""
    payload = verify_access_token(token)
    if not payload:
        raise Exception("Invalid token")
    from app.schemas.user import TokenData

    return TokenData(**payload)


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
    SMART ``scope`` string, the OAuth ``aud``/``iss`` claims, and the client's
    ``tenant_id``. There is no ``user_id`` / ``role`` — an api token is a
    client principal, not a user. ``bound_patient_id`` (for ``patient/``
    scoped clients) is embedded so the facade can enforce the patient
    compartment without a DB lookup per request. The caller may register the
    ``jti`` for revocation via ``token_store``.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.OAUTH_ACCESS_TOKEN_TTL_MINUTES)
    jti = uuid4().hex
    to_encode = {
        "sub": client_id,
        "client_id": client_id,
        "tenant_id": str(tenant_id),
        "scope": " ".join(scopes),
        "token_kind": "api",
        "aud": settings.OAUTH_AUDIENCE,
        "iss": settings.OAUTH_ISSUER or settings.APP_URL,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
        "jti": jti,
    }
    if bound_patient_id is not None:
        to_encode["bound_patient_id"] = str(bound_patient_id)
    token = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
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

    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("token_kind") != "api":
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
    payload = verify_access_token(token)
    if payload and payload.get("token_kind") == "api":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "API tokens are not accepted on this endpoint; use the FHIR "
                "facade (/api/v1/fhir/R4/*)."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
