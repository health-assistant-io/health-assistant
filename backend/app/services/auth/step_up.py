"""Session-bound step-up re-authentication (ADR 0054 Phase 6, program 12C1).

Five actions are sensitive enough that a live session is not sufficient
proof of presence: a role change, a data export, a break-glass elevation,
an admin-level patient grant and a passkey/MFA factor change. Each requires
a **fresh** proof that the human at the keyboard is the account owner:

``POST /auth/step-up`` verifies the factors available on the account —
the local password (when one is set) and TOTP/recovery codes (when MFA is
active) — and records a proof in the token store keyed
``stepup:{fid}:{action}``. A WebAuthn assertion proves the same actions
through ``POST /auth/step-up/passkeys/{options,verify}``; the proof it
records carries ``methods: ["webauthn"]``. The proof is a **bounded window**
(10 minutes, one action, one sign-in family): the action it authorizes must
follow inside the window, and a proof for one action never covers another.

The proof is bound to the *family* (``fid``), not to the bearer token: a
rotated access token inside the same sign-in keeps the proof valid, while
a different device never sees it.

SSO-only accounts (no password, no TOTP) can still step up while their
federated sign-in is recent: the session carries the IdP's ``auth_time``
and ``amr`` claims, and a second factor asserted within the window counts
as the fresh proof (``federated`` method). An account with no available
factor is refused with a typed ``step_up_unavailable`` rather than
silently allowed; an account whose only local factor is a passkey is told
``step_up_required`` with ``methods: ["webauthn"]`` so the SPA starts the
assertion ceremony.

Failure posture: enforcement never degrades open. A missing or unreadable
store means "no proof" and the action is refused with
``403 step_up_required``.
"""

from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass

from fastapi import HTTPException, status

from app.core import token_store
from app.core.security import verify_password
from app.schemas.user import TokenData
from app.services.audit.audit_service import OUTCOME_DENIED, OUTCOME_OK, log_audit_action
from app.services.auth import mfa_service
from app.services.auth.amr import amr_evidences_second_factor
from app.services.tenancy.user_service import get_user_by_id


class StepUpAction(enum.StrEnum):
    """The closed set of actions a step-up proof can authorize."""

    ROLE_CHANGE = "role_change"
    EXPORT = "export"
    BREAK_GLASS = "break_glass"
    GRANT_ADMIN = "grant_admin"
    MFA_CHANGE = "mfa_change"


#: Proof lifetime (seconds) — a re-authentication is good for ten minutes.
STEP_UP_TTL_SECONDS = 10 * 60

#: Stable error codes on the wire (the SPA switches on these).
CODE_REQUIRED = "step_up_required"
CODE_UNAVAILABLE = "step_up_unavailable"

#: Factors a proof can be made of.
METHOD_PASSWORD = "password"
METHOD_TOTP = "totp"
METHOD_RECOVERY = "recovery"
METHOD_FEDERATED = "federated"
METHOD_WEBAUTHN = "webauthn"


@dataclass(frozen=True)
class StepUpProof:
    """One successful proof: what authorized it and when."""

    action: str
    methods: tuple[str, ...]
    issued_at: int
    expires_at: int

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "methods": list(self.methods),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "expires_in": max(self.expires_at - int(time.time()), 0),
        }


def _required(action: StepUpAction | str) -> str:
    return str(StepUpAction(action))


def _denial(code: str, action: str, **extra) -> HTTPException:
    detail = {"code": code, "action": action, "expires_in": STEP_UP_TTL_SECONDS}
    detail.update(extra)
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def available_methods(user) -> tuple[str, ...]:
    """The factors this account can prove at step-up.

    Password and TOTP are proved at ``POST /auth/step-up``; a registered
    passkey proves the same actions at the assertion ceremony under
    ``POST /auth/step-up/passkeys``.
    """
    from app.services.auth import passkeys

    methods: list[str] = []
    if getattr(user, "password_hash", None):
        methods.append(METHOD_PASSWORD)
    if getattr(user, "mfa_secret_enc", None):
        methods.append(METHOD_TOTP)
    if await passkeys.has_active_passkey(user.id):
        methods.append(METHOD_WEBAUTHN)
    return tuple(methods)


def _federated_fresh(principal: TokenData, now: int) -> bool:
    """Whether the session's own federated sign-in is a fresh MFA proof.

    Requires all three: the session came from OIDC, the IdP asserted a
    second factor (``amr``), and ``auth_time`` is inside the proof window.
    """
    if (principal.auth_mode or "") != "oidc":
        return False
    if not amr_evidences_second_factor(principal.amr):
        return False
    auth_time = principal.auth_time
    if auth_time is None:
        return False
    return 0 <= now - int(auth_time) <= STEP_UP_TTL_SECONDS


