"""Key separation (identity-auth §8, plan 16 H4) — the per-purpose key ring.

Covers the three §8 invariants H4 enforces:

1. **Signing separation** — session-family kinds (session, api, invite,
   download) sign with ``HA_SESSION_KEY``; refresh signs with
   ``HA_REFRESH_KEY`` and nothing else; cross-family tokens fail
   verification (a session token signed by the refresh key — or by the
   DATA_KEY — is not a session token).
2. **DATA_KEY family** — ``HA_DATA_KEY`` is the Fernet at-rest key and never
   signs; the rotation ring (``HA_DATA_KEY_PREVIOUS``) keeps old
   ciphertext decryptable while new writes seal under the primary. No
   stored value becomes undecryptable across the H4 fold or a rotation
   (the pre-H4 ``INTEGRATION_SECRET_KEY`` env names are retired — rename
   them in .env, same values).
3. **Boot guards** — servers must pin strong, distinct keys via env
   (missing/weak/placeholder/partial/cross-purpose pins refuse to
   boot); dev/test auto-generate ephemeral keys.
"""

import base64
import datetime

import jwt as pyjwt
import pytest
from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from app.core import encryption
from app.core.config import Settings, settings
from app.core.encryption import (
    DataKeyError,
    decrypt_secret,
    encrypt_secret,
    fernet_from_data_key,
)
from app.core.keys import data_key_family, key_for, verification_keys

# Every test here implements identity-auth §18.12 (key separation) and the
from app.core.security import (
    API_TOKEN_KIND,
    DOWNLOAD_TOKEN_KIND,
    INVITE_TOKEN_KIND,
    REFRESH_TOKEN_KIND,
    SESSION_TOKEN_KIND,
    _decode_with,
    create_api_access_token,
    create_invite_token,
    create_presigned_token,
    create_refresh_token,
    create_session_access_token,
    decode_refresh_token,
    verify_access_token,
)

# Canonical strong keys live in tests/settings_factory (plan 23 D5) —
# imported under the names this module has always used.
from tests.settings_factory import PROD_REFRESH_KEY as REFRESH_KEY
from tests.settings_factory import PROD_SESSION_KEY as SESSION_KEY
from tests.settings_factory import dev_settings, prod_settings

