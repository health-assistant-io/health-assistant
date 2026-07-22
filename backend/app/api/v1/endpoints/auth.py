"""Authentication endpoints — login, register, invite, first-run setup.

Post-fix contract:
1. **First-run setup (POST /auth/setup)** — the only bootstrap path.
   Creates the initial tenant + SYSTEM_ADMIN. Only callable while the
   system is uninitialized (zero users). Protected by a one-time setup
   token (from the backend logs) for non-localhost / non-dev requests,
   closing the first-claim race for internet-exposed instances.
2. **Join existing tenant (POST /auth/register with tenant_id +
   invite_token)** — verifies the tenant exists AND that ``invite_token``
   is a valid JWT signed with ``SECRET_KEY``, scoped to that tenant. 403
   otherwise. The role in the token wins; SYSTEM_ADMIN is never granted
   via this path.
3. **Invite issuance (POST /auth/invite)** — ADMIN+ only. Mints a 7-day
   token scoped to the caller's tenant.

Audit item B7: ``POST /auth/register`` previously accepted any
``tenant_id`` with no check that the caller was authorized to join, and
the first-user SYSTEM_ADMIN check used an unlocked ``COUNT(*)``. Both
fixed: invite-token verification + the advisory-lock bootstrap (now in
``/auth/setup``).
"""

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.core.security import (
    RoleChecker,
    REFRESH_TOKEN_DAYS,
    create_access_token,
    create_invite_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    get_current_user,
    get_password_hash,
    get_current_user_id,
    verify_invite_token,
    verify_password,
)
from app.core import token_store
from app.core import setup_token
from app.models.enums import Role
from app.models.user_model import UserModel
from app.schemas.auth import (
    SetupRequest,
    SetupStatus,
    TokenRefresh,
    TokenResponse,
    UserRegister,
)
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


async def _is_initialized(db: AsyncSession) -> bool:
    """True once at least one user row exists."""
    result = await db.execute(select(func.count()).select_from(UserModel))
    return (result.scalar() or 0) > 0


@router.get("/setup-status", response_model=SetupStatus)
async def setup_status(request: Request, db: AsyncSession = Depends(get_db)):
    """First-run status — drives the frontend's login-vs-setup decision.

    No auth: the frontend must be able to call this before any user exists.
    ``initialized`` reflects whether a SYSTEM_ADMIN has been created (via
    the wizard, the CLI script, or the legacy register path).
    ``setup_token_required`` tells the wizard whether to collect the
    one-time setup token printed in the backend logs (skipped for
    localhost / dev).
    """
    initialized = await _is_initialized(db)
    return SetupStatus(
        initialized=initialized,
        setup_token_required=(
            False if initialized else setup_token.is_setup_token_required(request)
        ),
    )