async def issue_proof(
    principal: TokenData,
    *,
    action: StepUpAction | str,
    password: str | None = None,
    code: str | None = None,
) -> StepUpProof:
    """Verify the supplied factors and record the proof for ``action``.

    Raises ``HTTPException`` 403 ``step_up_required`` when a required
    factor is missing (with ``methods`` = what would be accepted), 401 on a
    wrong password/code, and 403 ``step_up_unavailable`` when the account
    has no factor at all.
    """
    resolved = _required(action)
    fid = principal.fid
    if not fid:
        raise _denial(CODE_UNAVAILABLE, resolved, reason="no_session_family")

    user = await get_user_by_id(principal.user_id)
    if user is None or not getattr(user, "is_active", False):
        raise _denial(CODE_UNAVAILABLE, resolved, reason="account_unavailable")

    accepted = await available_methods(user)
    now = int(time.time())
    methods: list[str] = []

    if METHOD_PASSWORD in accepted:
        if not password:
            raise _denial(CODE_REQUIRED, resolved, methods=list(accepted))
        if not verify_password(password, user.password_hash or ""):
            await log_audit_action(
                required=True,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="auth.step_up",
                resource_type="auth",
                resource_id=user.id,
                outcome=OUTCOME_DENIED,
                new_value={"detail": "invalid_password", "action": resolved},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid password.",
            )
        methods.append(METHOD_PASSWORD)

    if METHOD_TOTP in accepted:
        if not code:
            raise _denial(CODE_REQUIRED, resolved, methods=list(accepted))
        matched = await mfa_service.verify_code(user, code)
        if not matched:
            await log_audit_action(
                required=True,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="auth.step_up",
                resource_type="auth",
                resource_id=user.id,
                outcome=OUTCOME_DENIED,
                new_value={"detail": "invalid_code", "action": resolved},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid verification code.",
            )
        methods.append(METHOD_RECOVERY if matched == "recovery" else METHOD_TOTP)

    if not methods:
        if _federated_fresh(principal, now):
            methods.append(METHOD_FEDERATED)
        elif METHOD_WEBAUTHN in accepted:
            raise _denial(CODE_REQUIRED, resolved, methods=list(accepted))
        else:
            await log_audit_action(
                required=True,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="auth.step_up",
                resource_type="auth",
                resource_id=user.id,
                outcome=OUTCOME_DENIED,
                new_value={"detail": "no_factor", "action": resolved},
            )
            raise _denial(CODE_UNAVAILABLE, resolved, reason="no_factor")

    proof = StepUpProof(
        action=resolved,
        methods=tuple(methods),
        issued_at=now,
        expires_at=now + STEP_UP_TTL_SECONDS,
    )
    await token_store.register_step_up(
        str(fid),
        resolved,
        json.dumps({"issued_at": proof.issued_at, "methods": list(methods)}),
        STEP_UP_TTL_SECONDS,
    )
    await log_audit_action(
        required=True,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="auth.step_up",
        resource_type="auth",
        resource_id=user.id,
        outcome=OUTCOME_OK,
        new_value={"action": resolved, "methods": list(methods)},
    )
    return proof


async def active_proof(principal: TokenData, action: StepUpAction | str) -> str | None:
    """The family's live proof methods for ``action`` (``None`` when dead).

    A proof is a bounded **window**: it stays valid for
    :data:`STEP_UP_TTL_SECONDS` after the re-authentication, for exactly
    one action and one sign-in family.
    """
    resolved = _required(action)
    fid = principal.fid
    if not fid:
        return None
    raw = await token_store.read_step_up(str(fid), resolved)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        issued_at = int(payload.get("issued_at") or 0)
    except (ValueError, TypeError):
        return None
    now = int(time.time())
    if issued_at <= 0 or now - issued_at > STEP_UP_TTL_SECONDS or now < issued_at:
        return None
    methods = payload.get("methods") or []
    return ",".join(str(method) for method in methods)


async def _required_denial(principal: TokenData, action: str) -> HTTPException:
    """The 403 ``step_up_required`` body, carrying the acceptable methods.

    ``methods`` always rides the denial so the SPA can pick the ceremony
    without a second round trip. An account that cannot step up at all — no
    session family: a ``pat`` (identity-auth §8: a bounded bearer may not
    convert itself into account control) or a bare auth token — gets an
    empty list plus ``reason``.
    """
    methods: list[str] = []
    reason: str | None = None
    if not principal.fid:
        reason = "no_session_family"
    else:
        user = await get_user_by_id(principal.user_id)
        if user is not None:
            methods = list(await available_methods(user))
    extra: dict = {"methods": methods}
    if reason:
        extra["reason"] = reason
    return _denial(CODE_REQUIRED, action, **extra)


async def enforce(principal: TokenData, action: StepUpAction | str) -> str:
    """Validate the proof for ``action`` or raise ``403 step_up_required``.

    Returns the proof's method list (audit detail). Every sensitive write
    calls this **before** mutating, so a refused action leaves no state.
    The check never degrades open: a missing/unreadable store means "no
    proof", and the denial names the factors that would satisfy it.
    """
    resolved = _required(action)
    methods = await active_proof(principal, action)
    if methods is not None:
        return methods
    raise await _required_denial(principal, resolved)


__all__ = [
    "CODE_REQUIRED",
    "CODE_UNAVAILABLE",
    "METHOD_FEDERATED",
    "METHOD_PASSWORD",
    "METHOD_RECOVERY",
    "METHOD_TOTP",
    "METHOD_WEBAUTHN",
    "STEP_UP_TTL_SECONDS",
    "StepUpAction",
    "StepUpProof",
    "active_proof",
    "available_methods",
    "enforce",
    "issue_proof",
]
