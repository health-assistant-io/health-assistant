"""Plan 37 Phase 4.3 (health slice) — fail-closed / oracle endpoint proofs.

Covers the DB-backed halves of the 2026-10-18 audit items:

* F18 — provider list/detail responses return presence + ``***`` only and
  never decrypt the stored key.
* F21 — user-entered secret slots are encrypted with the instance id as
  cipher context, exactly like platform-provisioned machine secrets.
"""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.core.database import AsyncSessionLocal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def org_ctx():
    """Tenant + ADMIN user + patient, isolated per test."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()

    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.models.user_model import UserModel

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Phase 4.3", slug=f"p43-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"p43-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Hardening", "given": ["Phase"]},
                gender="UNKNOWN",
            )
        )
        await db.commit()
        return {"tenant_id": tenant_id, "user_id": user_id, "patient_id": patient_id}


@pytest.fixture
def data_key(monkeypatch):
    """A deterministic HA_DATA_KEY for the secret paths.

    Patches the settings singleton in place (the pattern every key test
    uses) instead of clearing ``get_settings``'s cache — a cleared cache
    leaves ``app.core.config.settings`` and ``get_settings()`` pointing at
    different objects and breaks unrelated key-separation tests.
    """
    key = Fernet.generate_key().decode()
    from app.core import encryption as enc
    from app.core.config import settings

    monkeypatch.setattr(settings, "HA_DATA_KEY", key)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
    enc.reset_cache()
    yield key
    enc.reset_cache()


# ---------------------------------------------------------------------------
# F18 — no last-4 hint, no decryption on list paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_list_returns_presence_and_a_bare_mask(org_ctx, data_key):
    """``GET /ai-config/providers`` (the list path) must not reveal a last-4
    fragment and must not run the cipher for any row."""
    from app.api.v1.endpoints import ai_config as ai_ep
    from app.core import encryption as enc
    from app.models.ai_provider_model import AIProviderModel
    from app.models.enums import AIScope
    from app.schemas.user import TokenData

    stored = enc.encrypt_secret("sk-live-COMPANY-SECRET-9876")
    provider_name = f"phase43-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        db.add(
            AIProviderModel(
                id=uuid.uuid4(),
                name=provider_name,
                scope=AIScope.SYSTEM,
                provider_type="openai",
                api_base="https://api.example.test/v1",
                api_key=stored,
                is_active=True,
                settings={},
            )
        )
        await db.commit()

        user = TokenData(
            user_id=org_ctx["user_id"],
            tenant_id=org_ctx["tenant_id"],
            role="SYSTEM_ADMIN",
        )

        def _boom(*_a, **_kw):  # pragma: no cover - a call is the failure
            raise AssertionError("list path decrypted a stored secret")

        with patch.object(enc, "decrypt_secret", _boom):
            rows = await ai_ep.get_providers(db=db, current_user=user)

    ours = next(r for r in rows if r.name == provider_name)
    assert ours.api_key == "***"
    assert ours.has_api_key is True
    assert "9876" not in ours.api_key
    assert "SECRET" not in ours.api_key


# ---------------------------------------------------------------------------
# F21 — user-entered secret slots bind to the instance (machine parity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_entered_secret_is_bound_to_the_new_instance(org_ctx, data_key):
    """A secret typed into the config flow is stored with the instance id as
    context: it decrypts for that row and refuses a cut-and-paste."""
    from sqlalchemy import select

    from app.api.v1.endpoints import integrations as ep
    from app.extensions.builtin.dev_dummy.config_flow import DevDummyConfigFlow
    from app.extensions.sdk.secrets import SecretCipher
    from app.models.user_integration import UserIntegration
    from app.schemas.user import TokenData

    user = TokenData(
        user_id=org_ctx["user_id"],
        tenant_id=org_ctx["tenant_id"],
        role="ADMIN",
    )
    flow = DevDummyConfigFlow()

    async with AsyncSessionLocal() as db:
        with (
            patch.object(ep.integration_registry, "get_config_flow", return_value=flow),
            # No inbound capabilities → no machine secret is provisioned; the
            # only secret in the row is the user-entered one.
            patch.object(ep.integration_registry, "get_provider", return_value=None),
            patch.object(ep.integration_registry, "has_capability", return_value=False),
        ):
            await ep.submit_config_flow(
                domain="dev_dummy",
                patient_id=str(org_ctx["patient_id"]),
                payload={
                    "instance_name": "bound-secret",
                    "sync_interval": 15,
                    "webhook_secret": "user-entered-secret-123",
                },
                current_user=user,
                db=db,
            )

        row = (
            await db.execute(
                select(UserIntegration).where(
                    UserIntegration.user_id == org_ctx["user_id"],
                    UserIntegration.provider == "dev_dummy",
                )
            )
        ).scalar_one()
        wrapped = row.user_config["webhook_secret"]
        assert isinstance(wrapped, dict) and "_encrypted" in wrapped

        cipher = SecretCipher.from_settings()
        assert cipher.decrypt_value(wrapped, context=str(row.id)) == "user-entered-secret-123"
        with pytest.raises(ValueError, match="different context"):
            cipher.decrypt_value(wrapped, context=str(uuid.uuid4()))
