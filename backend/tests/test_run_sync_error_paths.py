"""Behavioural tests for ``run_sync``'s error contract.

When ``provider.pull_data`` raises, ``run_sync`` rolls the session back and
then records the failure. ``rollback()`` expires every ORM instance in the
session, so the error branches must reload ``integration`` through the async
API before touching its attributes; a lazy refresh under ``AsyncSession``
raises ``MissingGreenlet`` and escapes as an unhandled error (HTTP 500 on the
manual sync endpoint).
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from integrations.sdk.exceptions import (
    IntegrationAuthError,
    IntegrationDataError,
    IntegrationRateLimitError,
)
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_integration import (
    IntegrationStatus,
    IntegrationSyncLog,
    UserIntegration,
)
from app.models.user_model import UserModel
from app.services import integration_sync_service as svc


@pytest_asyncio.fixture
async def integration_id():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name="Sync Error T.",
                slug=f"syncerr-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"syncerr-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Sync", "given": ["Error"]},
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
                provider="test_sync_error",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.commit()

    return integration_id


def _raising_provider(exc: Exception):
    class _Provider:
        domain = "test_sync_error"

        async def pull_data(self, integration):
            integration.user_config = {"_sync_state": {"cursor": "advanced"}}
            raise exc

    return _Provider()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc, error_type",
    [
        (RuntimeError("boom"), "data"),
        (IntegrationDataError("bad payload"), "data"),
        (IntegrationAuthError("token revoked"), "auth"),
        (IntegrationRateLimitError("slow down"), "rate_limit"),
    ],
)
async def test_pull_data_error_returns_failed_result(integration_id, exc, error_type):
    notify = AsyncMock()
    async with AsyncSessionLocal() as db:
        integration = (
            await db.execute(
                select(UserIntegration).where(UserIntegration.id == integration_id)
            )
        ).scalar_one()

        with patch.object(svc, "post_sync_notifications", notify):
            result = await svc.run_sync(
                db, integration, _raising_provider(exc), source="manual"
            )

    assert result.status == "failed"
    assert result.error_type == error_type
    assert result.error == str(exc)

    notify.assert_awaited_once()
    assert notify.await_args.kwargs["observations"] == []

    async with AsyncSessionLocal() as db:
        fresh = await db.get(UserIntegration, integration_id)
        logs = (
            (
                await db.execute(
                    select(IntegrationSyncLog).where(
                        IntegrationSyncLog.integration_id == integration_id
                    )
                )
            )
            .scalars()
            .all()
        )

    assert [log.status for log in logs] == ["failed"]
    assert fresh.user_config == {}
    expected_status = (
        IntegrationStatus.ERROR if error_type == "auth" else IntegrationStatus.ACTIVE
    )
    assert fresh.status == expected_status
