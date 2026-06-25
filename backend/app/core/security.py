import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from app.core.config import settings
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
        expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRATION_HOURS)

    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )

    return encoded_jwt


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT access token"""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except jwt.PyJWTError:
        return None


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


from app.schemas.user import TokenData
from typing import List, Optional
from app.models.enums import Role


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
        self.allowed_roles = [r.value if isinstance(r, Role) else r for r in allowed_roles]

    def __call__(self, current_user: TokenData = Depends(get_current_user)):
        if current_user.role not in self.allowed_roles and current_user.role != Role.SYSTEM_ADMIN.value:
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
