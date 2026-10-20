"""Platform secret encryption at rest (Fernet) — the DATA_KEY family.

The cipher is shared family code: the string helpers (``enc::`` prefix,
``***`` mask marker, tolerant legacy reads) and the Fernet machinery come
from ``nx_auth.atrest`` (see the kit's ``docs/atrest.md`` for the threat
model and the rotation runbook). This module is the thin health adapter:
it resolves the key family from the app ``Settings`` via
:func:`app.core.keys.data_key_family` and keeps health's storage contract —
single source of truth for encrypting secrets that live inside the app's
own tables (e.g. ``AIProviderModel.api_key``). Integrations continue to
use ``integrations.sdk.secrets`` which wraps the same key family inside
``user_config`` JSONB blobs.

The at-rest key is the per-purpose ``HA_DATA_KEY`` — it encrypts at
rest and **never signs anything**, and no key is derived from any other
value. Rotation ring (preserved from the pre-H4 integration key):
``HA_DATA_KEY`` is the primary and the only key that *encrypts*;
``HA_DATA_KEY_PREVIOUS`` (comma-separated) is tried on
*decrypt* only, so ciphertext sealed before a rotation keeps working —
no stored value becomes undecryptable across a key change.

Secrets are encrypted at rest with a Fernet token prefixed by ``enc::``
so storage and transport layers can distinguish them from any legacy
plaintext. Response schemas mask the key on read so it is never returned
to clients.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache

from cryptography.fernet import Fernet
from nx_auth import atrest
from nx_auth.atrest import SecretCipher

logger = logging.getLogger(__name__)

# Re-exported from the shared module — same values the storage layers match on.
ENCRYPTED_PREFIX = atrest.ENCRYPTED_PREFIX
MASK_MARKER = atrest.MASK_MARKER

DATA_KEY_HINT = (
    "HA_DATA_KEY must be 32-byte "
    "urlsafe-base64 key material — a Fernet key. Generate with: python3 -c "
    '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)


class DataKeyError(RuntimeError):
    """The DATA_KEY cannot be used as Fernet key material."""


def fernet_from_data_key(data_key: str) -> Fernet:
    """Fernet view of a DATA_KEY family entry.

    The DATA_KEY *is* the Fernet key. Both the standard padded form
    (``Fernet.generate_key()``, 44 chars) and the auth-kit's unpadded
    token form (43 chars) carry the same 32 key bytes; normalizing the
    base64 padding yields the standard Fernet encoding — no key material
    is derived from anything else.
    """
    padded = data_key + "=" * (-len(data_key) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError) as exc:  # binascii.Error ⊂ ValueError
        raise DataKeyError(
            f"DATA_KEY is not urlsafe-base64 key material: {exc}. {DATA_KEY_HINT}"
        ) from exc
    if len(raw) != 32:
        raise DataKeyError(
            f"DATA_KEY must carry exactly 32 key bytes (got {len(raw)}). {DATA_KEY_HINT}"
        )
    return Fernet(base64.urlsafe_b64encode(raw))


def _resolve_cipher() -> SecretCipher | None:
    """Build the rotation-ring cipher from the configured key family.

    Primary first (encrypts), then the ``HA_DATA_KEY_PREVIOUS`` priors
    (decrypt-only). Returns None if the primary key is unset or invalid —
    callers must handle that case (either by raising or by falling back
    to plaintext storage with a loud warning). An invalid *previous* entry
    only narrows the decrypt ring (warning logged).
    """
    from app.core.keys import data_key_family

    family = data_key_family()
    if not family:
        return None
    try:
        fernet_from_data_key(family[0])
    except DataKeyError as e:
        logger.error("HA_DATA_KEY is set but invalid: %s", e)
        return None
    previous: list[str] = []
    for key in family[1:]:
        try:
            fernet_from_data_key(key)
        except DataKeyError as e:
            logger.warning("ignoring invalid HA_DATA_KEY_PREVIOUS entry: %s", e)
            continue
        previous.append(key)
    return SecretCipher(family[0], previous=previous)


@lru_cache(maxsize=1)
def _fernet_singleton() -> SecretCipher | None:
    # Historical name (tests patch it by name); holds the ring cipher.
    return _resolve_cipher()


def is_encrypted(value: str | None) -> bool:
    """True if the stored value is in the encrypted ``enc::<token>`` form."""
    return atrest.is_encrypted(value)


def encrypt_secret(plaintext: str | None) -> str | None:
    """Encrypt a plaintext string for storage.

    Returns None if the input is None. If no DATA_KEY is configured, raises
    ``RuntimeError`` in production (fail-closed — never silently store secrets
    in cleartext) and only falls back to plaintext in dev/test with a loud
    warning. The production boot guard in ``config.py`` already requires the
    key; this is defence-in-depth for a misconfigured instance.

    Always encrypts under the primary key (MultiFernet order).
    """
    if plaintext is None:
        return None
    if plaintext == "":
        return ""
    if is_encrypted(plaintext):
        return plaintext
    cipher = _fernet_singleton()
    if cipher is None:
        from app.core.config import get_settings

        env = (get_settings().HA_APP_ENV or "").lower()
        if env in ("development", "dev", "test", "testing"):
            logger.warning(
                "HA_DATA_KEY not set — storing secret in PLAINTEXT "
                "(dev/test only). Set the key (Fernet, base64 32 bytes) for prod."
            )
            return plaintext
        raise RuntimeError(
            "Refusing to store a secret in plaintext: HA_DATA_KEY "
            "is not configured (HA_APP_ENV=%s)." % (env or "unset")
        )
    return atrest.encrypt_secret(plaintext, cipher)


def decrypt_secret(stored: str | None) -> str | None:
    """Decrypt a stored value produced by :func:`encrypt_secret`.

    Returns the plaintext. If the input is None, returns None. If the input
    is not in the encrypted form (legacy plaintext), returns it verbatim.
    Tries the full rotation ring (primary, then ``HA_DATA_KEY_PREVIOUS``)
    so pre-rotation ciphertext keeps decrypting. Raises ``ValueError`` if
    the value is encrypted but cannot be decrypted with any family key
    (wrong key, corrupted token) — callers should surface this as a config
    error rather than silently masking.
    """
    if stored is None or stored == "" or not is_encrypted(stored):
        return stored
    cipher = _fernet_singleton()
    if cipher is None:
        raise ValueError("Secret is encrypted but HA_DATA_KEY is not configured")
    try:
        return cipher.decrypt_raw(stored[len(ENCRYPTED_PREFIX) :])
    except ValueError as e:
        raise ValueError("Encrypted secret could not be decrypted") from e


def mask_secret(stored_or_plain: str | None) -> str | None:
    """Mask a secret for display: a bare ``***`` or ``None``.

    Audit 2026-10-18 F18: this used to decrypt the stored value and append
    the last four plaintext characters (``***<last4>``). For structured
    tokens that is key material, and it turned every response-model read
    into a decryption oracle. Masking is now presence-only and never
    touches the cipher: any non-empty value (ciphertext wrapper or legacy
    plaintext) answers ``***``.
    """
    if stored_or_plain is None or stored_or_plain == "":
        return None
    return MASK_MARKER


def looks_masked(value: str | None) -> bool:
    """True if ``value`` looks like a masked secret returned by :func:`mask_secret`.

    Update paths use this to decide whether to preserve the existing key.
    """
    return value is not None and value.startswith(MASK_MARKER)


def reset_cache() -> None:
    """Test hook: clear the cached cipher so a settings change takes effect."""
    _fernet_singleton.cache_clear()
