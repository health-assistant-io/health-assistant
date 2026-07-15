"""Platform secret encryption (Fernet).

Single source of truth for encrypting secrets that live inside the app's own
tables (e.g. ``AIProviderModel.api_key``). Integrations continue to use
``integrations.sdk.secrets`` which wraps the same Fernet key inside
``user_config`` JSONB blobs.

Secrets are encrypted at rest with a Fernet token prefixed by ``enc::`` so
storage and transport layers can distinguish them from any legacy plaintext.
Response schemas mask the key on read so it is never returned to clients.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


# Storage prefix so we can detect encrypted values vs legacy plaintext.
ENCRYPTED_PREFIX = "enc::"

# Marker the client/UI sends back when it wants to preserve the existing
# key (e.g. the user edited a different field and didn't retype the key).
# Anything matching this pattern is treated as "no change" by update paths.
MASK_MARKER = "***"


def _resolve_fernet() -> Optional[Fernet]:
    """Build a Fernet from the configured key, or None if no key is set.

    Reuses ``INTEGRATION_SECRET_KEY`` (a Fernet-format base64 key) so there
    is a single platform secret for both integrations and AI keys. If the
    key is unset, returns None — callers must handle that case (either by
    raising or by falling back to plaintext storage with a loud warning).
    """
    from app.core.config import get_settings

    key = get_settings().INTEGRATION_SECRET_KEY
    if not key:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        logger.error("INTEGRATION_SECRET_KEY is set but invalid: %s", e)
        return None


@lru_cache(maxsize=1)
def _fernet_singleton() -> Optional[Fernet]:
    return _resolve_fernet()


def is_encrypted(value: Optional[str]) -> bool:
    """True if the stored value is in the encrypted ``enc::<token>`` form."""
    return bool(value) and value.startswith(ENCRYPTED_PREFIX)


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt a plaintext string for storage.

    Returns None if the input is None. If no Fernet key is configured, raises
    ``RuntimeError`` in production (fail-closed — never silently store secrets
    in cleartext) and only falls back to plaintext in dev/test with a loud
    warning. The production boot guard in ``config.py`` already requires the
    key; this is defence-in-depth for a misconfigured instance.
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
                "INTEGRATION_SECRET_KEY not set — storing secret in PLAINTEXT "
                "(dev/test only). Set the key (Fernet, base64 32 bytes) for prod."
            )
            return plaintext
        raise RuntimeError(
            "Refusing to store a secret in plaintext: INTEGRATION_SECRET_KEY "
            "is not configured (APP_ENV=%s)." % (env or "unset")
        )
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_secret(stored: Optional[str]) -> Optional[str]:
    """Decrypt a stored value produced by :func:`encrypt_secret`.

    Returns the plaintext. If the input is None, returns None. If the input
    is not in the encrypted form (legacy plaintext), returns it verbatim.
    Raises ``ValueError`` if the value is encrypted but cannot be decrypted
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
            "Secret is encrypted but INTEGRATION_SECRET_KEY is not configured"
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
