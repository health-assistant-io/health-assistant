# ruff: noqa: B008 -- Depends(rate_limit(...)) is the project pattern
"""Public passkey sign-in and passkey step-up ceremonies (program 12C2).

Two surfaces share this module:

* ``POST /auth/passkeys/options`` + ``/verify`` — unauthenticated passkey
  sign-in (public-auth class), email-named or usernameless/discoverable. A
  successful assertion satisfies the tenant IP allowlist and MFA policy (a
  user-verified passkey is possession + presence) and issues an ordinary
  session through :func:`issue_session`; ``auth_mode`` stays the contract's
  closed ``password`` local value and ``amr`` records ``hwk``/``swk`` +
  ``user``.
* ``POST /auth/step-up/passkeys/options`` + ``/verify`` — the same assertion
  bound to the caller's session family and one closed step-up action. It
  records the ordinary ``stepup:{fid}:{action}`` proof with
  ``methods: ["webauthn"]``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core import tenant_context
from app.core.rate_limit import check_account_limit, rate_limit
from app.core.security import AUTH_MODE_PASSWORD, get_current_user
from app.schemas.auth import StepUpResponse, TokenResponse
from app.schemas.passkeys import (
    PasskeyAuthenticationOptionsRequest,
    PasskeyAuthenticationVerifyRequest,
    PasskeyOptionsResponse,
    PasskeyStepUpOptionsRequest,
    PasskeyStepUpOptionsResponse,
    PasskeyStepUpVerifyRequest,
)
from app.schemas.user import TokenData
from app.services.audit.audit_service import OUTCOME_DENIED, log_audit_action
from app.services.auth import (
    login_notifications,
    passkeys,
    security_policy,
    step_up,
)
from app.services.auth.auth_session_service import device_hint, issue_session
from app.services.tenancy.user_service import (
    get_user_by_email,
    normalize_email,
    reset_login_failures,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def _set_auth_cookies(response: Response, issued) -> None:
    from app.core.cookies import set_session_cookies

    set_session_cookies(
        response,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token or None,
    )


def _token_response(issued) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        token_type="bearer",
        expires_in=issued.access_expires_in,
    )


def _amr(credential) -> list[str]:
    """RFC 8176 methods for the minted session (passkey possession + presence)."""
    possession = "swk" if credential.backup_eligible else "hwk"
    return [possession, "user"]


@router.post("/passkeys/options", response_model=PasskeyOptionsResponse)
async def passkey_login_options(
    payload: PasskeyAuthenticationOptionsRequest,
    _rl=Depends(rate_limit("passkey_options", max_requests=20, window=60, bucket="auth")),
):
    """Assertion options for a passkey sign-in.

    An email names the account whose credentials are offered; omitting it
    starts a usernameless/discoverable ceremony. An unknown or inactive
    email gets the same options object with an empty ``allowCredentials``
    (no account enumeration) — the assertion then simply cannot resolve to
    that account.
    """
    email = normalize_email(payload.email) if payload.email else None
    if email:
        await check_account_limit("passkey_options", email)
    with tenant_context.system_scope("auth:passkey_options"):
        user = await get_user_by_email(email) if email else None
    if user is not None and not getattr(user, "is_active", False):
        user = None
    if user is not None:
        tenant_context.set_tenant(user.tenant_id, actor=str(user.id))
    return PasskeyOptionsResponse(**await passkeys.begin_authentication(user))


@router.post("/passkeys/verify", response_model=TokenResponse)
async def passkey_login_verify(
    payload: PasskeyAuthenticationVerifyRequest,
    request: Request,
    response: Response,
    _rl=Depends(rate_limit("passkey_verify", max_requests=20, window=60, bucket="auth")),
):
    """Verify the assertion and issue a session (MFA policy already satisfied)."""
    user, credential = await passkeys.finish_authentication(payload.credential)

    now = datetime.now(UTC)
    locked_until = getattr(user, "locked_until", None)
    if locked_until is not None:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=UTC)
        if locked_until > now:
            await _audit(
                user,
                "auth.login",
                OUTCOME_DENIED,
                {"detail": "account_locked", "method": "passkey"},
            )
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account locked; try again later",
            )

    policy = await security_policy.tenant_security_policy_for(user.tenant_id)
    client_ip = security_policy.request_client_ip(request)
    if not policy.allows_ip(client_ip):
        await _audit(
            user,
            "auth.login",
            OUTCOME_DENIED,
            {"detail": security_policy.DENY_IP, "method": "passkey"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=security_policy.DENY_IP,
        )

    await reset_login_failures(user.id)
    user_agent = request.headers.get("user-agent")
    issued = await issue_session(
        user,
        auth_mode=AUTH_MODE_PASSWORD,
        client_label=device_hint(user_agent) or "passkey",
        ip=client_ip,
        user_agent=user_agent,
        extra_claims={"amr": _amr(credential), "auth_time": int(now.timestamp())},
    )
    await _audit(user, "auth.login", "ok", {"detail": "passkey"})
    await login_notifications.notify_new_device(
        user,
        ip=client_ip,
        user_agent=user_agent,
        method="passkey",
        family_id=issued.family_id,
    )
    _set_auth_cookies(response, issued)
    return _token_response(issued)


@router.post("/step-up/passkeys/options", response_model=PasskeyStepUpOptionsResponse)
async def passkey_step_up_options(
    payload: PasskeyStepUpOptionsRequest,
    current_user: TokenData = Depends(get_current_user),
    _rl=Depends(rate_limit("passkey_step_up", max_requests=10, window=60, bucket="auth")),
):
    """Assertion options bound to this family and one step-up action."""
    action = _action(payload.action)
    _require_family(current_user, action)
    data = await passkeys.begin_step_up(current_user, action)
    return PasskeyStepUpOptionsResponse(**data)


@router.post("/step-up/passkeys/verify", response_model=StepUpResponse)
async def passkey_step_up_verify(
    payload: PasskeyStepUpVerifyRequest,
    current_user: TokenData = Depends(get_current_user),
    _rl=Depends(rate_limit("passkey_step_up", max_requests=10, window=60, bucket="auth")),
):
    """Verify the assertion and record the family-bound proof."""
    action = _action(payload.action)
    _require_family(current_user, action)
    await passkeys.finish_step_up(current_user, action, payload.credential)
    issued_at = int(time.time())
    proof = step_up.StepUpProof(
        action=action,
        methods=(step_up.METHOD_WEBAUTHN,),
        issued_at=issued_at,
        expires_at=issued_at + step_up.STEP_UP_TTL_SECONDS,
    )
    return StepUpResponse(**proof.to_dict())


def _action(value: str) -> str:
    try:
        return str(step_up.StepUpAction(value))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown step-up action {value!r}",
        ) from None


def _require_family(principal: TokenData, action: str) -> None:
    if not principal.fid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": step_up.CODE_UNAVAILABLE,
                "action": action,
                "reason": "no_session_family",
                "expires_in": step_up.STEP_UP_TTL_SECONDS,
            },
        )


async def _audit(user, action: str, outcome: str, new_value: dict) -> None:
    await log_audit_action(
        required=True,
        tenant_id=getattr(user, "tenant_id", None),
        user_id=getattr(user, "id", None),
        action=action,
        resource_type="auth",
        resource_id=getattr(user, "id", None),
        outcome=outcome,
        new_value=new_value,
    )


__all__ = ["router"]
