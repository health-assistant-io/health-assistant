"""Self-service passkey management (program 12C2).

Mounted under ``/me/mfa/passkeys`` next to the TOTP surface. Every
state-changing call requires a fresh step-up proof for ``mfa_change``
(password/TOTP or an existing passkey assertion), so a stolen live session
cannot silently enroll a new authenticator or delete the account's factors.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import get_current_user
from app.schemas.passkeys import (
    PasskeyListResponse,
    PasskeyOptionsResponse,
    PasskeyRegistrationVerifyRequest,
    PasskeyResponse,
)
from app.schemas.user import TokenData
from app.services.audit.audit_service import OUTCOME_DENIED, log_audit_action
from app.services.auth import passkeys, security_policy, step_up
from app.services.tenancy.user_service import get_user_by_id

router = APIRouter(prefix="/me/mfa/passkeys", tags=["mfa"])


async def _live_user(current_user: TokenData):
    user = await get_user_by_id(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("", response_model=PasskeyListResponse)
async def list_my_passkeys(current_user: TokenData = Depends(get_current_user)):
    """The caller's registered passkeys (settings card)."""
    rows = await passkeys.list_credentials(current_user.user_id)
    return PasskeyListResponse(items=[PasskeyResponse(**row.to_dict()) for row in rows])


@router.post("/options", response_model=PasskeyOptionsResponse)
async def passkey_registration_options(current_user: TokenData = Depends(get_current_user)):
    """Creation options — requires a recent ``mfa_change`` step-up proof."""
    await step_up.enforce(current_user, step_up.StepUpAction.MFA_CHANGE)
    user = await _live_user(current_user)
    return PasskeyOptionsResponse(**await passkeys.begin_registration(user))


@router.post("/verify", response_model=PasskeyResponse)
async def verify_passkey_registration(
    payload: PasskeyRegistrationVerifyRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """Verify the attestation, store the credential and return it."""
    await step_up.enforce(current_user, step_up.StepUpAction.MFA_CHANGE)
    user = await _live_user(current_user)
    row = await passkeys.finish_registration(user, payload.credential, name=payload.name)
    return PasskeyResponse(**row.to_dict())


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_passkey(
    credential_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Delete one credential — refused when it would drop the last factor.

    ``mfa_enforced`` and a tenant policy that lists the caller's role both
    make MFA non-optional; removing the last remaining factor would void
    that requirement at the next sign-in, so it is refused. Removing one of
    several factors is allowed.
    """
    await step_up.enforce(current_user, step_up.StepUpAction.MFA_CHANGE)
    user = await _live_user(current_user)
    rows = await passkeys.list_credentials(user.id)
    target = next((row for row in rows if str(row.id) == str(credential_id)), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")
    remaining = [row for row in rows if row.id != target.id and not row.disabled]
    policy = await security_policy.tenant_security_policy_for(user.tenant_id)
    requirement = bool(getattr(user, "mfa_enforced", False)) or policy.blocks_mfa_disable(user.role)
    if requirement and not remaining and not getattr(user, "mfa_secret_enc", None):
        await log_audit_action(
            required=True,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="mfa.passkey_delete",
            resource_type="auth",
            resource_id=user.id,
            outcome=OUTCOME_DENIED,
            new_value={"cause": "last_factor", "credential_id": str(target.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "MFA is required on this account and this is the last factor; "
                "enroll another factor before deleting it."
            ),
        )
    if not await passkeys.delete_credential(user, target.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")
