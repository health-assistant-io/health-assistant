"""Sealed connector credentials: sealing, rotation kid, revocation fail-closed.

Pins the plan-8 W1 credential contract:

* a sealed payload never appears in plaintext in the row, and a blob is bound
  to its owning integration (context-bound ciphertext);
* rotation reseals in place under the *new* primary key's ``_kid`` and stamps
  ``rotated_at``; once the retired key is dropped from the ring the old
  ciphertext is unreadable;
* revocation makes every read raise the typed ``credential``/``auth`` failure,
  and ``run_sync`` refuses to pull — no data is written.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.extensions.registry import integration_registry
from app.models.enums import IntegrationCredentialKind
from app.models.user_integration import IntegrationCredential, IntegrationSyncLog, UserIntegration
from app.services.integration import integration_credentials as credentials
from app.services.integration.integration_credentials import (
    CredentialError,
    read_credential,
    require_credentials,
    revoke_credential,
    seal_credential,
)

SECRET = {"access_token": "at-123", "refresh_token": "rt-456"}


@pytest_asyncio.fixture
async def integration():
    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.models.user_model import UserModel

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Cred T.", slug=f"cred-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"cred-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["Cred"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="test_credentials",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        yield (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        ).scalar_one()


@pytest.mark.asyncio
async def test_sealed_payload_is_ciphertext_and_reads_back(integration):
    async with AsyncSessionLocal() as db:
        row = await seal_credential(
            db,
            integration,
            IntegrationCredentialKind.OAUTH2,
            SECRET,
            scopes=["patient/*.read"],
        )
        await db.commit()
        assert row.kid
        stored = json.loads(row.sealed_blob)
        assert "_encrypted" in stored and stored["_kid"] == row.kid
        assert "at-123" not in row.sealed_blob

        assert await read_credential(db, integration, IntegrationCredentialKind.OAUTH2) == SECRET

        await db.refresh(row)
        assert list(row.scopes) == ["patient/*.read"]


@pytest.mark.asyncio
async def test_ciphertext_is_bound_to_the_owning_integration(integration):
    async with AsyncSessionLocal() as db:
        row = await seal_credential(
            db, integration, IntegrationCredentialKind.API_KEY, {"api_key": "k-1"}
        )
        await db.commit()
        with pytest.raises(CredentialError):
            credentials._unseal(row.sealed_blob, context=str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_rotation_reseals_under_a_new_kid(integration, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    original_key = settings.HA_DATA_KEY
    original_previous = settings.HA_DATA_KEY_PREVIOUS
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    try:
        monkeypatch.setattr(settings, "HA_DATA_KEY", old_key)
        monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
        async with AsyncSessionLocal() as db:
            first = await seal_credential(db, integration, IntegrationCredentialKind.OAUTH2, SECRET)
            await db.commit()
            old_kid = first.kid
            old_blob = first.sealed_blob
            assert first.rotated_at is None

        monkeypatch.setattr(settings, "HA_DATA_KEY", new_key)
        monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", old_key)
        async with AsyncSessionLocal() as db:
            rotated = await seal_credential(
                db,
                integration,
                IntegrationCredentialKind.OAUTH2,
                {"access_token": "at-new", "refresh_token": "rt-new"},
            )
            await db.commit()
            await db.refresh(rotated)
            assert rotated.kid != old_kid, "rotation must seal under the new primary key"
            assert rotated.rotated_at is not None
            assert rotated.revoked_at is None
            assert json.loads(rotated.sealed_blob)["_kid"] == rotated.kid
            assert await read_credential(db, integration, IntegrationCredentialKind.OAUTH2) == {
                "access_token": "at-new",
                "refresh_token": "rt-new",
            }

        # The old ciphertext is readable only while its key stays in the
        # decrypt-only ring; dropping PREVIOUS (the rotation runbook's last
        # step) makes it unreadable — no plaintext fallback.
        monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
        with pytest.raises(CredentialError):
            credentials._unseal(old_blob, context=str(integration.id))
    finally:
        monkeypatch.setattr(settings, "HA_DATA_KEY", original_key)
        monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", original_previous)


@pytest.mark.asyncio
async def test_revocation_fails_closed_before_any_pull(integration):
    from unittest.mock import AsyncMock

    from app.services import integration_sync_service as svc

    class _Provider:
        domain = "test_credentials"

        def __init__(self):
            self.pulled = False

        async def pull_data(self, integration):
            self.pulled = True
            return []

    provider = _Provider()

    async with AsyncSessionLocal() as db:
        await seal_credential(db, integration, IntegrationCredentialKind.SIGNED, {"secret": "s-1"})
        await db.commit()
        # Sanity: an active credential passes the gate.
        await require_credentials(db, integration)
        assert await revoke_credential(db, integration, IntegrationCredentialKind.SIGNED) == 1
        await db.commit()

        with pytest.raises(CredentialError):
            await read_credential(db, integration, IntegrationCredentialKind.SIGNED)
        with pytest.raises(CredentialError):
            await require_credentials(db, integration)

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration.id))
        ).scalar_one()
        with (
            patch.object(
                integration_registry,
                "has_capability",
                lambda domain, cap: cap == "sync",
            ),
            patch.object(svc, "_try_acquire_lock", AsyncMock(return_value=(True, "test-lock"))),
            patch.object(svc, "_release_lock", AsyncMock()),
            patch.object(svc, "post_sync_notifications", AsyncMock()),
        ):
            result = await svc.run_sync(db, row, provider, source="test")

    assert result.status == "failed"
    assert result.error_type == "auth"
    assert provider.pulled is False, "a revoked credential must stop the sync before pull"

    async with AsyncSessionLocal() as db:
        logs = (
            (
                await db.execute(
                    select(IntegrationSyncLog).where(
                        IntegrationSyncLog.integration_id == integration.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert logs, "the fail-closed sync must leave a typed failure row"
        assert logs[-1].status == "failed"
        assert logs[-1].error_class is not None
        assert logs[-1].error_class.value == "credential"

        integration_row = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration.id))
        ).scalar_one()
        assert integration_row.status.value == "ERROR"


@pytest.mark.asyncio
async def test_credentialless_instances_pass_the_gate(integration):
    async with AsyncSessionLocal() as db:
        # No rows → nothing to check (tokenless servers / HMAC-only bridges).
        await require_credentials(db, integration)


@pytest.mark.asyncio
async def test_credentials_summary_never_returns_the_payload(integration):
    async with AsyncSessionLocal() as db:
        await seal_credential(
            db, integration, IntegrationCredentialKind.API_KEY, {"api_key": "top-secret"}
        )
        await db.commit()
        summary = await credentials.credentials_summary(db, integration)
    assert summary and summary[0]["kind"] == "api_key"
    assert "top-secret" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_one_slot_per_integration_and_kind(integration):
    async with AsyncSessionLocal() as db:
        await seal_credential(db, integration, IntegrationCredentialKind.API_KEY, {"api_key": "a"})
        await seal_credential(db, integration, IntegrationCredentialKind.API_KEY, {"api_key": "b"})
        await db.commit()
        rows = (
            (
                await db.execute(
                    select(IntegrationCredential).where(
                        IntegrationCredential.user_integration_id == integration.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
