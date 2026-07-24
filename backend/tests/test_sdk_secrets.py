"""Unit tests for ``integrations.sdk.secrets`` — Fernet field encryption.

Covers the Phase 1.2 hardening: ``MultiFernet`` key rotation (decrypt with a
previous key after rotating the primary), ``_kid`` tagging, and the opt-in
``context`` binding that stops an encrypted blob being replayed into another
row. Legacy values (no ``_kid``, no envelope) keep decrypting.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from integrations.sdk.secrets import (
    KEY_ID_MARKER,
    SECRET_MARKER,
    SecretCipher,
    decrypt_fields,
    encrypt_fields,
    mask_fields,
)


@pytest.fixture
def cipher_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def cipher(cipher_key) -> SecretCipher:
    return SecretCipher(cipher_key)


# ---------------------------------------------------------------------------
# Basic encrypt / decrypt round-trip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_string_roundtrip(cipher):
    wrapped = cipher.encrypt_value("hunter2")
    assert SECRET_MARKER in wrapped
    assert KEY_ID_MARKER in wrapped
    assert cipher.decrypt_value(wrapped) == "hunter2"


def test_encrypt_decrypt_dict_roundtrip(cipher):
    wrapped = cipher.encrypt_value({"a": 1, "b": [2, 3]})
    assert cipher.decrypt_value(wrapped) == {"a": 1, "b": [2, 3]}


def test_encrypt_none_returns_empty(cipher):
    assert cipher.encrypt_value(None) == {}


def test_encrypt_stores_kid_tag(cipher):
    wrapped = cipher.encrypt_value("x")
    # The tag is present and non-empty.
    assert isinstance(wrapped[KEY_ID_MARKER], str) and wrapped[KEY_ID_MARKER]


def test_decrypt_passthrough_for_plaintext(cipher):
    """Non-encrypted values come back unchanged (legacy-safe)."""
    assert cipher.decrypt_value("plain") == "plain"
    assert cipher.decrypt_value({"foo": "bar"}) == {"foo": "bar"}
    assert cipher.decrypt_value(None) is None


def test_decrypt_legacy_value_without_kid(cipher_key):
    """A value encrypted by the pre-rotation code (single Fernet, no envelope,
    no ``_kid``) must still decrypt after the MultiFernet rewrite."""
    legacy_fernet = Fernet(cipher_key.encode())
    token = legacy_fernet.encrypt(b"legacy-secret").decode()
    legacy_wrapped = {SECRET_MARKER: token}  # no _kid
    cipher = SecretCipher(cipher_key)
    assert cipher.decrypt_value(legacy_wrapped) == "legacy-secret"


# ---------------------------------------------------------------------------
# Key rotation (MultiFernet)
# ---------------------------------------------------------------------------


def test_rotation_decrypts_old_ciphertext():
    """After rotating the primary key, values encrypted with the OLD key
    still decrypt (via INTEGRATION_SECRET_KEY_PREVIOUS)."""
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    old_cipher = SecretCipher(old_key)
    wrapped = old_cipher.encrypt_value("secret-from-old-key")

    # New cipher knows the new primary but accepts the old key for decrypt.
    rotated = SecretCipher(new_key, previous=[old_key])
    assert rotated.decrypt_value(wrapped) == "secret-from-old-key"


def test_rotation_encrypts_with_new_key():
    """Newly-encrypted values are stamped with the NEW primary's tag, so a
    migration can find rows that still carry the old tag."""
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    old_cipher = SecretCipher(old_key)
    rotated = SecretCipher(new_key, previous=[old_key])

    old_wrapped = old_cipher.encrypt_value("x")
    new_wrapped = rotated.encrypt_value("x")

    assert old_wrapped[KEY_ID_MARKER] != new_wrapped[KEY_ID_MARKER]
    # Both decrypt under the rotated cipher.
    assert rotated.decrypt_value(old_wrapped) == "x"
    assert rotated.decrypt_value(new_wrapped) == "x"


def test_rotation_without_previous_key_fails():
    """A value encrypted with an unknown key surfaces a clear error mentioning
    rotation — not a silent plaintext passthrough."""
    other_key = Fernet.generate_key().decode()
    primary = Fernet.generate_key().decode()
    wrapped = SecretCipher(other_key).encrypt_value("x")
    cipher = SecretCipher(primary)  # no previous
    with pytest.raises(ValueError, match="rotated"):
        cipher.decrypt_value(wrapped)


def test_empty_key_raises():
    with pytest.raises(RuntimeError, match="INTEGRATION_SECRET_KEY is not configured"):
        SecretCipher(None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        SecretCipher("")


# ---------------------------------------------------------------------------
# Context (AAD-like) binding
# ---------------------------------------------------------------------------


def test_context_binding_roundtrip(cipher):
    wrapped = cipher.encrypt_value("tok", context="integration-abc")
    assert cipher.decrypt_value(wrapped, context="integration-abc") == "tok"


def test_context_mismatch_rejected(cipher):
    wrapped = cipher.encrypt_value("tok", context="integration-abc")
    with pytest.raises(ValueError, match="different context"):
        cipher.decrypt_value(wrapped, context="integration-xyz")


def test_context_value_decrypts_without_context(cipher):
    """A context-bound value still decrypts when the caller omits context
    (best-effort / backwards-compatible). The binding only *rejects* an
    explicit wrong context."""
    wrapped = cipher.encrypt_value("tok", context="integration-abc")
    assert cipher.decrypt_value(wrapped) == "tok"


# ---------------------------------------------------------------------------
# Field-level helpers
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_fields_roundtrip(cipher_key, monkeypatch):
    _patch_settings(monkeypatch, cipher_key)
    config = {"api_secret": "hunter2", "instance_name": "my-bridge"}
    enc = encrypt_fields(config, ["api_secret"])
    assert enc["api_secret"] != "hunter2"
    assert enc["api_secret"][SECRET_MARKER]
    # Non-secret field untouched.
    assert enc["instance_name"] == "my-bridge"
    dec = decrypt_fields(enc, ["api_secret"])
    assert dec["api_secret"] == "hunter2"


def test_encrypt_fields_skips_empties(cipher_key, monkeypatch):
    _patch_settings(monkeypatch, cipher_key)
    config = {"api_secret": "", "token": None, "nested": {}}
    enc = encrypt_fields(config, ["api_secret", "token", "nested"])
    assert enc == {"api_secret": "", "token": None, "nested": {}}


def test_encrypt_fields_no_fields_returns_copy(cipher_key, monkeypatch):
    _patch_settings(monkeypatch, cipher_key)
    config = {"a": 1}
    out = encrypt_fields(config, [])
    assert out == config
    assert out is not config  # a copy, not the same dict


def test_mask_fields_replaces_secrets(cipher_key, monkeypatch):
    _patch_settings(monkeypatch, cipher_key)
    config = {"api_secret": "hunter2", "name": "bridge"}
    enc = encrypt_fields(config, ["api_secret"])
    masked = mask_fields(enc, ["api_secret"])
    assert masked["api_secret"] == "***"
    assert masked["name"] == "bridge"


def test_mask_fields_keeps_empty_secrets():
    config = {"api_secret": "", "name": "bridge"}
    masked = mask_fields(config, ["api_secret"])
    assert masked["api_secret"] == ""  # not masked -> UI shows empty input


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_settings(monkeypatch, primary_key: str, previous: str = ""):
    """Make ``SecretCipher.from_settings()`` use the given keys without booting
    the full app config. ``encrypt_fields`` / ``decrypt_fields`` call the
    classmethod, so patching it covers the field-level helpers too."""
    from integrations.sdk import secrets as secrets_mod

    monkeypatch.setattr(
        secrets_mod.SecretCipher,
        "from_settings",
        classmethod(lambda cls: secrets_mod.SecretCipher(primary_key)),
    )
