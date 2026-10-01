"""TOTP MFA enrollment + verification (plan 16 H5, identity-auth §5.14).

State machine (all state lives on the ``users`` row — no extra tables):

* **None** — ``mfa_secret_enc`` is NULL. MFA off (unless
  ``mfa_enforced``, see below).
* **Pending** — ``mfa_pending`` holds ``{"secret_enc": <Fernet>,
  "recovery": [<bcrypt hashes>]}`` between ``POST /me/mfa/enroll`` and
  ``POST /me/mfa/confirm`` (or the login-time forced-enrollment path).
  The plaintext secret + recovery codes are returned **once**, by the
  enroll call; the server keeps only the encrypted secret and the
  hashes.
* **Active** — ``mfa_secret_enc`` set + ``mfa_recovery_codes`` (JSON
  array of remaining single-use code hashes). Login then answers with
  the 401 ``mfa_required`` challenge instead of tokens.

At rest: the secret is sealed under the DATA_KEY family
(``app.core.encryption`` — Fernet, ``enc::`` prefix, never plaintext);
recovery codes are bcrypt-hashed exactly like passwords. Consumption of
a recovery code removes just its hash (single-use semantics).

``mfa_enforced`` (admin-forced, "promoted for institute use"): the next
password login gets ``mfa_required`` with ``enrollment_needed: true``;
the challenge then admits the user only by completing enrollment
(verify accepts the pending secret's code and activates it).
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.core.encryption import decrypt_secret, encrypt_secret
from app.core.security import get_password_hash, verify_password
from app.core.totp import (
    build_totp_uri,
    canonical_recovery_code,
    generate_recovery_codes,
    generate_totp_secret,
    verify_totp,
)
from app.models.user_model import UserModel

logger = logging.getLogger(__name__)


async def get_mfa_user(user_id: str | UUID) -> UserModel | None:
    """Fetch the live user row for MFA checks (None off the UUID parse)."""
    if not DATABASE_AVAILABLE:
        return None
    try:
        uid = UUID(str(user_id))
    except (ValueError, TypeError):
        return None
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(UserModel).where(UserModel.id == uid))
        return result.scalar_one_or_none()


def mfa_status(user: UserModel) -> dict:
    """The self-service status shape (``GET /me/mfa``)."""
    return {
        "enabled": bool(getattr(user, "mfa_secret_enc", None)),
        "enforced": bool(getattr(user, "mfa_enforced", False)),
        "pending": bool(getattr(user, "mfa_pending", None)),
    }


def _provisioning(secret: str, user: UserModel) -> dict:
    issuer = settings.APP_NAME or "Health Assistant"
    return {
        "secret": secret,
        "uri": build_totp_uri(secret, user.email, issuer),
    }


async def begin_enrollment(user: UserModel) -> dict:
    """Start (or restart) an enrollment: fresh secret + recovery codes.

    Generates a new 20-byte base32 secret and 8 single-use recovery
    codes, persists the encrypted secret + code hashes as the *pending*
    enrollment, and returns the plaintext provisioning payload (secret,
    ``otpauth://`` URI, recovery codes). The plaintext recovery codes
    are shown exactly once — this response is the only place they exist
    outside the user's password manager.
    """
    secret = generate_totp_secret()
    recovery_codes = generate_recovery_codes()
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(
                mfa_pending={
                    "secret_enc": encrypt_secret(secret),
                    "recovery": [get_password_hash(c) for c in recovery_codes],
                }
            )
        )
        await session.commit()
    return {**_provisioning(secret, user), "recovery_codes": recovery_codes}


async def confirm_enrollment(user_id: str | UUID, code: str) -> bool:
    """Activate the pending enrollment when ``code`` matches its secret.

    Moves the pending encrypted secret + recovery hashes to the active
    columns and clears the pending state. Returns False when there is no
    pending enrollment or the code is wrong (caller decides the HTTP
    shape and the §7 lockout counting).
    """
    user = await get_mfa_user(user_id)
    if user is None:
        return False
    pending = getattr(user, "mfa_pending", None)
    if not pending or not pending.get("secret_enc"):
        return False
    try:
        secret = decrypt_secret(pending["secret_enc"])
    except ValueError:
        logger.warning("mfa: pending secret undecryptable for %s", user.id)
        return False
    if not verify_totp(secret, code):
        return False
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(
                mfa_secret_enc=pending["secret_enc"],
                mfa_recovery_codes=json.dumps(pending.get("recovery") or []),
                mfa_pending=None,
            )
        )
        await session.commit()
    return True


async def verify_totp_code(user: UserModel, code: str) -> bool:
    """Check ``code`` against the user's **active** TOTP secret."""
    stored = getattr(user, "mfa_secret_enc", None)
    if not stored:
        return False
    try:
        secret = decrypt_secret(stored)
    except ValueError:
        logger.warning("mfa: secret undecryptable for %s", user.id)
        return False
    return verify_totp(secret, code)


async def consume_recovery_code(user: UserModel, code: str) -> bool:
    """Single-use recovery-code check — a match removes that code's hash.

    The stored value is a JSON array of bcrypt hashes (like passwords);
    the presented code is canonicalized (case, spaces, dash) and checked
    against each remaining hash. Only the matching hash is removed.
    """
    stored = getattr(user, "mfa_recovery_codes", None)
    if not stored:
        return False
    try:
        hashes = json.loads(stored)
    except (TypeError, ValueError):
        return False
    if not isinstance(hashes, list) or not hashes:
        return False
    canonical = canonical_recovery_code(code)
    for index, hashed in enumerate(hashes):
        if verify_password(canonical, str(hashed)):
            remaining = hashes[:index] + hashes[index + 1 :]
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(UserModel)
                    .where(UserModel.id == user.id)
                    .values(mfa_recovery_codes=json.dumps(remaining))
                )
                await session.commit()
            return True
    return False


async def verify_code(user: UserModel, code: str) -> str | None:
    """Verify an MFA code (TOTP first, then recovery). Returns the
    matched method (``"totp"`` / ``"recovery"``) or None."""
    if await verify_totp_code(user, code):
        return "totp"
    if await consume_recovery_code(user, code):
        return "recovery"
    return None


async def clear_mfa(user_id: str | UUID) -> None:
    """Drop the active secret + recovery hashes (and any pending state).

    The ``mfa_enforced`` flag is deliberately untouched — clearing MFA
    never silently cancels an admin's requirement; the next login will
    demand enrollment again.
    """
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(UserModel)
            .where(UserModel.id == UUID(str(user_id)))
            .values(mfa_secret_enc=None, mfa_recovery_codes=None, mfa_pending=None)
        )
        await session.commit()


async def set_enforced(user_id: str | UUID, enforced: bool) -> UserModel | None:
    """Set/clear the admin-forced flag. Returns the fresh row (or None)."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(UserModel)
            .where(UserModel.id == UUID(str(user_id)))
            .values(mfa_enforced=bool(enforced))
        )
        await session.commit()
    return await get_mfa_user(user_id)
