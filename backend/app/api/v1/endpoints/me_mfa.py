"""Self-service TOTP MFA (plan 16 H5, identity-auth §12 path style).

Mounted under ``/me`` to match the identity self-service neighbor
``/me/sessions`` (the ``users`` router's ``/users/me`` is the profile
read; session/account self-actions live on ``/me/*``):

* ``GET    /me/mfa``        — status (enabled / enforced / pending)
* ``POST   /me/mfa/enroll`` — one-time provisioning (secret + otpauth
  URI + recovery codes); stays **pending** until confirmed
* ``POST   /me/mfa/confirm``— code check against the pending secret ⇒
  active
* ``DELETE /me/mfa``        — password-confirmed removal; refused while
  ``mfa_enforced`` or while the tenant security policy requires MFA for
  the role (neither requirement is self-cancellable)

Enrollment itself is a factor change and therefore step-up gated
(``mfa_change``, program 12C1): TOTP is an accepted step-up method, so an
ungated enroll would let a stolen bearer mint proofs for export, role
changes, break-glass and admin grants. The pre-session forced-enrollment
door (``POST /auth/mfa/enroll``, driven by the login challenge token) is a
different surface and stays unaffected.

Every state change lands in ``audit_events`` via ``log_audit_action``
(the H2 chokepoint) with an outcome.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import get_current_user, verify_password
from app.schemas.auth import (
    MFAConfirmRequest,
    MFADisableRequest,
    MFAEnrollResponse,
    MFAStatusResponse,
)
from app.schemas.user import TokenData
from app.services.audit.audit_service import (
    OUTCOME_DENIED,
    OUTCOME_OK,
    log_audit_action,
)
from app.services.auth import (
    login_notifications,
    mfa_service,
    passkeys,
    security_policy,
    step_up,
)
from app.services.tenancy.user_service import get_user_by_id

router = APIRouter(prefix="/me", tags=["mfa"])


async def _live_user(current_user: TokenData):
    """The live row behind the session (is_active/ver already verified)."""
    user = await get_user_by_id(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/mfa", response_model=MFAStatusResponse)
async def get_my_mfa(current_user: TokenData = Depends(get_current_user)):
    """MFA status for the caller — drives the settings-page card."""
    user = await _live_user(current_user)
    return MFAStatusResponse(**await mfa_service.status_for(user))


@router.post("/mfa/enroll", response_model=MFAEnrollResponse)
async def enroll_my_mfa(current_user: TokenData = Depends(get_current_user)):
    """Begin (or restart) enrollment — one-time provisioning payload.

    Returns the base32 secret, the ``otpauth://`` URI (the frontend
    renders the QR from it) and 8 single-use recovery codes. The
    enrollment is **pending** until ``POST /me/mfa/confirm`` supplies a
    matching code; calling enroll again simply replaces the pending
    secret (e.g. a abandoned wizard). Active MFA must be disabled first
    (this is enrollment, not re-enrollment).

    Step-up gated (``mfa_change``): adding a factor changes what can mint
    future step-up proofs, so a stolen bearer alone cannot enroll one.
    """
    await step_up.enforce(current_user, step_up.StepUpAction.MFA_CHANGE)
    user = await _live_user(current_user)
    if getattr(user, "mfa_secret_enc", None):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already active on this account. Disable it first to re-enroll.",
        )
    data = await mfa_service.begin_enrollment(user)
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="mfa.enroll",
        resource_type="auth",
        resource_id=user.id,
        outcome=OUTCOME_OK,
    )
    return MFAEnrollResponse(**data)


@router.post("/mfa/confirm", response_model=MFAStatusResponse)
async def confirm_my_mfa(
    payload: MFAConfirmRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """Activate the pending enrollment (code check ⇒ active).

    Step-up gated like ``enroll`` — otherwise the pending secret could be
    completed by whoever holds the stolen session.
    """
    await step_up.enforce(current_user, step_up.StepUpAction.MFA_CHANGE)
    user = await _live_user(current_user)
    if not await mfa_service.confirm_enrollment(user.id, payload.code):
        await log_audit_action(
            required=True,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="mfa.confirm",
            resource_type="auth",
            resource_id=user.id,
            outcome=OUTCOME_DENIED,
            new_value={"cause": "invalid_code"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code — MFA was not activated.",
        )
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="mfa.confirm",
        resource_type="auth",
        resource_id=user.id,
        outcome=OUTCOME_OK,
    )
    fresh = await _live_user(current_user)
    await login_notifications.notify_security_event(
        fresh,
        event="totp_added",
        title="Two-factor authentication was added to your account",
        body=(
            "An authenticator app can now sign in to your account with a "
            "one-time code. If this was not you, remove it in "
            "Settings → Security."
        ),
        dedup=str(fresh.id),
    )
    return MFAStatusResponse(**await mfa_service.status_for(fresh))


@router.delete("/mfa", response_model=MFAStatusResponse)
async def disable_my_mfa(
    payload: MFADisableRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """Disable MFA — password-confirmed, refused while admin-forced.

    ``mfa_enforced`` accounts cannot self-disable: the admin requirement
    would be void the moment the user typed their password. Existing
    sessions stay valid either way (MFA gates **login**, not live
    sessions — §9 Bearer clients are unaffected by design).
    """
    user = await _live_user(current_user)
    if not getattr(user, "mfa_secret_enc", None) and not getattr(user, "mfa_pending", None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not active on this account.",
        )
    others = await passkeys.active_credentials(user.id)
    policy = await security_policy.tenant_security_policy_for(user.tenant_id)
    required = bool(getattr(user, "mfa_enforced", False)) or policy.blocks_mfa_disable(user.role)
    if required and not others:
        await log_audit_action(
            required=True,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="mfa.disable",
            resource_type="auth",
            resource_id=user.id,
            outcome=OUTCOME_DENIED,
            new_value={"cause": "mfa_enforced"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "MFA is required by policy on this account and this is the last "
                "factor; enroll a passkey before disabling TOTP, or contact your "
                "administrator."
            ),
        )
    stored_hash = getattr(user, "password_hash", None)
    if not stored_hash or not verify_password(payload.password, stored_hash):
        await log_audit_action(
            required=True,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="mfa.disable",
            resource_type="auth",
            resource_id=user.id,
            outcome=OUTCOME_DENIED,
            new_value={"cause": "invalid_password"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password.",
        )
    await mfa_service.clear_mfa(user.id)
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="mfa.disable",
        resource_type="auth",
        resource_id=user.id,
        outcome=OUTCOME_OK,
    )
    fresh = await _live_user(current_user)
    return MFAStatusResponse(**await mfa_service.status_for(fresh))
