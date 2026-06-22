"""Tests for audit item B1 (AI provider api_key encryption + masking).

B1: ``AIProviderModel.api_key`` was stored plaintext and returned in GET/
    list responses. Any authenticated user could read other tenants'
    provider keys by UUID. The fix:

      - Adds ``app.core.encryption`` (Fernet, reuses INTEGRATION_SECRET_KEY)
      - Stores values with ``enc::<token>`` prefix
      - ``AIProviderModel.get_api_key_plaintext()`` is the only sanctioned
        plaintext reader (used by the LLM factory)
      - ``AIProviderResponse`` / ``AIProviderWithModelsResponse`` mask the
        key on read via a model_validator and expose ``has_api_key: bool``
      - ``AIProviderService.create_provider`` / ``update_provider`` encrypt
        on write; update treats masked or None as "no change"
      - Backfill script ``scripts/encrypt_existing_api_keys.py`` converts
        legacy rows
"""
import inspect
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"


@pytest.fixture(autouse=True)
def _reset_encryption_cache():
    """Clear the cached Fernet between tests so settings changes take effect."""
    from app.core import encryption as enc

    enc.reset_cache()
    yield
    enc.reset_cache()


def _set_fernet_key(monkeypatch, key=None):
    if key is None:
        key = Fernet.generate_key().decode()
    from app.core.config import settings

    monkeypatch.setattr(settings, "INTEGRATION_SECRET_KEY", key)
    return key


# ---------------------------------------------------------------------------
# Encryption helper round-trip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import decrypt_secret, encrypt_secret, is_encrypted

    plain = "sk-live-abcdef1234567890"
    enc_val = encrypt_secret(plain)
    assert enc_val != plain
    assert is_encrypted(enc_val)
    assert enc_val.startswith("enc::")
    assert decrypt_secret(enc_val) == plain


