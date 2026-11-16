from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.core.security import (
    RoleChecker,
    get_current_user,
    get_password_hash,
    verify_password,
)
from app.models.enums import ErasureSubjectType, Role
from app.models.user_model import UserModel
from app.schemas.user import (
    PasswordResetRequest,
    SelfDeleteRequest,
    TokenData,
    UserCreate,
    UserResponse,
)
from app.services.audit.audit_service import log_audit_action
from app.services.auth import passkeys, step_up
from app.services.data_lifecycle.erasure import ErasureError, resolve_subject, run_erasure
from app.services.tenancy.user_service import (
    bump_token_version,
    create_user,
    get_user_by_email,
    get_user_by_id,
    reset_login_failures,
    set_login_failures,
    update_user,
)

router = APIRouter(prefix="/users", tags=["users"])


async def _factor_aware_shape(user, passkey_count: int | None = None) -> UserResponse:
    """Serialise a user with factor-aware MFA flags (TOTP OR passkey)."""
    count = (
        passkey_count
        if passkey_count is not None
        else (await passkeys.count_active_by_user([user.id])).get(user.id, 0)
    )
    return UserResponse.model_validate(user).model_copy(
        update={
            "mfa_enabled": bool(getattr(user, "mfa_secret_enc", None)) or count > 0,
            "passkey_count": count,
        }
    )


@router.get("", response_model=list[UserResponse])
async def list_tenant_users(
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.MANAGER])),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
    db: AsyncSession = Depends(get_db),
):
    """List all users in the current tenant"""
    # System admins see everyone? Or just their own tenant?
    # Let's start with tenant isolation even for admins.
    query = select(UserModel).where(UserModel.tenant_id == current_user.tenant_id)

    # If they are SYSTEM_ADMIN, maybe they want to see everyone?
    # For now, let's keep it per-tenant as requested for "Tenant Admins".

    result = await db.execute(query)
    users = list(result.scalars().all())
    counts = await passkeys.count_active_by_user([user.id for user in users])
    return [await _factor_aware_shape(user, counts.get(user.id, 0)) for user in users]


def _user_snapshot(user) -> dict[str, Any] | None:
    """The audit-relevant fields of a user row (§17 admin-action diffs)."""
    if user is None:
        return None
    role = getattr(user, "role", None)
    return {
        "email": getattr(user, "email", None),
        "role": getattr(role, "value", role),
        "is_active": bool(getattr(user, "is_active", True)),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
    }


