"""Sealed credential custody for calendar bindings (ADR-0052 §2, plan 52 P2).

**One secret store, one owner key over.** The family law (ADR-0052 §2 /
ADR-0042 clause 3): no column is token-shaped; credential material lives
only in the host's sealed store under a pointer such as
``calendar:<binding-id>:oauth``. Health's sealed store is the
``integration_credentials`` shape: rows whose ``sealed_blob`` is the
``nx_auth.atrest`` cipher wrapper (``{"_encrypted": …, "_kid": …}``),
context-bound to the owning row, ``kid`` mirrored as a typed column for
the rotation census.

Why this module is a thin adapter instead of a straight reuse of
``app.services.integration.integration_credentials``: that helper writes
``IntegrationCredential`` rows whose owner column is a **NOT NULL FK into
``user_integrations.id``**, and plan 52 §3 makes a calendar binding
deliberately *not* a ``UserIntegration`` (the FHIR pull legs cannot
represent calendar identity cleanly). Generalizing that FK would mutate a
live framework's schema for every existing connector. So the adapter:

* uses the **same cipher** (``app.extensions.sdk.secrets.SecretCipher`` —
  the ``nx_auth.atrest`` kit cipher, the ``HA_DATA_KEY`` rotation ring,
  the missing-key fail-closed ``RuntimeError``);
* uses the **same wrapper shape** and the binding id as cipher context
  (a blob copied into another binding cannot decrypt — the audit-2026-10-18
  F21 law);
* stores slots in ``calendar_binding_credentials`` (the same shape one
  owner key over: ``sealed_blob``/``kid``/``scopes``/expiry/rotation/
  revocation columns).

There is exactly one cryptographic store underneath (one key ring, one
rotation story); the rows are keyed by owner type. Fail-closed semantics
match the framework: a missing, revoked or unreadable slot raises
:class:`CalendarCredentialError` (charter code ``auth``) and the sync
stops — no plaintext fallback, ever.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extensions.errors import ExtensionError
from app.extensions.sdk.secrets import SecretCipher
from app.models.calendar_binding import SLOT_KINDS, CalendarBindingCredential

logger = logging.getLogger(__name__)

#: The pointer-suffix law: ``calendar:<binding-id>:<slot>``.
SLOT_FOR_AUTH_KIND = {
    "oauth2": "oauth",
    "app-password": "password",
    "token": "token",
    "none": None,
}


class CalendarCredentialError(ExtensionError):
    """A credential cannot be used — always a charter-typed stop code.

    ``code="auth"`` (default) means the **provider rejected the stored
    grant** (revoked/expired refresh token, ``invalid_grant``) — the
    family posture maps it to ``needs-auth``: stop, notify once, the user
    reconnects. ``code="needs-secret"`` means **no usable secret is
    stored** (missing/revoked slot, unreadable ciphertext) — mapped to
    ``needs-secret``. Both stop the sync; neither retries.
    """

    def __init__(self, detail: str, *, code: str = "auth") -> None:
        super().__init__(code, detail)


def slot_for(auth_kind: str | None) -> str | None:
    """The slot kind for a binding's ``auth_kind`` (``None`` = no secret)."""
    return SLOT_FOR_AUTH_KIND.get(str(auth_kind or ""))


def secret_ref_for(binding_id: Any, auth_kind: str | None) -> str | None:
    """The custody pointer for one binding (``calendar:<id>:<slot>``)."""
    kind = slot_for(auth_kind)
    if kind is None:
        return None
    return f"calendar:{binding_id}:{kind}"


def _slot_value(auth_kind: str | None, payload: dict[str, Any]) -> Any:
    """Extract the sealed value from a set-secret payload, typed.

    ``token`` bindings accept either ``{"token": …}`` or, for private ICS
    feeds, ``{"url": …}`` — a feed URL carrying a query string IS the
    bearer credential (ADR-0052 §9 / C4), so it is sealed like any other.
    Password/token slots also carry through an explicit ``url``/
    ``calendar_url`` (a CalDAV collection whose URL embeds credentials in
    its query), so the full address never lives outside the slot.
    """
    kind = slot_for(auth_kind)
    carry_urls = {
        key: str(payload[key])
        for key in ("url", "calendar_url")
        if payload.get(key)
    }
    if kind == "password":
        password = payload.get("password")
        if not password or not isinstance(password, str):
            raise ExtensionError("invalid", "an app-password binding needs {'password': …}")
        sealed: dict[str, Any] = {"password": password}
        if payload.get("username"):
            sealed["username"] = str(payload["username"])
        sealed.update(carry_urls)
        return sealed
    if kind == "token":
        token = payload.get("token")
        if token and isinstance(token, str):
            sealed = {"token": token}
            sealed.update(carry_urls)
            return sealed
        url = payload.get("url")
        if url and isinstance(url, str):
            return {"url": url, **carry_urls}
        raise ExtensionError(
            "invalid",
            "a token binding needs {'token': …} — or, for a private feed, "
            "{'url': …} (the full feed URL is the credential)",
        )
    if kind == "oauth":
        if not payload.get("access_token"):
            raise ExtensionError("invalid", "an oauth slot needs an access_token")
        sealed = dict(payload)
        sealed.update(carry_urls)
        return sealed
    raise ExtensionError("invalid", f"auth kind {auth_kind!r} takes no stored secret")