@router.post("/setup", response_model=TokenResponse)
async def setup(
    payload: SetupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit("register", max_requests=5, window=60)),
):
    """First-run setup wizard endpoint.

    Creates the initial tenant + SYSTEM_ADMIN and returns login tokens so
    the caller is immediately authenticated. Only callable while the
    system is uninitialized. Protected by the one-time setup token (from
    the backend logs) for non-localhost / non-dev requests — closes the
    first-claim race for internet-exposed instances.

    Replaces the old ``POST /auth/register`` no-tenant_id bootstrap path
    (moved here so registration can be locked down to invite-only).
    """
    if await _is_initialized(db):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "This instance is already initialized. New accounts must be "
                "created by an admin via an invite token (POST /auth/invite)."
            ),
        )

    # Setup-token guardrail (skipped for localhost / dev).
    if setup_token.is_setup_token_required(request):
        if not setup_token.validate(payload.setup_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "A setup token is required for first-run setup. Retrieve it "
                    "from the backend container logs: "
                    "`docker compose ... logs backend | grep -i 'setup token'`."
                ),
            )

    if await get_user_by_email(payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    hashed_password = get_password_hash(payload.password)

    # Race-protected bootstrap: the advisory lock serializes the count +
    # insert so two concurrent setup attempts cannot both succeed. Same
    # pattern as the old register bootstrap path.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": _BOOTSTRAP_ADVISORY_KEY}
    )

    # Re-check inside the lock — a concurrent setup may have initialized
    # while we waited.
    if await _is_initialized(db):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This instance was just initialized by another request.",
        )

    new_tenant = await create_tenant(name=payload.tenant_name)
    if not new_tenant:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create the initial tenant.",
        )

    new_user_obj = UserModel(
        email=payload.email,
        hashed_password=hashed_password,
        tenant_id=str(new_tenant.id),
        role=Role.SYSTEM_ADMIN,
        settings={"is_initial_admin": True},
    )
    db.add(new_user_obj)
    await db.commit()
    await db.refresh(new_user_obj)

    # Invalidate the one-time token — the system is now initialized.
    setup_token.clear()

    # Issue login tokens (mirrors /auth/login) so the caller is signed in.
    access_token_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    token_claims = {
        "sub": new_user_obj.email,
        "user_id": str(new_user_obj.id),
        "tenant_id": str(new_user_obj.tenant_id),
        "role": Role.SYSTEM_ADMIN.value,
    }
    access_token = create_access_token(
        data=token_claims, expires_delta=access_token_expires
    )
    refresh_token_expires = timedelta(days=REFRESH_TOKEN_DAYS)
    refresh_token, jti = create_refresh_token(
        data=token_claims, expires_delta=refresh_token_expires
    )
    await token_store.register_refresh(
        str(new_user_obj.id), jti, int(refresh_token_expires.total_seconds())
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=int(access_token_expires.total_seconds()),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    _rl=Depends(rate_limit("login", max_requests=20, window=60)),
):
    """Authenticate user and return tokens"""
    user = await get_user_by_email(form_data.username)

    if not user or not verify_password(
        form_data.password, getattr(user, "hashed_password", "")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if getattr(user, "is_service_account", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Service accounts cannot use password login.",
        )

    access_token_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    token_claims = {
        "sub": user.email,
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": getattr(user.role, "value", user.role),
    }
    access_token = create_access_token(
        data=token_claims,
        expires_delta=access_token_expires,
    )

    # Refresh tokens are typed + jti-tracked so they can be rotated/revoked
    # (audit A5). The jti is registered server-side; /auth/refresh replaces
    # it with a fresh one on each use.
    refresh_token_expires = timedelta(days=REFRESH_TOKEN_DAYS)
    refresh_token, jti = create_refresh_token(
        data=token_claims,
        expires_delta=refresh_token_expires,
    )
    await token_store.register_refresh(
        str(user.id), jti, int(refresh_token_expires.total_seconds())
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=int(access_token_expires.total_seconds()),
    )


@router.post("/register", response_model=UserResponse)
async def register(
    user_data: UserRegister,
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit("register", max_requests=5, window=60)),
):
    """Register a new user into an existing tenant (invite-only).

    The open bootstrap path (no ``tenant_id``) was removed — first-run
    provisioning now goes through ``POST /auth/setup`` (the browser
    wizard) or the ``create_system_admin.py`` CLI. Every registration
    here requires a ``tenant_id`` plus a valid invite token minted by
    that tenant's admin via ``POST /auth/invite``.
    """
    user = await get_user_by_email(user_data.email)
    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    if not user_data.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A tenant_id and a valid invite token are required to register. "
                "If this is a fresh install, use the first-run setup wizard "
                "(POST /auth/setup) instead."
            ),
        )

    hashed_password = get_password_hash(user_data.password)

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


