"""Authentication endpoints — login, register, token refresh, invite.

Audit item B7: ``POST /auth/register`` previously accepted any
``tenant_id`` with no check that the caller was authorized to join.
An attacker who learned a victim ``tenant_id`` could register inside it
and immediately read tenant-scoped data. The first-user SYSTEM_ADMIN
check used ``SELECT COUNT(*) FROM users`` with no locking, so two
concurrent bootstrap registrations could both promote.

Post-fix contract:
1. **Bootstrap (no tenant_id)** — creates a new tenant and the new user
   becomes SYSTEM_ADMIN of it. Race-protected by
   ``pg_advisory_xact_lock`` held for the duration of the count + insert.
2. **Join existing tenant (tenant_id + invite_token)** — verifies the
   tenant exists AND that ``invite_token`` is a valid JWT signed with
   ``SECRET_KEY``, scoped to that tenant. 403 otherwise. The role in
   the token wins; SYSTEM_ADMIN is never granted via this path.
3. **Invite issuance (POST /auth/invite)** — ADMIN+ only. Mints a
   7-day token scoped to the caller's tenant.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_invite_token,
    decode_access_token,
    get_current_user,
    get_password_hash,
    get_current_user_id,
    verify_invite_token,
    verify_password,
)
from app.models.enums import Role
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.schemas.auth import TokenRefresh, TokenResponse, UserRegister
from app.schemas.user import TokenData, UserResponse
from app.services.tenant_service import create_tenant, get_tenant
from app.services.user_service import (
    create_user as service_create_user,
    get_user_by_email,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


# Stable 64-bit key for the bootstrap advisory lock. Picked from a hash of
# "HEALTH_ASSISTANT_BOOTSTRAP" so it's deterministic across code paths but
# unlikely to collide with anything else in the DB.
_BOOTSTRAP_ADVISORY_KEY = 0x48414F424F4F54  # 'HAOBOOT' as int56


@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Authenticate user and return tokens"""
    user = await get_user_by_email(form_data.username)

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    access_token = create_access_token(
        data={
            "sub": user.email,
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": getattr(user.role, "value", user.role),
        },
        expires_delta=access_token_expires,
    )

    refresh_token_expires = timedelta(days=7)
    refresh_token = create_access_token(
        data={
            "sub": user.email,
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": getattr(user.role, "value", user.role),
        },
        expires_delta=refresh_token_expires,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=int(access_token_expires.total_seconds()),
    )


@router.post("/register", response_model=UserResponse)
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_db)):
    """Register a new user.

    See module docstring for the two onboarding paths.
    """
    user = await get_user_by_email(user_data.email)
    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    hashed_password = get_password_hash(user_data.password)

    if user_data.tenant_id:
        # Joining an existing tenant — require a valid invite token.
        tenant = await get_tenant(user_data.tenant_id)
        if not tenant:
            # 404 (not 403) so we don't leak that the tenant exists but is
            # locked down — matches the rest of the API's posture.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )

        if not user_data.invite_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "An invite token is required to join an existing tenant. "
                    "Ask the tenant administrator to issue one via POST /auth/invite."
                ),
            )

        ok, granted_role = verify_invite_token(
            user_data.invite_token,
            expected_tenant_id=str(tenant.id),
            expected_email=user_data.email,
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid, expired, or tenant-mismatched invite token.",
            )

        role = granted_role or Role.USER.value
        new_user = await service_create_user(
            email=user_data.email,
            hashed_password=hashed_password,
            tenant_id=str(tenant.id),
            role=role,
        )
        return new_user

    # Bootstrap path — create a new tenant and become its SYSTEM_ADMIN.
    # The race-protection lock serializes the count + insert so two
    # concurrent bootstrap registrations cannot both promote. The lock
    # is held for the duration of this transaction (released on commit /
    # rollback), and the INSERT happens in the same session so the count
    # sees the new row before the lock is released.
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _BOOTSTRAP_ADVISORY_KEY})

    count_result = await db.execute(select(func.count()).select_from(UserModel))
    total_users = count_result.scalar() or 0
    is_first_user = total_users == 0

    tenant_name = user_data.email.split("@")[0].capitalize()
    new_tenant = await create_tenant(name=f"{tenant_name} Household")
    if not new_tenant:
        raise HTTPException(
            status_code=500, detail="Could not create default tenant"
        )

    # The very first user in the system bootstraps as SYSTEM_ADMIN +
    # ADMIN. Later bootstrap registrations (their own household tenant)
    # become ADMIN of their new tenant.
    role = "SYSTEM_ADMIN" if is_first_user else "ADMIN"

    # Insert via the request's session so the advisory lock actually
    # covers the write (service_create_user opens its own session, which
    # would race). Inline the minimal create_user logic here.
    try:
        user_role = Role(role)
    except ValueError:
        user_role = Role.ADMIN

    new_user_obj = UserModel(
        email=user_data.email,
        hashed_password=hashed_password,
        tenant_id=str(new_tenant.id),
        role=user_role,
        settings={},
    )
    db.add(new_user_obj)
    await db.commit()
    await db.refresh(new_user_obj)
    return new_user_obj


@router.post("/invite")
async def create_invite(
    tenant_id: str | None = None,
    email: str | None = None,
    role: str = Role.USER.value,
    expires_days: int = 7,
    current_user: TokenData = Depends(get_current_user),
):
    """Mint a tenant invite token.

    Admin/Manager/System-admin only. The token is scoped to the caller's
    tenant (the ``tenant_id`` query param, if supplied, must match it).
    Cannot grant SYSTEM_ADMIN — that role is bootstrap-only.
    """
    if current_user.role not in (
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators may issue invite tokens.",
        )

    target_tenant = tenant_id or str(current_user.tenant_id)
    if str(current_user.tenant_id) != target_tenant and current_user.role != Role.SYSTEM_ADMIN.value:
        # Non-SYSTEM_ADMIN can only invite into their own tenant.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot issue invites for a different tenant.",
        )

    if role == Role.SYSTEM_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SYSTEM_ADMIN cannot be granted via invite. Use the bootstrap path.",
        )

    token = create_invite_token(
        tenant_id=target_tenant,
        email=email,
        role=role,
        expires_days=expires_days,
    )
    return {
        "invite_token": token,
        "tenant_id": target_tenant,
        "role": role,
        "expires_in_days": expires_days,
    }


@router.get("/validate")
async def validate_token(user_id: str = Depends(get_current_user_id)):
    """Validate current token"""
    return {"valid": True, "user_id": user_id}


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(token_data: TokenRefresh):
    """Refresh access token"""
    payload = decode_access_token(token_data.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    access_token = create_access_token(
        data={
            "sub": payload.get("sub"),
            "user_id": payload.get("user_id"),
            "tenant_id": payload.get("tenant_id"),
            "role": payload.get("role"),
        },
        expires_delta=access_token_expires,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=token_data.refresh_token,
        token_type="bearer",
        expires_in=int(access_token_expires.total_seconds()),
    )
