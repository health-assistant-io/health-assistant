"""Platform secret encryption at rest (Fernet) — the DATA_KEY family.

Single source of truth for encrypting secrets that live inside the app's
own tables (e.g. ``AIProviderModel.api_key``). Integrations continue to
use ``integrations.sdk.secrets`` which wraps the same key family inside
``user_config`` JSONB blobs.

Identity-auth §8 (plan 16 H4): the cipher key is the per-purpose
``HA_DATA_KEY`` — it encrypts at
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
from typing import Optional, Union

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger(__name__)


# Storage prefix so we can detect encrypted values vs legacy plaintext.
ENCRYPTED_PREFIX = "enc::"

# Marker the client/UI sends back when it wants to preserve the existing
# key (e.g. the user edited a different field and didn't retype the key).
# Anything matching this pattern is treated as "no change" by update paths.
MASK_MARKER = "***"

DATA_KEY_HINT = (
    "HA_DATA_KEY must be 32-byte "
    "urlsafe-base64 key material — a Fernet key. Generate with: python3 -c "
    '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)


class DataKeyError(RuntimeError):
    """The DATA_KEY cannot be used as Fernet key material."""


def fernet_from_data_key(data_key: str) -> Fernet:
    """Fernet view of a DATA_KEY family entry (identity-auth §8).

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


def _resolve_fernet() -> Optional[MultiFernet]:
    """Build the rotation-ring cipher from the configured key family.

    Primary first (encrypts), then the ``HA_DATA_KEY_PREVIOUS`` priors
    (decrypt-only). Returns None if the primary key is unset or invalid —
    callers must handle that case (either by raising or by falling back
    to plaintext storage with a loud warning).
    """
    from app.core.keys import data_key_family

    family = data_key_family()
    if not family:
        return None
    fernets: list[Fernet] = []
    for index, key in enumerate(family):
        try:
            fernets.append(fernet_from_data_key(key))
        except DataKeyError as e:
            if index == 0:
                logger.error("HA_DATA_KEY is set but invalid: %s", e)
                return None
            # A bad *previous* entry only narrows the decrypt ring.
            logger.warning("ignoring invalid HA_DATA_KEY_PREVIOUS entry: %s", e)
    if not fernets:  # pragma: no cover — primary handled above
        return None
    return MultiFernet(fernets)


@lru_cache(maxsize=1)
def _fernet_singleton() -> Optional[MultiFernet]:
    return _resolve_fernet()


def is_encrypted(value: Optional[str]) -> bool:
    """True if the stored value is in the encrypted ``enc::<token>`` form."""
    return bool(value) and value.startswith(ENCRYPTED_PREFIX)


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
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
    fernet = _fernet_singleton()
    if fernet is None:
        from app.core.config import get_settings

        env = (get_settings().APP_ENV or "").lower()
        if env in ("development", "dev", "test", "testing"):
            logger.warning(
                "HA_DATA_KEY not set — storing secret in PLAINTEXT "
                "(dev/test only). Set the key (Fernet, base64 32 bytes) for prod."
            )
            return plaintext
        raise RuntimeError(
            "Refusing to store a secret in plaintext: HA_DATA_KEY "
            "is not configured (APP_ENV=%s)." % (env or "unset")
        )
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_secret(stored: Optional[str]) -> Optional[str]:
    """Decrypt a stored value produced by :func:`encrypt_secret`.

    Returns the plaintext. If the input is None, returns None. If the input
    is not in the encrypted form (legacy plaintext), returns it verbatim.
    Tries the full rotation ring (primary, then ``HA_DATA_KEY_PREVIOUS``)
    so pre-rotation ciphertext keeps decrypting. Raises ``ValueError`` if
    the value is encrypted but cannot be decrypted with any family key
    (wrong key, corrupted token) — callers should surface this as a config
    error rather than silently masking.
    """
    if stored is None:
        return None
    if stored == "":
        return ""
    if not is_encrypted(stored):
        return stored
    token = stored[len(ENCRYPTED_PREFIX) :].encode("utf-8")
    fernet = _fernet_singleton()
    if fernet is None:
        raise ValueError(
            "Secret is encrypted but HA_DATA_KEY is not configured"
        )
    try:
        return fernet.decrypt(token).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Encrypted secret could not be decrypted") from e


def mask_secret(stored_or_plain: Optional[str], visible_tail: int = 4) -> Optional[str]:
    """Mask a secret for display: returns ``***<last N chars>`` or ``None``.

    Accepts either an encrypted value (decrypts first) or a plaintext value.
    On any error (no key, bad token), returns ``***`` so the UI never leaks
    the encrypted token or partial bytes.
    """
    if stored_or_plain is None or stored_or_plain == "":
        return None
    try:
        plain = decrypt_secret(stored_or_plain)
    except ValueError:
        return MASK_MARKER
    if plain is None or plain == "":
        return None
    if len(plain) <= visible_tail:
        return MASK_MARKER
    return f"{MASK_MARKER}{plain[-visible_tail:]}"


def looks_masked(value: Optional[str]) -> bool:
    """True if ``value`` looks like a masked secret returned by :func:`mask_secret`.

    Update paths use this to decide whether to preserve the existing key.
    """
    return bool(value) and value.startswith(MASK_MARKER)


def reset_cache() -> None:
    """Test hook: clear the cached Fernet so a settings change takes effect."""
    _fernet_singleton.cache_clear()