@router.post("/invite")
async def create_invite(
    tenant_id: str | None = None,
    email: str | None = None,
    role: str = Role.USER.value,
    expires_days: int = 7,
    current_user: TokenData = Depends(get_current_user),
    _rl=Depends(rate_limit("invite", max_requests=10, window=60)),
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
    if (
        str(current_user.tenant_id) != target_tenant
        and current_user.role != Role.SYSTEM_ADMIN.value
    ):
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
async def refresh_token(
    token_data: TokenRefresh,
    _rl=Depends(rate_limit("refresh", max_requests=30, window=60)),
):
    """Refresh access token (with rotation — audit A5).

    The presented refresh token's ``jti`` must be active server-side. On
    success a NEW refresh token is issued and the old ``jti`` is revoked, so a
    stolen refresh token stops working the moment the legitimate user refreshes
    (rotation), and logout/``revoke_refresh`` can invalidate a token early.
    """
    payload = decode_refresh_token(token_data.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("user_id")
    jti = payload.get("jti")
    if not user_id or not jti or not await token_store.is_active(user_id, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_claims = {
        "sub": payload.get("sub"),
        "user_id": user_id,
        "tenant_id": payload.get("tenant_id"),
        "role": payload.get("role"),
    }
    access_token_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    access_token = create_access_token(
        data=token_claims,
        expires_delta=access_token_expires,
    )

    # Rotate: revoke the consumed jti and issue a fresh one.
    await token_store.revoke_refresh(user_id, jti)
    refresh_token_expires = timedelta(days=REFRESH_TOKEN_DAYS)
    new_refresh, new_jti = create_refresh_token(
        data=token_claims,
        expires_delta=refresh_token_expires,
    )
    await token_store.register_refresh(
        user_id, new_jti, int(refresh_token_expires.total_seconds())
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=int(access_token_expires.total_seconds()),
    )


@router.post("/logout")
async def logout(
    token_data: TokenRefresh,
    current_user: TokenData = Depends(get_current_user),
):
    """Revoke the presented refresh token (audit A5).

    Access tokens are stateless JWTs and cannot be revoked without a blocklist
    (their short lifetime is the mitigation); this revokes the refresh token so
    no new access tokens can be minted from it.
    """
    payload = decode_refresh_token(token_data.refresh_token)
    if payload and payload.get("user_id") and payload.get("jti"):
        await token_store.revoke_refresh(payload["user_id"], payload["jti"])
    return {"revoked": True}


@router.post("/logout-all")
async def logout_all(
    current_user: TokenData = Depends(get_current_user),
):
    """Revoke every refresh token for the current user (audit A5)."""
    count = await token_store.revoke_all_refresh(current_user.user_id)
    return {"revoked": count}


# ---------------------------------------------------------------------------
# F19: Service-account token minting (tenant bridge for non-SMART clients)
# ---------------------------------------------------------------------------


@router.post("/service-account")
async def create_service_account(
    instance_name: str,
    tenant_id: Optional[str] = None,
    expires_days: int = 90,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),
):
    """Mint a long-lived service-account JWT for external system interop (F19).

    Creates a ``UserModel`` row with ``is_service_account=True`` (no password)
    and returns a JWT carrying ``is_service_account=True`` +
    ``client_id=<instance_name>``. The token works as a Bearer against the
    FHIR facade (``/fhir/R4/*``) and the REST API for the service account's
    tenant. SYSTEM_ADMIN can override the tenant via the ``X-Tenant`` header
    (see Phase 8 commit 8.2).

    ADMIN/MANAGER can only mint for their own tenant; SYSTEM_ADMIN can mint
    for any tenant by passing ``tenant_id``.
    """
    from uuid import uuid4

    target_tenant = tenant_id or str(current_user.tenant_id)
    sa_email = f"sa-{uuid4()}@service-account.local"

    sa_user = UserModel(
        email=sa_email,
        hashed_password=None,
        role=Role.USER,
        tenant_id=target_tenant,
        is_active=True,
        is_service_account=True,
    )
    db.add(sa_user)
    await db.flush()

    expires_delta = timedelta(days=min(max(expires_days, 1), 365))
    claims = {
        "sub": sa_email,
        "user_id": str(sa_user.id),
        "tenant_id": target_tenant,
        "role": Role.USER.value,
        "is_service_account": True,
        "client_id": instance_name,
    }
    access_token = create_access_token(claims, expires_delta=expires_delta)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "tenant_id": target_tenant,
        "client_id": instance_name,
        "expires_in_days": min(max(expires_days, 1), 365),
    }
