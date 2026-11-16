"""WebAuthn/passkey ceremonies (ADR 0054 Phase 6 — program 12C2).

The server side of the WebAuthn Level 2 ceremonies:

* **Registration** — ``POST /me/mfa/passkeys/options`` builds
  ``PublicKeyCredentialCreationOptions`` (RP id/name, an opaque user handle,
  exclusion list) and stores a single-use challenge; ``/verify`` checks the
  attestation against the configured RP id/origins, requires user
  verification, and stores the credential.
* **Authentication** — ``POST /auth/passkeys/options`` builds the assertion
  options (``allowCredentials`` for the named account, empty for a
  discoverable-credential/usernameless ceremony) and stores the challenge;
  ``/verify`` checks the assertion, advances the signature counter and
  answers with the account for session issuance.
* **Step-up** — the same assertion ceremony bound to a signed-in family and
  one closed action; success records the existing
  ``stepup:{fid}:{action}`` proof with ``methods: ["webauthn"]``.

Security posture: every challenge is single-use (consumed before the
signature check, so a replayed assertion fails even when the first attempt
was malformed) and expires after :data:`CHALLENGE_TTL_SECONDS`. User
verification is **required** for both ceremonies — a passkey is meant to be
presence + possession. A non-zero signature counter that fails to advance is
a clone alarm: the credential is disabled, the attempt audited and refused.
The counter is read from ``authenticatorData`` **only after** the assertion
verifies — that field is attacker-controlled before verification, so a
pre-check would let anyone who knows a credential id disable the passkey.
The check itself compares the library-verified ``new_sign_count`` with the
stored one (the library's own counter argument is pinned to zero so a
genuine regression reaches this alarm rather than a bare "invalid
assertion").
Syncable credentials (backup eligible) may legitimately keep a zero counter;
their flags are stored so the rule can stay conservative.

Passkey login issues a session through the ordinary issuance path with
``auth_mode="password"`` (the token contract's closed local-auth value) and
``amr=["hwk","user"]`` (+ ``swk`` for a synced credential), so downstream
policy sees a second factor without a new auth-mode vocabulary.

The relying party is configured by ``HA_WEBAUTHN_RP_ID`` /
``HA_WEBAUTHN_ORIGIN``; an instance that has not configured them outside
development answers ``503 webauthn_not_configured`` rather than falling back
to a localhost default.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, generate_challenge
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core import token_store
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.webauthn_credential import WebAuthnCredential
from app.services.audit.audit_service import OUTCOME_DENIED, OUTCOME_OK, log_audit_action

logger = logging.getLogger(__name__)

#: A ceremony challenge is valid for five minutes and exactly once.
CHALLENGE_TTL_SECONDS = 5 * 60

CODE_NOT_CONFIGURED = "webauthn_not_configured"
CODE_CHALLENGE_EXPIRED = "webauthn_challenge_expired"
CODE_INVALID_RESPONSE = "webauthn_invalid_response"
CODE_UNKNOWN_CREDENTIAL = "webauthn_unknown_credential"
CODE_CLONE_DETECTED = "webauthn_clone_detected"

_REG_PREFIX = "wreg"
_AUTH_PREFIX = "wauth"
_STEPUP_PREFIX = "wstep"


class WebAuthnNotConfigured(HTTPException):
    """The instance has no deliberate RP configuration outside dev/test."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": CODE_NOT_CONFIGURED,
                "detail": (
                    "Passkeys are not configured on this instance. Set "
                    "HA_WEBAUTHN_RP_ID and HA_WEBAUTHN_ORIGIN."
                ),
            },
        )


def _rp() -> tuple[str, str, list[str]]:
    """(rp_id, rp_name, origins) or the typed 503 when unconfigured."""
    if not settings.webauthn_configured():
        raise WebAuthnNotConfigured()
    origins = settings.webauthn_origins()
    if not origins:
        raise WebAuthnNotConfigured()
    return settings.webauthn_rp_id(), settings.HA_WEBAUTHN_RP_NAME, origins


def _denial(code: str, status_code: int = status.HTTP_400_BAD_REQUEST, **extra) -> HTTPException:
    detail = {"code": code}
    detail.update(extra)
    return HTTPException(status_code=status_code, detail=detail)


def _reg_key(user_id: str | UUID) -> str:
    return f"{_REG_PREFIX}:{user_id}"


def _auth_key(challenge: str) -> str:
    return f"{_AUTH_PREFIX}:{challenge}"


