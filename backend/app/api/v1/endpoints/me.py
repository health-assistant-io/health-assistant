"""Self-service identity endpoints (identity-auth §12).

The only surface here is credential rotation: ``PATCH /me/password``.
It is deliberately separate from ``/users`` so the contract path is stable
and the self-service action is not mixed with tenant user administration.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from app.core import token_store
from app.core.config import settings
from app.core.rate_limit import rate_limit
from app.core.security import get_current_user, get_password_hash, verify_password
from app.schemas.user import PasswordChangeRequest, TokenData
from app.services.audit.audit_service import log_audit_action
from app.services.tenancy.user_service import (
    bump_token_version,
    get_user_by_id,
    reset_login_failures,
    set_login_failures,
    update_user,
)

router = APIRouter(prefix="/me", tags=["me"])


@router.patch("/password", status_code=204)
async def change_password_endpoint(
    payload: PasswordChangeRequest,
    current_user: TokenData = Depends(get_current_user),
    _rl=Depends(rate_limit("password_change", max_requests=5, window=60, bucket="auth")),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
):
    """Rotate the caller's password after re-authenticating (§7/§12).

    Wrong current passwords share the login lockout counter. On success the
    password hash is replaced and every outstanding token family is revoked,
    so a stolen credential or session stops working immediately. The caller
    must sign in again with the new password.
    """
    user = await get_user_by_id(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(payload.current_password, user.password_hash or ""):
        failed = int(user.failed_login_attempts or 0) + 1
        locked = user.locked_until
        if failed >= settings.HA_AUTH_LOCKOUT_THRESHOLD:
            locked = datetime.now(UTC) + timedelta(minutes=settings.HA_AUTH_LOCKOUT_MINUTES)
        await set_login_failures(user.id, failed, locked)
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    updated = await update_user(
        user.id,
        password_hash=get_password_hash(payload.new_password),
        tenant_id=user.tenant_id,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")

    await reset_login_failures(user.id)
    await bump_token_version(str(user.id))
    await token_store.revoke_everything(str(user.id))
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="user.password_change",
        resource_type="user",
        resource_id=str(user.id),
    )
    return None
