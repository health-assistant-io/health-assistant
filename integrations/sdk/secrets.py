"""Platform-level secret encryption helpers for integrations.

Any integration can declare secret config fields via
``BaseConfigFlow.get_secret_fields()``. The SDK default
``prepare_for_storage`` / ``prepare_for_read`` implementations in
``integrations.sdk.base`` call into this module so that no integration has to
roll its own crypto and no platform endpoint has to know which integration
owns which secret.

Encryption is opt-in per field. Encrypted values are stored as
``{"_encrypted": "<fernet-token>", "_kid": "<short-key-tag>"}`` so they are
easy to identify, mask on read, and re-encrypt after a key rotation.

The cipher itself is shared family code: this module re-exports
``nx_auth.atrest.SecretCipher`` (rotation ring, ``_kid`` fingerprints,
context binding — see the kit's ``docs/atrest.md`` for the threat model and
the rotation runbook). Health's :class:`SecretCipher` subclass only keeps
the platform-specific construction story: keys come from the app
``Settings`` (``HA_DATA_KEY`` primary, ``HA_DATA_KEY_PREVIOUS``
comma-separated decrypt-only ring) via :meth:`SecretCipher.from_settings`,
and the missing-key error names the ``HA_DATA_KEY`` knob.

Key rotation:

* The primary key is ``settings.HA_DATA_KEY`` (always used to *encrypt*).
* ``settings.HA_DATA_KEY_PREVIOUS`` (comma-separated) holds prior
  keys accepted for *decryption* only, so ciphertext produced before a
  rotation keeps decrypting.
* A short ``_kid`` tag records which key produced a value, so a rotation
  migration can find values that still need re-encryption.
* An optional ``context`` (typically the ``integration_id``) is folded into
  the plaintext envelope so an encrypted blob can't be cut-and-pasted between
  rows in a multi-tenant JSONB column.

If ``HA_DATA_KEY`` is unset, :class:`SecretCipher.from_settings`
raises ``RuntimeError`` — the platform endpoint turns this into a 400 so the
user is told to configure the key before saving secrets. Integrations with
no secret fields are unaffected (the cipher is never constructed).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nx_auth.atrest import KEY_ID_MARKER, SECRET_MARKER
from nx_auth.atrest import SecretCipher as _AtRestSecretCipher

__all__ = [
    "KEY_ID_MARKER",
    "SECRET_MARKER",
    "SecretCipher",
    "decrypt_fields",
    "encrypt_fields",
    "mask_fields",
]


class SecretCipher(_AtRestSecretCipher):
    """SDK surface over the shared family at-rest cipher (``nx_auth.atrest``).

    All cipher machinery — MultiFernet rotation ring, canonical key
    normalization (``_kid`` stability), the context envelope — is inherited
    verbatim from the kit. This subclass adds only the platform plumbing:
    :meth:`from_settings` resolves the key family from the app ``Settings``
    instead of raw env vars, and the missing-key error names ``HA_DATA_KEY``.
    """

    def __init__(
        self,
        key: str | bytes | None,
        *,
        previous: Sequence[str | bytes] | None = None,
    ) -> None:
        if not key:
            raise RuntimeError(
                "HA_DATA_KEY is not configured. Set it (a Fernet "
                "key, base64 32 bytes) to use integrations that store secrets."
            )
        super().__init__(key, previous=previous)

    @classmethod
    def from_settings(cls) -> SecretCipher:
        from app.core.config import get_settings

        settings = get_settings()
        previous = [
            k.strip() for k in (settings.HA_DATA_KEY_PREVIOUS or "").split(",") if k.strip()
        ]
        return cls(settings.HA_DATA_KEY, previous=previous)


def encrypt_fields(
    config: dict[str, Any], fields: list[str], *, context: str | None = None
) -> dict[str, Any]:
    """Return a copy of ``config`` with the given ``fields`` encrypted.

    Fields that are missing, ``None``, ``""``, ``{}`` or ``[]`` are left as-is
    (no point encrypting empties). If ``fields`` is empty, returns the config
    unchanged (no cipher is constructed — no key required).

    ``context`` binds each ciphertext to a row (e.g. the integration id) so it
    can't be replayed into another row — pass the same on decrypt.
    """
    if not fields:
        return dict(config)
    cipher = SecretCipher.from_settings()
    out = dict(config)
    for field in fields:
        val = out.get(field)
        if val in (None, "", {}, []):
            continue
        out[field] = cipher.encrypt_value(val, context=context)
    return out


def decrypt_fields(
    config: dict[str, Any], fields: list[str], *, context: str | None = None
) -> dict[str, Any]:
    """Return a copy of ``config`` with the given ``fields`` decrypted."""
    if not config or not fields:
        return dict(config or {})
    cipher = SecretCipher.from_settings()
    out = dict(config)
    for field in fields:
        if field in out:
            out[field] = cipher.decrypt_value(out[field], context=context)
    return out


def mask_fields(config: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Return a config copy with the given ``fields`` replaced by ``"***"``.

    Used when serving config back to the UI so secrets never leave the server
    in plaintext. Keeps keys present so the frontend can render the form.
    Non-secret fields and empty secret fields are left unchanged.

    Only masks top-level ``fields``; nested secrets (e.g. an ``_oauth`` token
    blob) are encrypted at rest separately and not touched here.
    """
    if not config:
        return {}
    out = dict(config)
    for field in fields:
        val = out.get(field)
        if val in (None, "", {}, []):
            continue
        out[field] = "***"
    return out
