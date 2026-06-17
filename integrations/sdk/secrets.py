"""Platform-level secret encryption helpers for integrations.

Any integration can declare secret config fields via
``BaseConfigFlow.get_secret_fields()``. The SDK default
``prepare_for_storage`` / ``prepare_for_read`` implementations in
``integrations.sdk.base`` call into this module so that no integration has to
roll its own crypto and no platform endpoint has to know which integration
owns which secret.

Encryption is opt-in per field. Encrypted values are stored as
``{"_encrypted": "<fernet-token>"}`` so they are easy to identify and mask on
read. The key is read from ``settings.INTEGRATION_SECRET_KEY``.

If ``INTEGRATION_SECRET_KEY`` is unset, :class:`SecretCipher.from_settings`
raises ``RuntimeError`` — the platform endpoint turns this into a 400 so the
user is told to configure the key before saving secrets. Integrations with
no secret fields are unaffected (the cipher is never constructed).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

SECRET_MARKER = "_encrypted"


class SecretCipher:
    """Fernet wrapper for encrypting tagged fields inside ``user_config``."""

    def __init__(self, key) -> None:
        if not key:
            raise RuntimeError(
                "INTEGRATION_SECRET_KEY is not configured. Set it (a Fernet "
                "key, base64 32 bytes) to use integrations that store secrets."
            )
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    @classmethod
    def from_settings(cls) -> "SecretCipher":
        from app.core.config import get_settings

        return cls(get_settings().INTEGRATION_SECRET_KEY)

    def encrypt_value(self, value: Any) -> Dict[str, str]:
        """Encrypt a single value -> ``{"_encrypted": "<token>"}``."""
        if value is None:
            return {}
        if isinstance(value, (dict, list)):
            payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        else:
            payload = str(value)
        token = self._fernet.encrypt(payload.encode("utf-8")).decode("utf-8")
        return {SECRET_MARKER: token}

    def decrypt_value(self, wrapped: Any) -> Any:
        """Inverse of :meth:`encrypt_value`.

        Returns the input unchanged if it isn't an encrypted wrapper so that
        plain-text legacy configs keep working.
        """
        if not isinstance(wrapped, dict) or SECRET_MARKER not in wrapped:
            return wrapped
        token = wrapped[SECRET_MARKER].encode("utf-8")
        try:
            plaintext = self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as e:
            raise ValueError("Encrypted config value could not be decrypted.") from e
        try:
            return json.loads(plaintext)
        except Exception:
            return plaintext


def encrypt_fields(config: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    """Return a copy of ``config`` with the given ``fields`` encrypted.

    Fields that are missing, ``None``, ``""``, ``{}`` or ``[]`` are left as-is
    (no point encrypting empties). If ``fields`` is empty, returns the config
    unchanged (no cipher is constructed — no key required).
    """
    if not fields:
        return dict(config)
    cipher = SecretCipher.from_settings()
    out = dict(config)
    for field in fields:
        val = out.get(field)
        if val in (None, "", {}, []):
            continue
        out[field] = cipher.encrypt_value(val)
    return out


def decrypt_fields(config: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    """Return a copy of ``config`` with the given ``fields`` decrypted."""
    if not config or not fields:
        return dict(config or {})
    cipher = SecretCipher.from_settings()
    out = dict(config)
    for field in fields:
        if field in out:
            out[field] = cipher.decrypt_value(out[field])
    return out


def mask_fields(config: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    """Return a config copy with the given ``fields`` replaced by ``"***"``.

    Used when serving config back to the UI so secrets never leave the server
    in plaintext. Keeps keys present so the frontend can render the form.
    Non-secret fields and empty secret fields are left unchanged.
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