OLD_DATA_KEY = Fernet.generate_key().decode()
NEW_DATA_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _pinned_ring(monkeypatch):
    """Pin the three keys on the live settings singleton (most code reads
    ``settings`` directly); clear the cached at-rest cipher around each
    test so the pin takes effect."""
    monkeypatch.setattr(settings, "HA_SESSION_KEY", SESSION_KEY)
    monkeypatch.setattr(settings, "HA_REFRESH_KEY", REFRESH_KEY)
    monkeypatch.setattr(settings, "HA_DATA_KEY", NEW_DATA_KEY)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
    encryption.reset_cache()
    yield
    encryption.reset_cache()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Constructing fresh Settings objects must not see ambient pins."""
    for var in (
        "HA_SESSION_KEY",
        "HA_REFRESH_KEY",
        "HA_DATA_KEY",
        "HA_DATA_KEY_PREVIOUS",
        "INTEGRATION_SECRET_KEY",
        "INTEGRATION_SECRET_KEY_PREVIOUS",
    ):
        monkeypatch.delenv(var, raising=False)


# ===========================================================================
# 1. Signing separation — kind → key map + cross-family rejection
# ===========================================================================


def test_kind_to_key_map():
    """session/api/invite/download ride the session family; refresh alone."""
    assert key_for(SESSION_TOKEN_KIND) == SESSION_KEY
    assert key_for(API_TOKEN_KIND) == SESSION_KEY
    assert key_for(INVITE_TOKEN_KIND) == SESSION_KEY
    assert key_for(DOWNLOAD_TOKEN_KIND) == SESSION_KEY
    assert key_for(REFRESH_TOKEN_KIND) == REFRESH_KEY
    assert set(verification_keys()) == {SESSION_KEY, REFRESH_KEY}


def test_unknown_kind_fails_closed():
    with pytest.raises(ValueError, match="unknown token kind"):
        key_for("passwordless")


def _forge(kind: str, key: str, **extra) -> str:
    """Hand-mint a contract-shaped JWT under an arbitrary key."""
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "iss": "health",
        "token_kind": kind,
        "sub": "u-1",
        "jti": "j" * 8,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=5),
    }
    payload.update(extra)
    return pyjwt.encode(payload, key, algorithm="HS256")


def test_session_token_signed_by_refresh_key_is_rejected():
    """THE §8 cross-family case: refresh-key-signed session token fails."""
    token = _forge(SESSION_TOKEN_KIND, REFRESH_KEY)
    assert verify_access_token(token) is None


def test_refresh_token_signed_by_session_key_is_rejected():
    token = _forge(REFRESH_TOKEN_KIND, SESSION_KEY)
    assert decode_refresh_token(token) is None


def test_data_key_never_signs_a_usable_token():
    """A JWT signed by the DATA_KEY verifies under no signing family."""
    forged = _forge(SESSION_TOKEN_KIND, NEW_DATA_KEY)
    assert verify_access_token(forged) is None
    assert decode_refresh_token(forged) is None


def test_real_mints_verify_under_their_own_family_only():
    session, _ = create_session_access_token({"user_id": "u-1"})
    refresh, _ = create_refresh_token({"user_id": "u-1"})
    api, _ = create_api_access_token(client_id="c-1", tenant_id="t-1", scopes=["system/*.read"])
    invite, _ = create_invite_token("t-1", email="a@b.c")
    download = create_presigned_token("doc-1", "u-1")

    # Each verifies under its own family key…
    assert _decode_with(session, SESSION_KEY)["token_kind"] == "session"
    assert _decode_with(refresh, REFRESH_KEY)["token_kind"] == "refresh"
    assert _decode_with(api, SESSION_KEY)["token_kind"] == "api"
    assert _decode_with(invite, SESSION_KEY)["token_kind"] == "invite"
    assert _decode_with(download, SESSION_KEY)["token_kind"] == "download"

    # …and under no other key of the ring.
    for token in (session, api, invite, download):
        assert _decode_with(token, REFRESH_KEY) is None
        assert _decode_with(token, NEW_DATA_KEY) is None
    assert _decode_with(refresh, SESSION_KEY) is None

    # Kind enforcement stays mutually exclusive within the session family.
    assert verify_access_token(refresh) is None
    assert verify_access_token(api) is None
    assert verify_access_token(invite) is None
    assert verify_access_token(download) is None
    assert decode_refresh_token(session) is None


def test_retired_secret_key_no_longer_signs():
    """SECRET_KEY is gone from signing: a token signed by the retired
    single-key value (e.g. a pre-H4 leftover) verifies under nothing."""
    legacy_secret = "the-pre-h4-all-purpose-secret-key-0123456789"
    stale = _forge(SESSION_TOKEN_KIND, legacy_secret)
    assert verify_access_token(stale) is None
    assert decode_refresh_token(stale) is None
    assert "SECRET_KEY" not in Settings.model_fields


# ===========================================================================
# 2. Boot guards (config) — server requires strong distinct pins; dev generates
# ===========================================================================


def test_prod_missing_signing_keys_refuse_to_boot():
    with pytest.raises(ValidationError, match="HA_SESSION_KEY"):
        prod_settings(HA_SESSION_KEY=None, HA_REFRESH_KEY=None)


def test_prod_missing_refresh_key_refuse_to_boot():
    with pytest.raises(ValidationError, match="HA_REFRESH_KEY"):
        prod_settings(HA_REFRESH_KEY=None)


def test_prod_weak_and_placeholder_signing_keys_refuse_to_boot():
    for bad in ("change_this_to_a_secure_random_string", "short-key", "x" * 48):
        with pytest.raises(ValidationError):
            prod_settings(HA_SESSION_KEY=bad)
        with pytest.raises(ValidationError):
            prod_settings(HA_REFRESH_KEY=bad)


def test_partial_signing_pin_fails_closed_in_every_env():
    for make in (prod_settings, dev_settings):
        with pytest.raises(ValidationError, match="partial signing-key pin"):
            make(HA_SESSION_KEY=SESSION_KEY, HA_REFRESH_KEY=None)


def test_prod_shared_key_across_purposes_refuses_to_boot():
    with pytest.raises(ValidationError, match="distinct"):
        prod_settings(
            HA_SESSION_KEY=SESSION_KEY,
            HA_REFRESH_KEY=SESSION_KEY,
            HA_DATA_KEY=NEW_DATA_KEY,
        )
    # A signing key reused as the Fernet DATA_KEY is cross-purpose reuse.
    with pytest.raises(ValidationError, match="distinct"):
        prod_settings(
            HA_SESSION_KEY=NEW_DATA_KEY,
            HA_REFRESH_KEY=REFRESH_KEY,
            HA_DATA_KEY=NEW_DATA_KEY,
        )


def test_prod_strong_distinct_pins_boot():
    s = prod_settings(HA_DATA_KEY=NEW_DATA_KEY)
    assert s.HA_SESSION_KEY == SESSION_KEY
    assert s.HA_REFRESH_KEY == REFRESH_KEY
    assert s.HA_DATA_KEY == NEW_DATA_KEY


def test_prod_data_key_required_and_must_be_fernet_material():
    with pytest.raises(ValidationError, match="HA_DATA_KEY"):
        prod_settings(HA_DATA_KEY=None)
    with pytest.raises(ValidationError, match="32 bytes"):
        prod_settings(HA_DATA_KEY="not-fernet-material-at-all")


def test_dev_generates_strong_distinct_keys():
    s = dev_settings()
    assert len(s.HA_SESSION_KEY) >= 43  # token_urlsafe(32) ⇒ 43 chars
    assert len(s.HA_REFRESH_KEY) >= 43
    assert len({s.HA_SESSION_KEY, s.HA_REFRESH_KEY, s.HA_DATA_KEY}) == 3
    fernet_from_data_key(s.HA_DATA_KEY)  # dev data key is valid Fernet material
    # The keys are random per process, not fixed constants.
    other = dev_settings()
    assert other.HA_SESSION_KEY != s.HA_SESSION_KEY
    assert other.HA_REFRESH_KEY != s.HA_REFRESH_KEY


def test_legacy_env_names_are_gone(monkeypatch):
    """No-legacy policy: the pre-H4 INTEGRATION_SECRET_KEY env names are
    no longer accepted — an operator who still sets them gets the
    canonical-missing behavior (ephemeral dev key / production boot
    refusal), loudly, instead of a silent alias feed. The rename is
    value-preserving: moving the same key value to HA_DATA_KEY keeps
    every sealed ring decryptable."""
    monkeypatch.setenv("INTEGRATION_SECRET_KEY", OLD_DATA_KEY)
    monkeypatch.setenv("INTEGRATION_SECRET_KEY_PREVIOUS", f"{NEW_DATA_KEY},{OLD_DATA_KEY}")
    s = dev_settings()
    assert s.HA_DATA_KEY != OLD_DATA_KEY  # ignored: fresh ephemeral dev key
    assert s.HA_DATA_KEY_PREVIOUS == ""  # ignored entirely
    # The legacy attribute aliases are gone with the env names.
    assert not hasattr(settings, "INTEGRATION_SECRET_KEY")
    assert not hasattr(dev_settings(), "INTEGRATION_SECRET_KEY")


# ===========================================================================
# 3. DATA_KEY family — Fernet at rest, rotation ring preserved
# ===========================================================================


def test_data_key_round_trip():
    sealed = encrypt_secret("sk-live-abc")
    assert sealed.startswith("enc::")
    assert decrypt_secret(sealed) == "sk-live-abc"


def test_fernet_from_data_key_accepts_both_token_forms():
    raw = base64.urlsafe_b64decode(NEW_DATA_KEY)
    unpadded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    token_cipher = fernet_from_data_key(unpadded)
    padded_cipher = fernet_from_data_key(NEW_DATA_KEY)
    sealed = token_cipher.encrypt(b"payload").decode()
    assert padded_cipher.decrypt(sealed.encode()) == b"payload"


def test_fernet_from_data_key_rejects_non_key_material():
    with pytest.raises(DataKeyError, match="32 key bytes"):
        fernet_from_data_key("z" * 48)
    with pytest.raises(DataKeyError):
        fernet_from_data_key("short")
    with pytest.raises(DataKeyError, match="DATA_KEY"):
        fernet_from_data_key("!! not base64 !! not base64 !! not base64 ==")


def test_ring_old_ciphertext_decrypts_and_reseals_under_new_primary(monkeypatch):
    """The rotation ring: pre-rotation ciphertext keeps decrypting while
    new writes seal under the new primary (decrypt-old/encrypt-new)."""
    # Sealed under the OLD key (pre-rotation instance state).
    monkeypatch.setattr(settings, "HA_DATA_KEY", OLD_DATA_KEY)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
    encryption.reset_cache()
    old_sealed = encrypt_secret("bridge-api-secret")

    # Rotate: new primary, old key in the decrypt-only ring.
    monkeypatch.setattr(settings, "HA_DATA_KEY", NEW_DATA_KEY)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", OLD_DATA_KEY)
    encryption.reset_cache()

    assert decrypt_secret(old_sealed) == "bridge-api-secret"
    re_sealed = encrypt_secret("bridge-api-secret")
    assert re_sealed != old_sealed, "new writes must seal under the new primary"
    assert decrypt_secret(re_sealed) == "bridge-api-secret"

    # Without the previous entry the old ciphertext is undecryptable…
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
    encryption.reset_cache()
    with pytest.raises(ValueError):
        decrypt_secret(old_sealed)


def test_sdk_ring_rotation_with_legacy_env_names(monkeypatch):
    """The integrations SDK ring (bridge pairing secrets) rotates through
    the same family under the legacy env names — untouched semantics."""
    from integrations.sdk.secrets import SecretCipher

    monkeypatch.setattr(settings, "HA_DATA_KEY", OLD_DATA_KEY)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
    old_cipher = SecretCipher.from_settings()
    sealed = old_cipher.encrypt_value("android-pairing-secret", context="inst-42")

    monkeypatch.setattr(settings, "HA_DATA_KEY", NEW_DATA_KEY)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", OLD_DATA_KEY)
    ring_cipher = SecretCipher.from_settings()
    assert ring_cipher.decrypt_value(sealed, context="inst-42") == ("android-pairing-secret")
    re_sealed = ring_cipher.encrypt_value("android-pairing-secret", context="inst-42")
    assert re_sealed["_kid"] != sealed["_kid"], "re-seal records the new primary"
    # Context binding survives the rotation.
    with pytest.raises(ValueError, match="different context"):
        ring_cipher.decrypt_value(re_sealed, context="inst-99")


def test_signing_keys_never_decrypt_at_rest():
    """The JWT HMAC secrets are not DATA_KEY material: ciphertext sealed
    under the DATA_KEY does not open under a signing key."""
    sealed = encrypt_secret("payload")
    token = sealed[len("enc::") :].encode()
    # A Fernet built from the session key's leading 32 bytes is simply a
    # *different* key — the ciphertext must not open under it.
    other_key = base64.urlsafe_b64encode(SESSION_KEY.encode()[:32])
    with pytest.raises(InvalidToken):
        Fernet(other_key).decrypt(token)
    with pytest.raises(InvalidToken):
        Fernet(base64.urlsafe_b64encode(REFRESH_KEY.encode()[:32])).decrypt(token)


def test_data_key_family_order_primary_first(monkeypatch):
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", f" {OLD_DATA_KEY},,{NEW_DATA_KEY} ")
    family = data_key_family()
    assert family[0] == NEW_DATA_KEY, "primary always first (encrypts)"
    assert OLD_DATA_KEY in family
    assert len(family) == 2, "deduplicated"
