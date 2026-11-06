"""Sealed connector credentials: one slot per ``(integration, kind)``.

Secret material never lives in ``user_config``. A credential is a row whose
``sealed_blob`` is the family at-rest cipher wrapper
(``{"_encrypted": token, "_kid": fingerprint}``) produced with the integration
id as cipher context — a blob copied into another instance cannot decrypt.
``kid`` mirrors the wrapper's fingerprint as a typed column so a rotation
census is a plain query.

Failure semantics are fail-closed and typed (charter vocabulary): a missing,
revoked, expired or unreadable credential raises :class:`CredentialError`
(charter code ``auth``, recorded on sync-state as the engine-only
``credential`` class). The sync engine calls :func:`require_credentials`
before any pull, so revocation stops data flow instead of running
unauthenticated.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extensions.errors import ExtensionError
from app.extensions.sdk.secrets import SecretCipher
from app.models.enums import IntegrationCredentialKind
from app.models.user_integration import IntegrationCredential

logger = logging.getLogger(__name__)


class CredentialError(ExtensionError):
    """A credential cannot be used — always the charter ``auth`` code.

    The engine records ``last_error_class = credential`` (the engine-only
    class) while the wire code stays ``auth`` so the instance flips to
    ``ERROR`` and the UI offers a reconnect.
    """

    def __init__(self, detail: str) -> None:
        super().__init__("auth", detail)


def _kind_value(kind: IntegrationCredentialKind | str) -> str:
    return kind.value if isinstance(kind, IntegrationCredentialKind) else str(kind)


def _seal(payload: dict[str, Any], *, context: str) -> tuple[str, str]:
    cipher = SecretCipher.from_settings()
    wrapper = cipher.encrypt_value(payload, context=context)
    kid = str(wrapper.get("_kid") or cipher.primary_tag)
    return json.dumps(wrapper, separators=(",", ":")), kid


def _unseal(sealed_blob: str, *, context: str) -> dict[str, Any]:
    cipher = SecretCipher.from_settings()
    try:
        wrapper = json.loads(sealed_blob)
        value = cipher.decrypt_value(wrapper, context=context)
    except Exception as exc:
        raise CredentialError(
            "Stored credential cannot be decrypted (missing/rotated data key?)"
        ) from exc
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {"value": value}
    if not isinstance(value, dict):
        return {"value": value}
    return value


async def _load(
    db: AsyncSession,
    integration: Any,
    kind: IntegrationCredentialKind | str,
) -> IntegrationCredential | None:
    result = await db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.user_integration_id == integration.id,
            IntegrationCredential.kind == _kind_value(kind),
        )
    )
    return result.scalars().one_or_none()


async def seal_credential(
    db: AsyncSession,
    integration: Any,
    kind: IntegrationCredentialKind | str,
    payload: dict[str, Any],
    *,
    scopes: list[str] | None = None,
    access_expires_at: datetime | None = None,
    refresh_expires_at: datetime | None = None,
) -> IntegrationCredential:
    """Create or rotate the ``(integration, kind)`` credential slot.

    Rotation reseals in place under the current primary key's ``_kid`` and
    stamps ``rotated_at``; a revoked slot becomes usable again (the caller
    has just re-authorized).
    """
    now = datetime.now(UTC)
    sealed_blob, kid = _seal(payload, context=str(integration.id))
    row = await _load(db, integration, kind)
    if row is None:
        row = IntegrationCredential(
            tenant_id=integration.tenant_id,
            user_integration_id=integration.id,
            kind=_kind_value(kind),
            sealed_blob=sealed_blob,
            kid=kid,
            scopes=list(scopes or []),
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )
        db.add(row)
    else:
        row.sealed_blob = sealed_blob
        row.kid = kid
        row.scopes = list(scopes or [])
        row.access_expires_at = access_expires_at
        row.refresh_expires_at = refresh_expires_at
        row.rotated_at = now
        row.revoked_at = None
    await db.flush()
    return row


async def read_credential(
    db: AsyncSession,
    integration: Any,
    kind: IntegrationCredentialKind | str,
) -> dict[str, Any]:
    """Decrypt and return the credential payload; fail closed when unusable."""
    row = await _load(db, integration, kind)
    if row is None:
        raise CredentialError(f"No {_kind_value(kind)} credential is stored for this instance.")
    if row.revoked_at is not None:
        raise CredentialError("The stored credential was revoked — reconnect the source.")
    return _unseal(row.sealed_blob, context=str(integration.id))


async def revoke_credential(
    db: AsyncSession,
    integration: Any,
    kind: IntegrationCredentialKind | str | None = None,
) -> int:
    """Revoke one slot (or every slot); subsequent reads fail closed.

    The ciphertext is left intact for the erasure job to shred (plan 6) — a
    revoked credential is unusable, not necessarily forgotten.
    """
    if kind is None:
        result = await db.execute(
            select(IntegrationCredential).where(
                IntegrationCredential.user_integration_id == integration.id
            )
        )
        rows = list(result.scalars().all())
    else:
        row = await _load(db, integration, kind)
        rows = [row] if row is not None else []
    now = datetime.now(UTC)
    for row in rows:
        row.revoked_at = now
    await db.flush()
    return len(rows)


async def require_credentials(db: AsyncSession, integration: Any) -> None:
    """Fail-closed gate the engine runs before pulling.

    Instances with no credential rows are credential-less by design (tokenless
    FHIR servers, HMAC-only inbound bridges) and pass. Any row that is
    revoked, unreadable, or an expired OAuth refresh raises
    :class:`CredentialError`.
    """
    result = await db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.user_integration_id == integration.id
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return
    now = datetime.now(UTC)
    for row in rows:
        kind = row.kind.value if isinstance(row.kind, IntegrationCredentialKind) else str(row.kind)
        if row.revoked_at is not None:
            raise CredentialError(f"The {kind} credential was revoked — reconnect the source.")
        if kind == IntegrationCredentialKind.OAUTH2.value:
            refresh_expires_at = row.refresh_expires_at
            if refresh_expires_at is not None and refresh_expires_at <= now:
                raise CredentialError("The OAuth refresh token expired — reconnect the source.")
        _unseal(row.sealed_blob, context=str(integration.id))


async def credentials_summary(db: AsyncSession, integration: Any) -> list[dict[str, Any]]:
    """Read-model projection — never includes the sealed payload."""
    result = await db.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.user_integration_id == integration.id
        )
    )
    out = []
    for row in result.scalars().all():
        kind = row.kind.value if isinstance(row.kind, IntegrationCredentialKind) else str(row.kind)
        out.append(
            {
                "kind": kind,
                "kid": row.kid,
                "scopes": list(row.scopes or []),
                "access_expires_at": row.access_expires_at,
                "refresh_expires_at": row.refresh_expires_at,
                "rotated_at": row.rotated_at,
                "revoked_at": row.revoked_at,
            }
        )
    return out


def credential_id(integration_id: UUID) -> str:
    """The cipher context for one integration's credential slots."""
    return str(integration_id)