def _stepup_key(family_id: str, action: str) -> str:
    return f"{_STEPUP_PREFIX}:{family_id}:{action}"


def _client_challenge(credential: dict[str, Any]) -> str | None:
    """The challenge named by the credential's ``clientDataJSON``."""
    try:
        raw = credential["response"]["clientDataJSON"]
        if isinstance(raw, str):
            decoded = base64url_to_bytes(raw)
        else:
            raise TypeError("clientDataJSON must be base64url text")
        return str(json.loads(decoded.decode("utf-8")).get("challenge") or "") or None
    except (KeyError, TypeError, ValueError):
        return None


def _transports(values: Any) -> list[str]:
    allowed = {member.value for member in AuthenticatorTransport}
    return sorted({str(value) for value in (values or []) if str(value) in allowed})


async def _store_challenge(key: str, payload: dict[str, Any]) -> None:
    await token_store.register_webauthn_challenge(key, json.dumps(payload), CHALLENGE_TTL_SECONDS)


async def _consume_challenge(key: str) -> dict[str, Any] | None:
    raw = await token_store.consume_webauthn_challenge(key)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# --- credential reads -------------------------------------------------------


async def list_credentials(user_id: str | UUID) -> list[WebAuthnCredential]:
    """All credentials of one account (including disabled ones)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == UUID(str(user_id)))
            .order_by(WebAuthnCredential.created_at.asc())
        )
        return list(result.scalars().all())


async def active_credentials(user_id: str | UUID) -> list[WebAuthnCredential]:
    """Only credentials the account can still authenticate with."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WebAuthnCredential).where(
                WebAuthnCredential.user_id == UUID(str(user_id)),
                WebAuthnCredential.disabled.is_(False),
            )
        )
        return list(result.scalars().all())


async def has_active_passkey(user_id: str | UUID) -> bool:
    """Whether the account has at least one usable passkey."""
    try:
        uid = UUID(str(user_id))
    except (TypeError, ValueError):
        return False
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count())
            .select_from(WebAuthnCredential)
            .where(
                WebAuthnCredential.user_id == uid,
                WebAuthnCredential.disabled.is_(False),
            )
        )
        return int(result.scalar() or 0) > 0


async def count_active_by_user(user_ids: list[UUID]) -> dict[UUID, int]:
    """Active credential counts for a batch of accounts (admin list)."""
    if not user_ids:
        return {}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WebAuthnCredential.user_id, func.count())
            .where(
                WebAuthnCredential.user_id.in_(user_ids),
                WebAuthnCredential.disabled.is_(False),
            )
            .group_by(WebAuthnCredential.user_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}


# --- registration ----------------------------------------------------------


async def begin_registration(user) -> dict[str, Any]:
    """Creation options + a stored challenge (caller is authenticated)."""
    rp_id, rp_name, _origins = _rp()
    existing = await active_credentials(user.id)
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_name=user.email,
        user_id=UUID(str(user.id)).bytes,
        user_display_name=getattr(user, "full_name", "") or user.email,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(
                id=credential.credential_id,
                transports=[
                    AuthenticatorTransport(transport) for transport in credential.transports or []
                ],
            )
            for credential in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
            resident_key=ResidentKeyRequirement.PREFERRED,
        ),
    )
    await _store_challenge(
        _reg_key(user.id),
        {"user_id": str(user.id), "challenge": bytes_to_base64url(options.challenge)},
    )
    return {
        "options": json.loads(options_to_json(options)),
        "expires_in": CHALLENGE_TTL_SECONDS,
    }


