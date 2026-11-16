"""The integration-actor seam (ADR 0054 D8, Phase 13).

``resolve_integration_actor`` no longer derives the owner's role: it resolves
the integration's scoped principal (provisioning one from the extension's
declaration on first use). These tests pin the seam's contract — a principal
shape with a declared subset, and a typed refusal instead of an impersonation
fallback.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.authz.permissions import Permission
from app.core.authz.roles import principal_has
from app.core.database import AsyncSessionLocal
from app.core.errors import AuthorizationError, NotFoundError
from app.models.enums import IntegrationStatus, Role
from app.models.fhir.patient import Patient
from app.models.user_integration import UserIntegration
from app.schemas.user import TokenData
from app.services.integration.integration_actor import resolve_integration_actor
from tests._auth_helpers import create_tenant, create_user


async def _world(provider: str = "dev_dummy") -> tuple[object, uuid.UUID, uuid.UUID]:
    tenant_id = await create_tenant()
    owner = await create_user(role=Role.USER, tenant_id=tenant_id)
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                user_id=owner.id,
                name={"family": "Seam", "given": ["Test"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=owner.id,
                patient_id=patient_id,
                provider=provider,
                instance_name="seam",
                status=IntegrationStatus.ACTIVE,
                user_config={},
            )
        )
        await db.commit()
    return owner, integration_id, patient_id


@pytest.mark.asyncio
async def test_resolver_returns_the_principal_not_the_owner_role():
    owner, integration_id, patient_id = await _world()
    async with AsyncSessionLocal() as db:
        integration = await db.get(UserIntegration, integration_id)
        actor = await resolve_integration_actor(db, integration)
        await db.commit()

    assert isinstance(actor, TokenData)
    assert actor.user_id == owner.id
    assert actor.integration_principal_id is not None
    assert actor.integration_patient_ids == [str(patient_id)]
    assert actor.integration_permissions
    assert principal_has(actor, Permission.CLINICAL_WRITE)
    assert not principal_has(actor, Permission.TENANT_MANAGE)
    assert not principal_has(actor, Permission.DOCUMENT_DELETE)


@pytest.mark.asyncio
async def test_resolver_refuses_an_extension_with_no_declaration():
    _owner, integration_id, _patient = await _world(provider="not-a-registered-domain")
    async with AsyncSessionLocal() as db:
        integration = await db.get(UserIntegration, integration_id)
        with pytest.raises(AuthorizationError):
            await resolve_integration_actor(db, integration)


@pytest.mark.asyncio
async def test_resolver_refuses_a_missing_owner():
    tenant_id = await create_tenant()
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Orphan", "given": ["Test"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        await db.commit()
        # The FK would normally cascade; the row is constructed with a
        # non-existent owner to exercise the resolver's typed refusal.
        integration = UserIntegration(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=uuid.uuid4(),
            patient_id=patient_id,
            provider="dev_dummy",
            instance_name="orphan",
            status=IntegrationStatus.ACTIVE,
            user_config={},
        )
        with pytest.raises(NotFoundError):
            await resolve_integration_actor(db, integration)