@router.post("", response_model=UserResponse)
async def create_user_endpoint(
    user_in: UserCreate,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
    db: AsyncSession = Depends(get_db),
):
    """Create a new user within a tenant"""
    # SYSTEM_ADMIN is bootstrap-only: a tenant ADMIN must never be able to
    # mint a platform operator (audit 2026-08 C-2). Only an existing
    # SYSTEM_ADMIN may create another one.
    if user_in.role == Role.SYSTEM_ADMIN.value and (
        current_user.role != Role.SYSTEM_ADMIN.value or user_in.tenant_id is not None
    ):
        raise HTTPException(
            status_code=403,
            detail="SYSTEM_ADMIN accounts can only be created by a SYSTEM_ADMIN.",
        )

    # Verify email doesn't exist
    existing_user = await get_user_by_email(user_in.email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Enforce tenant isolation
    tenant_id = user_in.tenant_id
    if current_user.role != Role.SYSTEM_ADMIN.value or tenant_id is None:
        # Non-system admins or system admins not providing a tenant_id
        # default to the current user's tenant.
        tenant_id = current_user.tenant_id

    hashed_password = get_password_hash(user_in.password)

    # Same-transaction audit (S1): the §17 row rides the request session and
    # commits with the user — a failed audit write rolls the create back.
    new_user = await create_user(
        email=user_in.email,
        password_hash=hashed_password,
        tenant_id=tenant_id,
        role=user_in.role,
        full_name=user_in.full_name,
        session=db,
    )
    await log_audit_action(
        required=True,
        session=db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="user.create",
        resource_type="user",
        resource_id=getattr(new_user, "id", None),
        new_value=_user_snapshot(new_user),
    )
    await db.commit()

    return new_user


@router.get("/me", response_model=UserResponse)
async def get_current_user_endpoint(
    current_user: TokenData = Depends(get_current_user),
):
    """Get current user information.

    For a switched SYSTEM_ADMIN the JWT's ``tenant_id`` is the target
    tenant, but the user row lives in the admin's original tenant. We
    therefore look up by ``original_tenant_id`` when switched, and skip
    the tenant filter entirely for non-switched SYSTEM_ADMIN (whose user
    row is in their own tenant).
    """
    if current_user.role == Role.SYSTEM_ADMIN.value:
        lookup_tenant = (
            current_user.original_tenant_id if getattr(current_user, "switched", False) else None
        )
    else:
        lookup_tenant = current_user.tenant_id

    user = await get_user_by_id(current_user.user_id, tenant_id=lookup_tenant)
    if not user:
        raise HTTPException(status_code=404, detail="User not found in database")

    return await _factor_aware_shape(user)


@router.delete("/me")
async def delete_current_user_endpoint(
    payload: SelfDeleteRequest,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit("self_delete", max_requests=5, window=60, bucket="auth")),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
):
    """Self-service account erasure (``USER`` deletes self).

    Destructive and irreversible, so identity-auth §7/§12 require the
    account password (with the shared login lockout) — a hijacked session
    alone must not be able to erase the account and every owned patient.
    Runs the same ordered cascade as admin erasure and revokes every token
    family. Dry-run first at ``POST /data-lifecycle/erasures``.
    """
    from app.core import token_store

    if current_user.user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await get_user_by_id(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(payload.password, user.password_hash or ""):
        failed = int(user.failed_login_attempts or 0) + 1
        locked = user.locked_until
        if failed >= settings.HA_AUTH_LOCKOUT_THRESHOLD:
            locked = datetime.now(UTC) + timedelta(minutes=settings.HA_AUTH_LOCKOUT_MINUTES)
        await set_login_failures(user.id, failed, locked)
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    await reset_login_failures(user.id)

    if user.role == Role.SYSTEM_ADMIN.value:
        remaining = await db.execute(
            select(func.count(UserModel.id)).where(
                UserModel.role == Role.SYSTEM_ADMIN.value,
                UserModel.is_active.is_(True),
                UserModel.id != user.id,
            )
        )
        if int(remaining.scalar_one() or 0) == 0:
            raise HTTPException(
                status_code=409,
                detail="The last SYSTEM_ADMIN cannot erase their own account.",
            )

    try:
        request = await run_erasure(
            db,
            subject_type=ErasureSubjectType.USER,
            subject_id=current_user.user_id,
            requested_by=current_user.user_id,
            dry_run=False,
        )
    except ErasureError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.detail},
        ) from exc
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="user.self_delete",
        resource_type="user",
        resource_id=str(current_user.user_id),
    )
    await token_store.revoke_everything(str(current_user.user_id))
    return {"message": "Account erased", "erasure_id": str(request.id)}


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_endpoint(user_id: str, current_user: TokenData = Depends(get_current_user)):
    """Get user information"""
    # Check permissions: Either you are the user, or you are an admin/manager in the same tenant
    # or you are a SYSTEM_ADMIN.

    is_self = str(current_user.user_id) == user_id
    is_admin = current_user.role in [
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ]

    # Enforce tenant isolation for non-system admins
    tenant_id = None if current_user.role == Role.SYSTEM_ADMIN.value else current_user.tenant_id

    user = await get_user_by_id(user_id, tenant_id=tenant_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Not authorized to view this user")

    return await _factor_aware_shape(user)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user_endpoint(
    user_id: str,
    email: str | None = None,
    role: str | None = None,
    settings: dict[str, Any] | None = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a user account (admin surface, ADR 0054).

    The central route gate requires ``user:role_assign @ tenant`` — held by
    ADMIN and SYSTEM_ADMIN only — so account administration is not a
    self-service surface: profile edits go through ``/settings/user``. The
    handler re-asserts the admin fact at its door as defense-in-depth (a
    redundant check, not a compat shim), keeping MANAGER out of the allowed
    set: MANAGERs manage content, not accounts.
    """
    from app.core import token_store

    if current_user.role not in (Role.ADMIN.value, Role.SYSTEM_ADMIN.value):
        raise HTTPException(status_code=403, detail="Not authorized to update this user")

    # Enforce tenant isolation for non-system admins
    tenant_id = None if current_user.role == Role.SYSTEM_ADMIN.value else current_user.tenant_id

    # If updating role, must be a real admin (MANAGERs manage content, not
    # accounts), and SYSTEM_ADMIN grants require a SYSTEM_ADMIN caller
    # (audit 2026-08 C-2 — tenant ADMIN/MANAGER must not escalate).
    if role:
        if current_user.role not in (Role.ADMIN.value, Role.SYSTEM_ADMIN.value):
            raise HTTPException(status_code=403, detail="Only admins can change roles")
        if role == Role.SYSTEM_ADMIN.value and current_user.role != Role.SYSTEM_ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="SYSTEM_ADMIN can only be granted by a SYSTEM_ADMIN.",
            )

    # Snapshot the pre-update state for the §17 audit diff (also gives
    # the 404 for a missing row before any mutation is attempted).
    before = await get_user_by_id(user_id, tenant_id=tenant_id)
    if not before:
        raise HTTPException(status_code=404, detail="User not found")

    # Privilege changes need fresh proof of presence (program 12C1): the
    # proof is bound to this sign-in family and consumed here, so a stolen
    # bearer cannot promote an account without the owner's password/TOTP.
    prior_role = getattr(before.role, "value", before.role)
    if role and role != prior_role:
        await step_up.enforce(current_user, step_up.StepUpAction.ROLE_CHANGE)

    # Same-transaction audit (S1): the change and its §17 row commit (or
    # roll back) together — a privilege change cannot land unrecorded.
    user = await update_user(
        user_id,
        email,
        role,
        settings,
        tenant_id=tenant_id,
        allow_system_admin=current_user.role == Role.SYSTEM_ADMIN.value,
        session=db,
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # §17: user updates are admin actions (role changes get their own
    # action so the trail answers "who changed privileges?" directly).
    prior_snapshot = _user_snapshot(before)
    new_snapshot = _user_snapshot(user)
    role_changed = prior_snapshot.get("role") != new_snapshot.get("role")
    await log_audit_action(
        required=True,
        session=db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="user.role_change" if role_changed else "user.update",
        resource_type="user",
        resource_id=user.id,
        old_value=prior_snapshot,  # gate-allow: *, audit diff
        new_value=new_snapshot,
    )
    await db.commit()

    # A role change must take effect immediately: bump ``token_version``
    # (every outstanding token fails its ``ver`` check — §8) and kill the
    # Redis hot layer so stale privileged claims cannot outlive the change
    # (audit 2026-08 H2). The demoted user simply re-logs-in.
    if role and str(user.id) != str(current_user.user_id):
        from app.services.tenancy.user_service import bump_token_version

        await bump_token_version(str(user.id))
        await token_store.revoke_everything(str(user.id))

    return user


@router.post("/{user_id}/reset-password", status_code=204)
async def reset_user_password_endpoint(
    user_id: str,
    payload: PasswordResetRequest,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN])),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
    db: AsyncSession = Depends(get_db),
):
    """Admin-initiated password reset (identity-auth §12).

    Sets a new password, then bumps ``token_version`` and revokes every
    token family so the old credential and its live sessions stop working
    immediately. A tenant ADMIN cannot reset a SYSTEM_ADMIN.
    """
    from app.core import token_store

    tenant_id = None if current_user.role == Role.SYSTEM_ADMIN.value else current_user.tenant_id
    target = await get_user_by_id(user_id, tenant_id=tenant_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == Role.SYSTEM_ADMIN.value and current_user.role != Role.SYSTEM_ADMIN.value:
        raise HTTPException(status_code=403, detail="Only SYSTEM_ADMIN may reset that account")

    # Same-transaction audit (S1): the reset and its §17 row commit together.
    updated = await update_user(
        user_id,
        password_hash=get_password_hash(payload.new_password),
        tenant_id=tenant_id,
        session=db,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")

    await log_audit_action(
        required=True,
        session=db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="user.password_reset",
        resource_type="user",
        resource_id=str(updated.id),
    )
    await db.commit()

    await bump_token_version(str(updated.id))
    await token_store.revoke_everything(str(updated.id))
    return None


@router.post("/{user_id}/force-logout")
async def force_logout_user_endpoint(
    user_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN])),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
):
    """Revoke every outstanding token for a user (identity-auth §12)."""
    from app.core import token_store

    tenant_id = None if current_user.role == Role.SYSTEM_ADMIN.value else current_user.tenant_id
    target = await get_user_by_id(user_id, tenant_id=tenant_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == Role.SYSTEM_ADMIN.value and current_user.role != Role.SYSTEM_ADMIN.value:
        raise HTTPException(status_code=403, detail="Only SYSTEM_ADMIN may revoke that account")

    await bump_token_version(str(target.id))
    await token_store.revoke_everything(str(target.id))
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="user.force_logout",
        resource_type="user",
        resource_id=str(target.id),
    )
    return {"revoked": True}


@router.delete("/{user_id}")
async def delete_user_endpoint(
    user_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
    db: AsyncSession = Depends(get_db),
):
    """Erase a user account and every record it owns (data-subject erasure)."""
    from app.core import token_store

    # Enforce tenant isolation for non-system admins
    tenant_id = None if current_user.role == Role.SYSTEM_ADMIN.value else current_user.tenant_id

    # Snapshot for the §17 audit trail before the row disappears.
    before = await get_user_by_id(user_id, tenant_id=tenant_id)
    if before is None:
        raise HTTPException(status_code=404, detail="User not found")
    if str(before.id) == str(current_user.user_id):
        # Account self-erasure is password-confirmed; the admin route has no
        # password body, so it must not be a side door.
        raise HTTPException(
            status_code=400,
            detail="Use DELETE /api/v1/users/me (password-confirmed) to erase your own account.",
        )

    try:
        subject = await resolve_subject(db, ErasureSubjectType.USER, before.id, tenant_id=tenant_id)
        request = await run_erasure(
            db,
            subject_type=ErasureSubjectType.USER,
            subject_id=subject.id,
            requested_by=current_user.user_id,
            dry_run=False,
        )
    except ErasureError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.detail},
        ) from exc

    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="user.delete",
        resource_type="user",
        resource_id=user_id,
        old_value=_user_snapshot(before),  # gate-allow: *, audit diff
    )

    # Deleted users must lose access at once — not when their tokens
    # happen to expire (audit 2026-08 H2).
    await token_store.revoke_everything(user_id)
    return {"message": "User deleted successfully", "erasure_id": str(request.id)}