async def finish_registration(user, credential: dict[str, Any], *, name: str) -> WebAuthnCredential:
    """Verify the attestation and persist the credential."""
    rp_id, _rp_name, origins = _rp()
    consumed = await _consume_challenge(_reg_key(user.id))
    if consumed is None:
        await _audit(
            user,
            "mfa.passkey_register",
            OUTCOME_DENIED,
            {"cause": "challenge_expired"},
        )
        raise _denial(CODE_CHALLENGE_EXPIRED)
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(str(consumed.get("challenge") or "")),
            expected_rp_id=rp_id,
            expected_origin=origins,
            require_user_presence=True,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, TypeError) as exc:
        logger.info("passkey registration refused for %s: %s", user.id, exc)
        await _audit(
            user,
            "mfa.passkey_register",
            OUTCOME_DENIED,
            {"cause": "invalid_attestation"},
        )
        raise _denial(CODE_INVALID_RESPONSE) from None

    transports = _transports((credential.get("response") or {}).get("transports"))
    row = WebAuthnCredential(
        tenant_id=UUID(str(user.tenant_id)),
        user_id=UUID(str(user.id)),
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=int(verified.sign_count or 0),
        transports=transports,
        aaguid=str(verified.aaguid or "") or None,
        backup_eligible=verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
        backup_state=bool(verified.credential_backed_up),
        name=(name or "Passkey")[:200],
    )
    async with AsyncSessionLocal() as session:
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            await _audit(
                user,
                "mfa.passkey_register",
                OUTCOME_DENIED,
                {"cause": "duplicate_credential"},
            )
            raise _denial("webauthn_credential_exists", status.HTTP_409_CONFLICT) from None
        await session.refresh(row)
    await _audit(
        user,
        "mfa.passkey_register",
        OUTCOME_OK,
        {"credential_id": str(row.id), "name": row.name},
    )
    from app.services.auth import login_notifications

    await login_notifications.notify_security_event(
        user,
        event="passkey_added",
        title="A passkey was added to your account",
        body=(
            f'"{row.name}" can now sign in to your account. If this was not you, '
            "remove it in Settings → Security."
        ),
        dedup=str(row.id),
    )
    return row


# --- authentication --------------------------------------------------------


async def begin_authentication(user) -> dict[str, Any]:
    """Assertion options for a known account (or discoverable credentials)."""
    rp_id, _rp_name, _origins = _rp()
    challenge = generate_challenge()
    credentials = await active_credentials(user.id) if user is not None else []
    options = generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        allow_credentials=[
            PublicKeyCredentialDescriptor(
                id=credential.credential_id,
                transports=[
                    AuthenticatorTransport(transport) for transport in credential.transports or []
                ],
            )
            for credential in credentials
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_b64 = bytes_to_base64url(options.challenge)
    await _store_challenge(
        _auth_key(challenge_b64),
        {
            "user_id": str(user.id) if user is not None else None,
            "challenge": challenge_b64,
        },
    )
    return {
        "options": json.loads(options_to_json(options)),
        "expires_in": CHALLENGE_TTL_SECONDS,
    }


async def finish_authentication(
    credential: dict[str, Any],
) -> tuple[Any, WebAuthnCredential]:
    """Verify an assertion and return ``(user row, credential row)``."""
    from app.services.tenancy.user_service import get_user_by_id

    rp_id, _rp_name, origins = _rp()
    challenge_b64 = _client_challenge(credential)
    if not challenge_b64:
        raise _denial(CODE_INVALID_RESPONSE)
    consumed = await _consume_challenge(_auth_key(challenge_b64))
    if consumed is None:
        raise _denial(CODE_CHALLENGE_EXPIRED)

    credential_id = _credential_id_bytes(credential)
    if credential_id is None:
        raise _denial(CODE_INVALID_RESPONSE)
    row = await _credential_by_id(credential_id)
    if row is None or row.disabled:
        raise _denial(CODE_UNKNOWN_CREDENTIAL, status.HTTP_401_UNAUTHORIZED)
    claimed_user_id = consumed.get("user_id")
    if claimed_user_id and str(claimed_user_id) != str(row.user_id):
        raise _denial(CODE_UNKNOWN_CREDENTIAL, status.HTTP_401_UNAUTHORIZED)

    from app.core import tenant_context

    tenant_context.set_tenant(row.tenant_id, actor=str(row.user_id))
    user = await get_user_by_id(row.user_id)
    if user is None or not getattr(user, "is_active", False):
        raise _denial(CODE_UNKNOWN_CREDENTIAL, status.HTTP_401_UNAUTHORIZED)

    # The signature counter is *only* trustworthy after the assertion verifies:
    # ``authenticatorData`` is attacker-controlled until then, so a pre-check
    # would let anyone holding a credential id disable the passkey (DoS). The
    # counter rule therefore runs on the library-verified ``new_sign_count``
    # (below, the clone alarm): the library's own counter argument is pinned to
    # zero so a genuine counter regression reaches our alarm (disable + audit +
    # notify) instead of stopping at a bare "invalid assertion".
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(str(consumed.get("challenge") or "")),
            expected_rp_id=rp_id,
            expected_origin=origins,
            credential_public_key=bytes(row.public_key),
            credential_current_sign_count=0,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, TypeError) as exc:
        logger.info("passkey assertion refused for %s: %s", user.id, exc)
        await _audit(user, "auth.passkey_login", OUTCOME_DENIED, {"cause": "invalid_assertion"})
        raise _denial(CODE_INVALID_RESPONSE, status.HTTP_401_UNAUTHORIZED) from None

    new_count = int(verified.new_sign_count or 0)
    stored_count = int(row.sign_count or 0)
    if new_count and stored_count and new_count <= stored_count:
        await _disable(row)
        await _audit(
            user,
            "auth.passkey_clone_detected",
            OUTCOME_DENIED,
            {
                "credential_id": str(row.id),
                "stored_sign_count": stored_count,
                "new_sign_count": new_count,
            },
        )
        from app.services.auth import login_notifications

        await login_notifications.notify_security_event(
            user,
            event="passkey_clone_detected",
            title="A passkey was disabled",
            body=(
                "A cloned authenticator was detected (its signature counter did "
                "not advance). That passkey was disabled; sign in with another "
                "factor and register a replacement."
            ),
            dedup=str(row.id),
            severity=login_notifications.NotificationSeverity.CRITICAL,
        )
        raise _denial(CODE_CLONE_DETECTED, status.HTTP_403_FORBIDDEN)

    async with AsyncSessionLocal() as session:
        stored = await session.get(WebAuthnCredential, row.id)
        if stored is not None:
            stored.sign_count = new_count
            stored.last_used_at = datetime.now(UTC)
            stored.backup_state = bool(verified.credential_backed_up)
            stored.backup_eligible = (
                verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE
            )
            await session.commit()
            await session.refresh(stored)
            row = stored
    await _audit(user, "auth.passkey_login", OUTCOME_OK, {"credential_id": str(row.id)})
    return user, row