def test_encrypt_none_returns_none(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import encrypt_secret

    assert encrypt_secret(None) is None
    assert encrypt_secret("") == ""


def test_encrypt_idempotent_on_encrypted_input(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import encrypt_secret

    plain = "sk-test-123"
    once = encrypt_secret(plain)
    twice = encrypt_secret(once)
    assert once == twice, "Re-encrypting an encrypted value must be a no-op"


def test_decrypt_legacy_plaintext_returns_verbatim(monkeypatch):
    """Existing rows that pre-date encryption must still work."""
    _set_fernet_key(monkeypatch)
    from app.core.encryption import decrypt_secret, is_encrypted

    legacy = "sk-legacy-plaintext-key"
    assert not is_encrypted(legacy)
    assert decrypt_secret(legacy) == legacy


def test_decrypt_with_wrong_key_raises(monkeypatch):
    """A rotated key surfaces as ValueError so callers can prompt re-entry."""
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    from app.core import encryption as enc

    _set_fernet_key(monkeypatch, key_a)
    encrypted = enc.encrypt_secret("sk-test-XYZ")
    _set_fernet_key(monkeypatch, key_b)  # rotate
    enc.reset_cache()
    with pytest.raises(ValueError):
        enc.decrypt_secret(encrypted)


def test_mask_secret_reveals_only_last_four(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import encrypt_secret, mask_secret

    encrypted = encrypt_secret("sk-live-XX-1234567890-secret")
    masked = mask_secret(encrypted)
    assert masked is not None
    assert masked.startswith("***")
    assert masked.endswith("cret")  # last 4 chars
    assert "secret" not in masked[:-4]
    assert "sk-live" not in masked


def test_mask_secret_handles_plaintext_input(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import mask_secret

    masked = mask_secret("sk-legacy-abcdef")
    assert masked == "***cdef"


def test_mask_secret_none(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import mask_secret

    assert mask_secret(None) is None
    assert mask_secret("") is None


def test_mask_secret_on_bad_token(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import mask_secret

    # Garbage with the encrypted prefix → can't decrypt → "***"
    assert mask_secret("enc::garbage") == "***"


def test_looks_masked():
    from app.core.encryption import looks_masked

    assert looks_masked("***abcd")
    assert not looks_masked("sk-real-key")
    assert not looks_masked(None)


# ---------------------------------------------------------------------------
# AIProviderResponse masks api_key + sets has_api_key
# ---------------------------------------------------------------------------


def test_response_masks_encrypted_api_key(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import encrypt_secret
    from app.schemas.ai_config import AIProviderResponse

    encrypted = encrypt_secret("sk-prod-DEADBEEF")
    resp = AIProviderResponse(
        id=uuid4(),
        name="p",
        scope="SYSTEM",
        provider_type="openai",
        api_base="https://api.openai.com",
        api_key=encrypted,
        is_active=True,
        settings={},
    )
    assert resp.has_api_key is True
    # Must not contain the plaintext
    assert "sk-prod-DEADBEEF" not in (resp.api_key or "")
    assert "DEADBEEF" not in (resp.api_key or "")
    # Masked form reveals only last 4 of the plaintext
    assert resp.api_key is not None
    assert resp.api_key.startswith("***")
    assert resp.api_key.endswith("BEEF")  # last 4 chars of plaintext "DEADBEEF"


def test_response_masks_plaintext_api_key(monkeypatch):
    """Legacy plaintext rows must also be masked on read."""
    _set_fernet_key(monkeypatch)
    from app.schemas.ai_config import AIProviderResponse

    resp = AIProviderResponse(
        id=uuid4(),
        name="p",
        scope="SYSTEM",
        provider_type="openai",
        api_base="https://api.openai.com",
        api_key="sk-legacy-123456",
        is_active=True,
        settings={},
    )
    assert resp.has_api_key is True
    assert resp.api_key == "***3456"
    assert "sk-legacy" not in resp.api_key


def test_response_none_api_key(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.schemas.ai_config import AIProviderResponse

    resp = AIProviderResponse(
        id=uuid4(),
        name="p",
        scope="SYSTEM",
        provider_type="openai",
        api_base="https://api.openai.com",
        api_key=None,
        is_active=True,
        settings={},
    )
    assert resp.has_api_key is False
    assert resp.api_key is None


def test_with_models_response_also_masks(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import encrypt_secret
    from app.schemas.ai_config import AIProviderWithModelsResponse

    encrypted = encrypt_secret("sk-abcdef-XX-SECRET")
    resp = AIProviderWithModelsResponse(
        id=uuid4(),
        name="p",
        provider_type="openai",
        api_base="https://api.openai.com",
        api_key=encrypted,
        is_active=True,
        settings={},
        models=[],
    )
    assert resp.has_api_key is True
    assert "SECRET" not in (resp.api_key or "")
    assert resp.api_key is not None and resp.api_key.startswith("***")


# ---------------------------------------------------------------------------
# Service: create/update encrypts; update preserves on masked/None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_provider_encrypts_api_key(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import decrypt_secret, is_encrypted
    from app.services.ai_provider_service import AIProviderService
    from app.schemas.ai_config import AIProviderCreate
    from app.models.enums import AIScope

    db = MagicMock()
    captured = {}

    async def fake_commit():
        pass

    async def fake_refresh(p):
        captured["provider"] = p

    db.add = MagicMock()
    db.commit = fake_commit
    db.refresh = fake_refresh

    service = AIProviderService(db)
    payload = AIProviderCreate(
        name="p",
        scope=AIScope.SYSTEM,
        provider_type="openai",
        api_base="https://api.openai.com",
        api_key="sk-test-SECRET",
    )
    provider = await service.create_provider(payload)
    assert is_encrypted(provider.api_key), "Stored value was not encrypted"
    assert decrypt_secret(provider.api_key) == "sk-test-SECRET"


@pytest.mark.asyncio
async def test_update_provider_preserves_key_when_masked_sent(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import encrypt_secret
    from app.services.ai_provider_service import AIProviderService
    from app.schemas.ai_config import AIProviderUpdate

    encrypted_existing = encrypt_secret("sk-real-key-XYZ")

    # Mock the DB layer: capture the values passed to UPDATE
    captured = {}

    async def fake_execute(stmt):
        # Capture values without touching a real DB
        if hasattr(stmt, "values"):
            captured.update(stmt.compile().params)

    async def fake_commit():
        pass

    existing_provider = MagicMock()
    existing_provider.api_key = encrypted_existing

    async def fake_get_provider(_id):
        return existing_provider

    db = MagicMock()
    db.execute = fake_execute
    db.commit = fake_commit

    service = AIProviderService(db)
    service.get_provider = fake_get_provider

    # UI sends back the masked form "***XYZ" — must NOT overwrite the key
    await service.update_provider(
        uuid4(),
        AIProviderUpdate(api_key="***XYZ", name="renamed"),
    )

    # api_key must NOT be in the update payload
    assert all("api_key" not in k for k in captured.keys()), (
        f"api_key leaked into update payload: {captured}"
    )


@pytest.mark.asyncio
async def test_update_provider_encrypts_new_plaintext(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import decrypt_secret
    from app.services.ai_provider_service import AIProviderService
    from app.schemas.ai_config import AIProviderUpdate

    captured = {}

    async def fake_execute(stmt):
        if hasattr(stmt, "values"):
            # SQLAlchemy 2.0 build — params lives on compilation
            captured["values"] = dict(stmt.compile().params)

    async def fake_commit():
        pass

    existing = MagicMock()
    existing.api_key = None

    async def fake_get_provider(_id):
        return existing

    db = MagicMock()
    db.execute = fake_execute
    db.commit = fake_commit

    service = AIProviderService(db)
    service.get_provider = fake_get_provider

    await service.update_provider(
        uuid4(),
        AIProviderUpdate(api_key="sk-brand-new-PLAIN"),
    )
    # The value stored in the UPDATE must be encrypted, not plaintext
    api_key_val = captured["values"].get("api_key")
    assert api_key_val is not None
    assert api_key_val.startswith("enc::"), "New api_key was not encrypted on update"
    assert decrypt_secret(api_key_val) == "sk-brand-new-PLAIN"


@pytest.mark.asyncio
async def test_update_provider_clears_key_when_none_explicitly(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.services.ai_provider_service import AIProviderService
    from app.schemas.ai_config import AIProviderUpdate

    captured = {}

    async def fake_execute(stmt):
        if hasattr(stmt, "values"):
            captured["values"] = dict(stmt.compile().params)

    async def fake_commit():
        pass

    existing = MagicMock()
    existing.api_key = "enc::something"

    async def fake_get_provider(_id):
        return existing

    db = MagicMock()
    db.execute = fake_execute
    db.commit = fake_commit

    service = AIProviderService(db)
    service.get_provider = fake_get_provider

    await service.update_provider(uuid4(), AIProviderUpdate(api_key=None))
    # api_key should be set to None (cleared)
    assert captured["values"].get("api_key") is None


# ---------------------------------------------------------------------------
# Model: get_api_key_plaintext()
# ---------------------------------------------------------------------------


def test_model_get_api_key_plaintext_decrypts(monkeypatch):
    _set_fernet_key(monkeypatch)
    from app.core.encryption import encrypt_secret
    from app.models.ai_provider_model import AIProviderModel

    p = AIProviderModel(
        name="x",
        scope="SYSTEM",
        provider_type="openai",
        api_base="https://x",
        api_key=encrypt_secret("sk-real-PLAIN"),
    )
    assert p.get_api_key_plaintext() == "sk-real-PLAIN"


def test_model_get_api_key_plaintext_legacy(monkeypatch):
    """Legacy plaintext rows pass through unchanged."""
    _set_fernet_key(monkeypatch)
    from app.models.ai_provider_model import AIProviderModel

    p = AIProviderModel(
        name="x",
        scope="SYSTEM",
        provider_type="openai",
        api_base="https://x",
        api_key="sk-legacy-no-prefix",
    )
    assert p.get_api_key_plaintext() == "sk-legacy-no-prefix"


def test_model_get_api_key_plaintext_bad_token_returns_none(monkeypatch):
    """A corrupted/rotated value surfaces as None (no exception)."""
    _set_fernet_key(monkeypatch)
    from app.models.ai_provider_model import AIProviderModel

    p = AIProviderModel(
        name="x",
        scope="SYSTEM",
        provider_type="openai",
        api_base="https://x",
        api_key="enc::garbage-token",
    )
    assert p.get_api_key_plaintext() is None


# ---------------------------------------------------------------------------
# Source-level regression guards
# ---------------------------------------------------------------------------


def test_provider_service_never_reads_plaintext_attr():
    """B1: ``provider.api_key`` must NEVER be used as plaintext in the service.

    All reads must go through ``provider.get_api_key_plaintext()``. Catches
    a future regression at source level.
    """
    src = (BACKEND / "app" / "services" / "ai_provider_service.py").read_text()
    # 'provider.api_key' is allowed inside the model itself (to_dict), but
    # not in the service. The encrypted value is fine to read for storage
    # paths, but anything that needs the actual key must use the getter.
    forbidden_patterns = [
        "f\"Bearer {provider.api_key}\"",
        'f\'Bearer {provider.api_key}\'',
        "api_key=provider.api_key",
    ]
    for pat in forbidden_patterns:
        assert pat not in src, (
            f"ai_provider_service.py still contains plaintext-key read {pat!r}"
        )


def test_backfill_script_exists():
    """B1: the one-shot backfill script must exist and be idempotent."""
    script = BACKEND / "scripts" / "encrypt_existing_api_keys.py"
    assert script.exists(), "encrypt_existing_api_keys.py missing"
    text = script.read_text()
    assert "--dry-run" in text, "Backfill script should support --dry-run"
    assert "is_encrypted" in text, "Backfill must skip already-encrypted rows"


def test_to_dict_does_not_decrypt():
    """B1: AIProviderModel.to_dict() must NOT call decrypt_secret.

    The to_dict() output flows through logging and audit trails — it must
    only ever return the encrypted form. Plaintext reads go through the
    explicit getter.
    """
    src = inspect.getsource(__import__("app.models.ai_provider_model", fromlist=["AIProviderModel"]).AIProviderModel.to_dict)
    assert "decrypt_secret" not in src, (
        "to_dict() must not decrypt — would leak plaintext via logs"
    )
