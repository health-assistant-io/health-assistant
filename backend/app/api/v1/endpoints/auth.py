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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import setup_token, token_store
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.core.security import (
    REFRESH_TOKEN_DAYS,
    _dummy_hash,
    create_invite_token,
    create_refresh_token,
    create_session_access_token,
    decode_refresh_token,
    get_current_user,
    get_current_user_id,
    get_password_hash,
    get_token,
    invite_jti,
    verify_access_token,
    verify_invite_token,
    verify_password,
)
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
)
from app.services.user_service import (
    get_user_by_email,
    get_user_by_id,
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


async def _issue_session_tokens(claims: dict) -> TokenResponse:
    """Mint a session access token + rotating refresh token pair.

    The access token's jti is registered in the session store so logout /
    user deletion / role changes can revoke it immediately; the refresh
    token's jti is registered for rotation + revocation.
    """
    access_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    access_token, access_jti = create_session_access_token(
        claims, expires_delta=access_expires
    )
    await token_store.register_session(
        claims["user_id"], access_jti, int(access_expires.total_seconds())
    )
    refresh_expires = timedelta(days=REFRESH_TOKEN_DAYS)
    refresh_token, refresh_jti = create_refresh_token(
        data=claims, expires_delta=refresh_expires
    )
    await token_store.register_refresh(
        claims["user_id"], refresh_jti, int(refresh_expires.total_seconds())
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=int(access_expires.total_seconds()),
    )


@router.get("/setup-status", response_model=SetupStatus)
async def setup_status(request: Request, db: AsyncSession = Depends(get_db)):
    """First-run status — drives the frontend's login-vs-setup decision.

    No auth: the frontend must be able to call this before any user exists.
    ``initialized`` reflects whether a SYSTEM_ADMIN has been created (via
    the wizard, the CLI script, or the legacy register path).
    ``setup_token_required`` tells the wizard whether to collect the
    one-time setup token (per-mode: see ``app/core/setup_token.py``).

    SECURITY: this endpoint never returns the setup token itself. In
    ``env`` mode the launcher already holds the token (it set it); it
    composes the one-click URL itself. Echoing the token here let any
    anonymous caller bootstrap the instance (audit 2026-08 C-1).
    """
    initialized = await _is_initialized(db)
    mode = setup_token.current_mode()
    token_required = (
        False if initialized else setup_token.is_setup_token_required(request)
    )
    return SetupStatus(
        initialized=initialized,
        setup_token_required=token_required,
        token_mode=mode,
        setup_url_hint=None,
        demo_mode=settings.DEMO_MODE,
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
                    "`docker compose ... logs backend | grep -i -A 1 'setup token'`."
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
    token_claims = {
        "sub": new_user_obj.email,
        "user_id": str(new_user_obj.id),
        "tenant_id": str(new_user_obj.tenant_id),
        "role": Role.SYSTEM_ADMIN.value,
    }
    return await _issue_session_tokens(token_claims)


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    _rl=Depends(rate_limit("login", max_requests=20, window=60)),
):
    """Authenticate user and return tokens"""
    user = await get_user_by_email(form_data.username)

    # Uniform timing + message: verify against a dummy hash when the user
    # does not exist so response time cannot enumerate accounts (audit
    # 2026-08 M1).
    stored_hash = (
        getattr(user, "hashed_password", "") if user is not None else _dummy_hash()
    )
    authenticated = user is not None and verify_password(
        form_data.password, stored_hash
    )
    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_claims = {
        "sub": user.email,
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": getattr(user.role, "value", user.role),
    }
    return await _issue_session_tokens(token_claims)


@router.post("/demo-login", response_model=TokenResponse)
async def demo_login(
    _rl=Depends(rate_limit("demo_login", max_requests=20, window=60)),
):
    """Credential-free login for the demo account.

    Only available when ``DEMO_MODE=true`` (returns 404 otherwise). Looks
    up the pre-seeded demo user (``DEMO_USER_EMAIL``) and issues access +
    refresh tokens stamped with a ``demo`` claim so the frontend can render
    the demo banner. The demo user + data are auto-seeded on boot by the
    lifespan (scripts/seed_demo.py).
    """
    if not settings.DEMO_MODE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo login is not enabled on this instance.",
        )

    user = await get_user_by_email(settings.DEMO_USER_EMAIL)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Demo user not found. Ensure DEMO_MODE seeding has completed "
                "(the demo user is created on backend startup)."
            ),
        )

    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo account is disabled.",
        )

    token_claims = {
        "sub": user.email,
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": getattr(user.role, "value", user.role),
        "demo": True,
    }
    return await _issue_session_tokens(token_claims)


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

    The invite is validated (and consumed — single-use) BEFORE the
    email-exists check, so an unauthenticated caller cannot use the
    "Email already registered" error to enumerate accounts without a
    valid invite (audit 2026-08 M2).
    """
    if not user_data.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A tenant_id and a valid invite token are required to register. "
                "If this is a fresh install, use the first-run setup wizard "
                "(POST /auth/setup) instead."
            ),
        )

    if not user_data.invite_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "An invite token is required to join an existing tenant. "
                "Ask the tenant administrator to issue one via POST /auth/invite."
            ),
        )

    # Joining an existing tenant — require a valid invite token.
    tenant = await get_tenant(user_data.tenant_id)
    if not tenant:
        # 404 (not 403) so we don't leak that the tenant exists but is
        # locked down — matches the rest of the API's posture.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
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

    # Single-use: atomically consume the invite's jti (Redis GETDEL
    # semantics). Fails closed when Redis is unavailable so an outage
    # cannot convert single-use invites into unlimited ones.
    invite_jti_value = invite_jti(user_data.invite_token)
    if invite_jti_value and not await token_store.consume_invite(invite_jti_value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite token has already been used.",
        )

    if await get_user_by_email(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    hashed_password = get_password_hash(user_data.password)

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
    """Mint a tenant invite token (single-use, TTL capped at 30 days).

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

    # Cap the TTL: an invite is a short-lived onboarding artifact, not a
    # standing credential (audit 2026-08 M3).
    expires_days = max(1, min(int(expires_days), 30))

    token, invite_jti_value = create_invite_token(
        tenant_id=target_tenant,
        email=email,
        role=role,
        expires_days=expires_days,
    )
    await token_store.register_invite(
        invite_jti_value, int(timedelta(days=expires_days).total_seconds())
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

    The presented refresh token's ``jti`` must be active server-side. The
    user row is re-loaded from the DB on every refresh (audit 2026-08 H2):
    a deleted or deactivated user is refused, and the new claims (email,
    tenant, role) are rebuilt from the database — never from the old
    token — so role changes and tenant moves take effect at the next
    refresh even if the old token predates them.

    Switched SYSTEM_ADMIN sessions preserve their switch metadata: the
    target tenant is re-validated to still exist and be active before the
    switched context is extended.
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

    user = await get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is disabled.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    db_role = getattr(user.role, "value", user.role)
    db_tenant_id = str(user.tenant_id)

    token_claims = {
        "sub": user.email,
        "user_id": str(user.id),
        "tenant_id": db_tenant_id,
        "role": db_role,
    }
    if payload.get("demo"):
        token_claims["demo"] = True

    if payload.get("switched"):
        # Preserve the tenant-switch context, but re-validate the target
        # tenant is still there. Privileges (role) still come from the DB.
        scoped_tenant_id = payload.get("scoped_tenant_id") or payload.get("tenant_id")
        target = await get_tenant(scoped_tenant_id)
        if target is None or not getattr(target, "is_active", True):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Switched session target tenant is gone; switch back and re-authenticate.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token_claims.update(
            {
                "tenant_id": str(target.id),
                "original_tenant_id": payload.get("original_tenant_id"),
                "original_user_id": payload.get("original_user_id"),
                "switched": True,
                "scoped_tenant_id": str(target.id),
            }
        )

    # Rotate: revoke the consumed jti (refresh + any session it maps to)
    # and issue a fresh pair.
    await token_store.revoke_refresh(user_id, jti)
    return await _issue_session_tokens(token_claims)


@router.post("/logout")
async def logout(
    token_data: TokenRefresh,
    current_user: TokenData = Depends(get_current_user),
    token: str = Depends(get_token),
):
    """Revoke the presented refresh token AND the caller's access token.

    The access token's ``jti`` (from the Authorization header) is deleted
    from the session store, so the bearer credential itself stops working
    immediately — not just at the next refresh (audit 2026-08 H3).
    """
    payload = decode_refresh_token(token_data.refresh_token)
    if payload and payload.get("user_id") and payload.get("jti"):
        await token_store.revoke_refresh(payload["user_id"], payload["jti"])
    access_payload = verify_access_token(token)
    if access_payload and access_payload.get("jti"):
        await token_store.revoke_session(
            str(access_payload["user_id"]), access_payload["jti"]
        )
    return {"revoked": True}


@router.post("/logout-all")
async def logout_all(
    current_user: TokenData = Depends(get_current_user),
):
    """Revoke every refresh + session access token for the current user."""
    count = await token_store.revoke_everything(current_user.user_id)
    return {"revoked": count}