def _credential_id_bytes(credential: dict[str, Any]) -> bytes | None:
    raw = credential.get("rawId") or credential.get("id")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return base64url_to_bytes(raw)
    except (TypeError, ValueError):
        return None


async def _credential_by_id(credential_id: bytes) -> WebAuthnCredential | None:
    from app.core import tenant_context

    with tenant_context.system_scope("auth:passkey_lookup"):
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WebAuthnCredential).where(WebAuthnCredential.credential_id == credential_id)
            )
            return result.scalar_one_or_none()


async def _disable(row: WebAuthnCredential) -> None:
    async with AsyncSessionLocal() as session:
        stored = await session.get(WebAuthnCredential, row.id)
        if stored is not None and not stored.disabled:
            stored.disabled = True
            await session.commit()


# --- step-up ---------------------------------------------------------------


async def begin_step_up(principal, action: str) -> dict[str, Any]:
    """Assertion options bound to this family and one step-up action."""
    rp_id, _rp_name, _origins = _rp()
    credentials = await active_credentials(principal.user_id)
    challenge = generate_challenge()
    options = generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        allow_credentials=[
            PublicKeyCredentialDescriptor(
                id=credential.credential_id,
                transports=[
                    AuthenticatorTransport(transport) for transport in credential.transports or []
                ],
            )
            for credential in credentials
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_b64 = bytes_to_base64url(options.challenge)
    await _store_challenge(
        _stepup_key(str(principal.fid), action),
        {
            "user_id": str(principal.user_id),
            "challenge": challenge_b64,
            "action": action,
        },
    )
    return {
        "action": action,
        "options": json.loads(options_to_json(options)),
        "expires_in": CHALLENGE_TTL_SECONDS,
    }


async def finish_step_up(principal, action: str, credential: dict[str, Any]) -> None:
    """Verify the assertion and record the family-bound step-up proof."""
    from app.services.auth import step_up

    rp_id, _rp_name, origins = _rp()
    consumed = await _consume_challenge(_stepup_key(str(principal.fid), action))
    if consumed is None:
        raise _denial(CODE_CHALLENGE_EXPIRED)
    if str(consumed.get("user_id")) != str(principal.user_id) or consumed.get("action") != action:
        raise _denial(CODE_INVALID_RESPONSE)

    credential_id = _credential_id_bytes(credential)
    if credential_id is None:
        raise _denial(CODE_INVALID_RESPONSE)
    row = await _credential_by_id(credential_id)
    if row is None or row.disabled or str(row.user_id) != str(principal.user_id):
        raise _denial(CODE_UNKNOWN_CREDENTIAL, status.HTTP_401_UNAUTHORIZED)

    # Counter read from *unverified* ``authenticatorData`` is attacker-controlled:
    # the clone alarm must run on the library-verified value (below), never as a
    # pre-check — otherwise a known credential id is enough to disable a passkey.
    # ``credential_current_sign_count`` is pinned to zero for the same reason as
    # in :func:`finish_authentication`.
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(str(consumed.get("challenge") or "")),
            expected_rp_id=rp_id,
            expected_origin=origins,
            credential_public_key=bytes(row.public_key),
            credential_current_sign_count=0,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, TypeError) as exc:
        logger.info("passkey step-up refused for %s: %s", principal.user_id, exc)
        await _audit(
            principal,
            "auth.step_up",
            OUTCOME_DENIED,
            {"action": action, "detail": "invalid_passkey"},
        )
        raise _denial(CODE_INVALID_RESPONSE, status.HTTP_401_UNAUTHORIZED) from None

    new_count = int(verified.new_sign_count or 0)
    stored_count = int(row.sign_count or 0)
    if new_count and stored_count and new_count <= stored_count:
        await _disable(row)
        await _audit(
            principal,
            "auth.passkey_clone_detected",
            OUTCOME_DENIED,
            {"credential_id": str(row.id), "action": action},
        )
        from app.services.auth import login_notifications
        from app.services.tenancy.user_service import get_user_by_id

        user = await get_user_by_id(principal.user_id)
        if user is not None:
            await login_notifications.notify_security_event(
                user,
                event="passkey_clone_detected",
                title="A passkey was disabled",
                body=(
                    "A cloned authenticator was detected (its signature counter "
                    "did not advance) during a step-up check. That passkey was "
                    "disabled; sign in with another factor and register a "
                    "replacement."
                ),
                dedup=str(row.id),
                severity=login_notifications.NotificationSeverity.CRITICAL,
            )
        raise _denial(CODE_CLONE_DETECTED, status.HTTP_403_FORBIDDEN)

    async with AsyncSessionLocal() as session:
        stored = await session.get(WebAuthnCredential, row.id)
        if stored is not None:
            stored.sign_count = new_count
            stored.last_used_at = datetime.now(UTC)
            await session.commit()

    now = int(time.time())
    await token_store.register_step_up(
        str(principal.fid),
        action,
        json.dumps({"issued_at": now, "methods": [step_up.METHOD_WEBAUTHN]}),
        step_up.STEP_UP_TTL_SECONDS,
    )
    await _audit(
        principal,
        "auth.step_up",
        OUTCOME_OK,
        {"action": action, "methods": [step_up.METHOD_WEBAUTHN]},
    )