async def _load(db: AsyncSession, binding: Any, kind: str) -> CalendarBindingCredential | None:
    result = await db.execute(
        select(CalendarBindingCredential).where(
            CalendarBindingCredential.binding_id == binding.id,
            CalendarBindingCredential.kind == kind,
        )
    )
    return result.scalars().one_or_none()


def _seal(payload: Any, *, context: str) -> tuple[str, str]:
    cipher = SecretCipher.from_settings()
    wrapper = cipher.encrypt_value(payload, context=context)
    kid = str(wrapper.get("_kid") or cipher.primary_tag)
    return json.dumps(wrapper, separators=(",", ":")), kid


def _unseal(sealed_blob: str, *, context: str) -> dict[str, Any]:
    try:
        cipher = SecretCipher.from_settings()
        wrapper = json.loads(sealed_blob)
        value = cipher.decrypt_value(wrapper, context=context)
    except Exception as exc:
        raise CalendarCredentialError(
            "Stored credential cannot be decrypted (missing/rotated data key?)",
            code="needs-secret",
        ) from exc
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {"value": value}
    if not isinstance(value, dict):
        return {"value": value}
    return value


async def set_secret(
    db: AsyncSession,
    binding: Any,
    payload: dict[str, Any],
    *,
    scopes: list[str] | None = None,
    access_expires_at: datetime | None = None,
    refresh_expires_at: datetime | None = None,
) -> CalendarBindingCredential:
    """Create or rotate the binding's credential slot (rotation reseals).

    The payload shape is validated per ``auth_kind`` first; the sealed
    value never touches an unsealed column.
    """
    kind = slot_for(binding.auth_kind)
    if kind is None:
        raise ExtensionError("invalid", f"auth kind {binding.auth_kind!r} takes no secret")
    if kind not in SLOT_KINDS:
        raise ExtensionError("invalid", f"unknown slot kind {kind!r}")
    sealed_value = _slot_value(binding.auth_kind, payload)
    sealed_blob, kid = _seal(sealed_value, context=str(binding.id))
    now = datetime.now(UTC)
    row = await _load(db, binding, kind)
    if row is None:
        row = CalendarBindingCredential(
            tenant_id=binding.tenant_id,
            binding_id=binding.id,
            kind=kind,
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


async def read_secret(db: AsyncSession, binding: Any) -> dict[str, Any]:
    """Decrypt the binding's slot; fail closed when unusable.

    Missing / revoked / unreadable slots raise ``needs-secret`` (no usable
    secret is stored — the remedy is setting one), not ``auth``.
    """
    kind = slot_for(binding.auth_kind)
    if kind is None:
        raise CalendarCredentialError(
            "This binding's auth kind needs no stored secret.", code="needs-secret"
        )
    row = await _load(db, binding, kind)
    if row is None:
        raise CalendarCredentialError(
            f"No {kind} secret is stored for this binding.", code="needs-secret"
        )
    if row.revoked_at is not None:
        raise CalendarCredentialError(
            "The stored secret was revoked — reconnect the source.",
            code="needs-secret",
        )
    return _unseal(row.sealed_blob, context=str(binding.id))


async def revoke_secrets(db: AsyncSession, binding: Any) -> int:
    """Revoke the binding's slots (reads then fail closed)."""
    result = await db.execute(
        select(CalendarBindingCredential).where(
            CalendarBindingCredential.binding_id == binding.id
        )
    )
    rows = list(result.scalars().all())
    now = datetime.now(UTC)
    for row in rows:
        row.revoked_at = now
    await db.flush()
    return len(rows)


async def delete_secrets(db: AsyncSession, binding: Any) -> int:
    """Hard-delete every slot row (the unbind law: delete every secret)."""
    result = await db.execute(
        select(CalendarBindingCredential).where(
            CalendarBindingCredential.binding_id == binding.id
        )
    )
    rows = list(result.scalars().all())
    for row in rows:
        await db.delete(row)
    await db.flush()
    return len(rows)


async def require_secret(db: AsyncSession, binding: Any) -> dict[str, Any]:
    """The fail-closed gate the sync runs before any network call.

    ``auth_kind='none'`` bindings pass with ``{}``. Any missing, revoked
    or unreadable slot raises :class:`CalendarCredentialError`.
    """
    if slot_for(binding.auth_kind) is None:
        return {}
    return await read_secret(db, binding)


async def slots_summary(db: AsyncSession, binding: Any) -> list[dict[str, Any]]:
    """Read-model projection — never includes the sealed payload."""
    result = await db.execute(
        select(CalendarBindingCredential).where(
            CalendarBindingCredential.binding_id == binding.id
        )
    )
    return [
        {
            "kind": row.kind,
            "kid": row.kid,
            "scopes": list(row.scopes or []),
            "access_expires_at": row.access_expires_at,
            "refresh_expires_at": row.refresh_expires_at,
            "rotated_at": row.rotated_at,
            "revoked_at": row.revoked_at,
        }
        for row in result.scalars().all()
    ]


__all__ = [
    "SLOT_FOR_AUTH_KIND",
    "CalendarCredentialError",
    "delete_secrets",
    "read_secret",
    "require_secret",
    "revoke_secrets",
    "secret_ref_for",
    "set_secret",
    "slot_for",
    "slots_summary",
]
