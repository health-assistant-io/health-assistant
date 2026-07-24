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

Key rotation (Phase 1.2 of the SDK hardening plan):

* The primary key is ``settings.INTEGRATION_SECRET_KEY`` (always used to
  *encrypt*).
* ``settings.INTEGRATION_SECRET_KEY_PREVIOUS`` (comma-separated) holds prior
  keys accepted for *decryption* only, so ciphertext produced before a
  rotation keeps decrypting.
* A short ``_kid`` tag records which key produced a value, so a rotation
  migration can find values that still need re-encryption.
* An optional ``context`` (typically the ``integration_id``) is folded into
  the plaintext envelope so an encrypted blob can't be cut-and-pasted between
  rows in a multi-tenant JSONB column.

If ``INTEGRATION_SECRET_KEY`` is unset, :class:`SecretCipher.from_settings`
raises ``RuntimeError`` — the platform endpoint turns this into a 400 so the
user is told to configure the key before saving secrets. Integrations with
no secret fields are unaffected (the cipher is never constructed).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger(__name__)

SECRET_MARKER = "_encrypted"
KEY_ID_MARKER = "_kid"

# Length to which a key is truncated for the ``_kid`` tag. Short enough to be
# a cheap fingerprint, long enough to distinguish keys in practice.
_KEY_TAG_LEN = 8


def _key_tag(key: Union[str, bytes]) -> str:
    """A short, stable fingerprint of a Fernet key (not security-sensitive)."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    # Fernet keys are base64url; take the first chars as the tag.
    import hashlib

    return hashlib.sha256(key).hexdigest()[:_KEY_TAG_LEN]


class SecretCipher:
    """Fernet wrapper for encrypting tagged fields inside ``user_config``.

    Uses :class:`~cryptography.fernet.MultiFernet` so a rotation is non-
    disruptive: the first key in the list is the primary (used to encrypt),
    the rest are accepted for decryption only. Construct via
    :meth:`from_settings` to pick up both the primary and any
    ``INTEGRATION_SECRET_KEY_PREVIOUS`` keys.
    """

    def __init__(self, key: Union[str, bytes, None], *, previous: Optional[List[Union[str, bytes]]] = None) -> None:
        keys: List[Union[str, bytes]] = []
        if not key:
            raise RuntimeError(
                "INTEGRATION_SECRET_KEY is not configured. Set it (a Fernet "
                "key, base64 32 bytes) to use integrations that store secrets."
            )
        keys.append(key)
        for prev in previous or []:
            if prev and prev not in keys:
                keys.append(prev)
        fernets = [Fernet(k.encode("utf-8") if isinstance(k, str) else k) for k in keys]
        self._multi = MultiFernet(fernets)
        self._primary = fernets[0]
        self._primary_tag = _key_tag(keys[0])

    @classmethod
    def from_settings(cls) -> "SecretCipher":
        from app.core.config import get_settings

        settings = get_settings()
        previous = [k.strip() for k in (settings.INTEGRATION_SECRET_KEY_PREVIOUS or "").split(",") if k.strip()]
        return cls(settings.INTEGRATION_SECRET_KEY, previous=previous)

    def encrypt_value(self, value: Any, *, context: Optional[str] = None) -> Dict[str, str]:
        """Encrypt a single value -> ``{"_encrypted": "<token>", "_kid": "<tag>"}``.

        ``context`` (e.g. the owning ``integration_id``) is folded into the
        plaintext envelope so the resulting ciphertext can't be replayed into
        a different row — :meth:`decrypt_value` rejects a mismatch. Pass the
        same context on decrypt.
        """
        if value is None:
            return {}
        if isinstance(value, (dict, list)):
            payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        else:
            payload = str(value)
        token = self._primary.encrypt(self._envelope(payload, context)).decode("utf-8")
        return {SECRET_MARKER: token, KEY_ID_MARKER: self._primary_tag}

    def decrypt_value(self, wrapped: Any, *, context: Optional[str] = None) -> Any:
        """Inverse of :meth:`encrypt_value`.

        Returns the input unchanged if it isn't an encrypted wrapper so that
        plain-text legacy configs keep working. Raises :class:`ValueError`
        if the value can't be decrypted (key mismatch / rotated) or if the
        context doesn't match the one used at encrypt time.
        """
        if not isinstance(wrapped, dict) or SECRET_MARKER not in wrapped:
            return wrapped
        token = wrapped[SECRET_MARKER].encode("utf-8")
        try:
            plaintext = self._multi.decrypt(token).decode("utf-8")
        except InvalidToken as e:
            raise ValueError(
                "Encrypted config value could not be decrypted "
                "(key missing/rotated? set INTEGRATION_SECRET_KEY_PREVIOUS)."
            ) from e
        value_str, stored_context = self._split_envelope(plaintext)
        if stored_context is not None and context is not None and stored_context != context:
            raise ValueError(
                "Encrypted config value was encrypted for a different "
                "context (integration/row mismatch)."
            )
        try:
            return json.loads(value_str)
        except (ValueError, json.JSONDecodeError):
            return value_str

    # --- envelope helpers -------------------------------------------------

    @staticmethod
    def _envelope(payload: str, context: Optional[str]) -> bytes:
        """Pack ``payload`` with an optional AAD-like context tag.

        Format: ``ctx:<context>\n<payload>`` when context is given, else the
        raw payload. Fernet has no native AAD, so we bind the context into
        the plaintext (verified on decrypt). A value encrypted *without* a
        context decrypts to its raw payload regardless of the decrypt-time
        context, preserving backwards compatibility with pre-rotation values.
        """
        if context:
            return f"ctx:{context}\n{payload}".encode("utf-8")
        return payload.encode("utf-8")

    @staticmethod
    def _split_envelope(plaintext: str) -> "tuple[str, Optional[str]]":
        if plaintext.startswith("ctx:"):
            newline = plaintext.find("\n")
            if newline != -1:
                ctx = plaintext[4:newline]
                return plaintext[newline + 1 :], ctx
        return plaintext, None


def encrypt_fields(config: Dict[str, Any], fields: List[str], *, context: Optional[str] = None) -> Dict[str, Any]:
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


def decrypt_fields(config: Dict[str, Any], fields: List[str], *, context: Optional[str] = None) -> Dict[str, Any]:
    """Return a copy of ``config`` with the given ``fields`` decrypted."""
    if not config or not fields:
        return dict(config or {})
    cipher = SecretCipher.from_settings()
    out = dict(config)
    for field in fields:
        if field in out:
            out[field] = cipher.decrypt_value(out[field], context=context)
    return out


def mask_fields(config: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
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