# --- lifecycle -------------------------------------------------------------


async def delete_credential(user, credential_id: str | UUID) -> bool:
    """Delete one of the caller's credentials (and audit it)."""
    try:
        cid = UUID(str(credential_id))
    except (TypeError, ValueError):
        return False
    async with AsyncSessionLocal() as session:
        row = await session.get(WebAuthnCredential, cid)
        if row is None or str(row.user_id) != str(user.id):
            return False
        await session.delete(row)
        await session.commit()
    await _audit(user, "mfa.passkey_delete", OUTCOME_OK, {"credential_id": str(cid)})
    from app.services.auth import login_notifications

    await login_notifications.notify_security_event(
        user,
        event="passkey_removed",
        title="A passkey was removed from your account",
        body=(
            "A passkey can no longer sign in to your account. If this was not "
            "you, change your password."
        ),
        dedup=str(cid),
    )
    return True


async def _audit(actor, action: str, outcome: str, new_value: dict) -> None:
    """One auth audit row (fail-closed — auth decisions are never unrecorded)."""
    user_id = getattr(actor, "id", None) or getattr(actor, "user_id", None)
    tenant_id = getattr(actor, "tenant_id", None)
    await log_audit_action(
        required=True,
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        resource_type="auth",
        resource_id=user_id,
        outcome=outcome,
        new_value=new_value,
    )


__all__ = [
    "CHALLENGE_TTL_SECONDS",
    "CODE_CHALLENGE_EXPIRED",
    "CODE_CLONE_DETECTED",
    "CODE_INVALID_RESPONSE",
    "CODE_NOT_CONFIGURED",
    "CODE_UNKNOWN_CREDENTIAL",
    "WebAuthnNotConfigured",
    "active_credentials",
    "begin_authentication",
    "begin_registration",
    "begin_step_up",
    "count_active_by_user",
    "delete_credential",
    "finish_authentication",
    "finish_registration",
    "finish_step_up",
    "has_active_passkey",
    "list_credentials",
]
