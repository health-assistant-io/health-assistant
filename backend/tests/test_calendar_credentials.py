"""Calendar credential custody (ADR-0052 §2 — the binding law).

Pins the plan-52 P2 custody contract over the sealed slot table:

* the sealed payload never appears in plaintext in any row/column, and a
  blob is bound to its owning binding (context-bound ciphertext);
* the ``secret_ref`` pointer follows the slot law
  (``calendar:<binding-id>:oauth|password|token``);
* rotation reseals in place under the current key's ``_kid``;
* revocation makes every read fail closed (typed ``auth``);
* a missing slot / missing data key fails closed — no plaintext fallback;
* option payloads cannot smuggle credential material (API-adjacent guard).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.extensions.errors import ExtensionError
from app.models.calendar_binding import (
    CalendarBinding,
)
from app.models.enums import Role
from app.services.calendar import credentials as creds

SECRET = {"access_token": "at-cal-123", "refresh_token": "rt-cal-456", "expires_at": None}


@pytest_asyncio.fixture
async def binding():
    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.models.user_model import UserModel

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    binding_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Cal T.", slug=f"cal-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"cal-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role=Role.USER.value,
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["Cal"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            CalendarBinding(
                id=binding_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="nextcloud",
                service="calendar",
                auth_kind="app-password",
                label="Nextcloud",
                secret_ref=creds.secret_ref_for(binding_id, "app-password"),
                direction="push",
                redaction="neutral",
                status="unbound",
                options={"base_url": "https://cloud.example.com"},
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        yield (
            await db.execute(
                select(CalendarBinding).where(CalendarBinding.id == binding_id)
            )
        ).scalar_one()


@pytest.fixture
def data_key(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "HA_DATA_KEY", key)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
    return key


@pytest.mark.asyncio
async def test_sealed_payload_is_ciphertext_and_reads_back(binding, data_key):
    binding.auth_kind = "app-password"
    async with AsyncSessionLocal() as db:
        stored = await db.execute(
            select(CalendarBinding).where(CalendarBinding.id == binding.id)
        )
        live = stored.scalar_one()
        row = await creds.set_secret(
            db, live, {"password": "app-pass-xyz", "username": "ilia"}
        )
        await db.commit()
        assert row.kid
        assert "app-pass-xyz" not in row.sealed_blob
        assert "_encrypted" in row.sealed_blob

        assert await creds.read_secret(db, live) == {
            "password": "app-pass-xyz",
            "username": "ilia",
        }


@pytest.mark.asyncio
async def test_no_secret_material_in_the_binding_row(binding, data_key):
    marker = "app-pass-xyz"
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        await creds.set_secret(db, live, {"password": marker})
        await db.commit()
        await db.refresh(live)
        # the binding row's columns carry the pointer, never the secret
        row_dump = repr(live.__dict__)
        assert marker not in row_dump
        assert live.secret_ref == f"calendar:{binding.id}:password"
        # and the surface projection (what the API serializes) is clean too
        surface = await creds.slots_summary(db, live)
        assert marker not in repr(surface)
        assert all("sealed_blob" not in slot for slot in surface)


@pytest.mark.asyncio
async def test_ciphertext_is_bound_to_the_owning_binding(binding, data_key):
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        row = await creds.set_secret(db, live, {"password": "app-pass-xyz"})
        await db.commit()
        with pytest.raises(creds.CalendarCredentialError):
            creds._unseal(row.sealed_blob, context=str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_rotation_reseals_under_a_new_kid(binding, data_key, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    old_key, new_key = data_key, Fernet.generate_key().decode()
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        first = await creds.set_secret(db, live, {"password": "one"})
        await db.commit()
        old_kid, old_blob = first.kid, first.sealed_blob

        monkeypatch.setattr(settings, "HA_DATA_KEY", new_key)
        monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", old_key)
        second = await creds.set_secret(db, live, {"password": "two"})
        await db.commit()
        assert second.kid != old_kid, "rotation must seal under the new primary key"
        assert second.rotated_at is not None
        assert await creds.read_secret(db, live) == {"password": "two"}

        # dropping the previous key makes the old ciphertext unreadable
        monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
        with pytest.raises(creds.CalendarCredentialError):
            creds._unseal(old_blob, context=str(binding.id))


@pytest.mark.asyncio
async def test_revocation_fails_closed(binding, data_key):
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        await creds.set_secret(db, live, {"password": "app-pass-xyz"})
        await db.commit()
        assert await creds.revoke_secrets(db, live) == 1
        with pytest.raises(creds.CalendarCredentialError):
            await creds.read_secret(db, live)
        with pytest.raises(creds.CalendarCredentialError):
            await creds.require_secret(db, live)


@pytest.mark.asyncio
async def test_missing_slot_fails_closed(binding, data_key):
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        with pytest.raises(creds.CalendarCredentialError):
            await creds.require_secret(db, live)


@pytest.mark.asyncio
async def test_missing_data_key_fails_closed(binding, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "HA_DATA_KEY", None)
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        with pytest.raises(RuntimeError):
            await creds.set_secret(db, live, {"password": "app-pass-xyz"})


def test_secret_ref_pointer_law():
    one = uuid.UUID(int=1)
    prefix = "calendar:00000000-0000-0000-0000-000000000001"
    assert creds.secret_ref_for(one, "oauth2") == f"{prefix}:oauth"
    assert creds.secret_ref_for(one, "app-password") == f"{prefix}:password"
    assert creds.secret_ref_for(one, "token") == f"{prefix}:token"
    assert creds.secret_ref_for(one, "none") is None


@pytest.mark.asyncio
async def test_payload_shape_is_validated_per_auth_kind(binding, data_key):
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        with pytest.raises(ExtensionError):
            await creds.set_secret(db, live, {"wrong": "shape"})
        live.auth_kind = "none"
        with pytest.raises(ExtensionError):
            await creds.set_secret(db, live, {"password": "x"})
        live.auth_kind = "oauth2"
        with pytest.raises(ExtensionError):
            await creds.set_secret(db, live, {"refresh_token": "only"})
