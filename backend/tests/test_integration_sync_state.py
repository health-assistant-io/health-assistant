"""First-class sync state: cursor round-trip, backoff data, one truth.

Pins the plan-8 W1/W3 contract:

* every cursor kind (``timestamp``/``page_token``/``etag``/``commit``)
  round-trips through ``integration_sync_state`` exactly;
* the SDK's JSONB cursor path is gone — ``user_config`` stays configuration;
* failures write typed health (streak, ``last_error_class``, ``next_retry_at``)
  and a success resets it;
* the beat's gate reads ``next_retry_at`` (no Redis cooldown, no code state).
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.enums import IntegrationSyncStream
from app.models.tenant_model import TenantModel
from app.models.user_integration import IntegrationSyncState, UserIntegration
from app.models.user_model import UserModel
from app.services.integration import integration_sync_state as state_service

TENANT = uuid.uuid4()


@pytest_asyncio.fixture
async def integration():
    """Isolated tenant + user + patient + integration instance."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Sync State T.", slug=f"sstate-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"sstate-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        from app.models.fhir.patient import Patient

        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["State"]},
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
                provider="test_sync_state",
                status="ACTIVE",
                user_config={"parser_type": "basic"},
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        ).scalar_one()
        yield row


async def _reload(integration_id):
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration_id))
        ).scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("timestamp", "2026-10-22T08:30:00+00:00"),
        ("page_token", "page-3"),
        ("etag", 'W/"abc123"'),
        ("commit", {"codes": ["8867-4"], "n": 2}),
    ],
)
async def test_every_cursor_kind_round_trips(integration, kind, value):
    tenant_id = integration.tenant_id
    stream = IntegrationSyncStream.OBSERVATIONS

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration.id))
        ).scalar_one()
        await state_service.hydrate(db, row)
        state_service.set_cursor(row, stream, value, key="Observation", kind=kind)
        assert await state_service.flush(db, row) == 1
        await db.commit()

    reloaded = await _reload(integration.id)
    async with AsyncSessionLocal() as db:
        await state_service.hydrate(db, reloaded)
        assert state_service.get_cursor(reloaded, stream, key="Observation") == value, (
            f"{kind} cursor did not round-trip"
        )

    async with AsyncSessionLocal() as db:
        stored = (
            await db.execute(
                select(IntegrationSyncState).where(
                    IntegrationSyncState.user_integration_id == integration.id,
                    IntegrationSyncState.tenant_id == tenant_id,
                )
            )
        ).scalar_one()
        assert stored.cursor_kind.value == kind
        assert stored.stream.value == "observations"


@pytest.mark.asyncio
async def test_cursor_writes_never_touch_user_config(integration):
    """One truth: cursors are rows; ``user_config`` stays configuration."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration.id))
        ).scalar_one()
        await state_service.hydrate(db, row)
        state_service.set_cursor(row, IntegrationSyncStream.TELEMETRY, "2026-10-22T00:00:00+00:00")
        await state_service.flush(db, row)
        await db.commit()

    reloaded = await _reload(integration.id)
    assert reloaded.user_config == {"parser_type": "basic"}
    assert "_sync_state" not in (reloaded.user_config or {})


def test_sdk_cursor_api_is_row_backed_only():
    """The JSONB cursor methods are deleted, not deprecated."""
    from app.extensions.sdk import BaseHealthProvider

    assert not hasattr(BaseHealthProvider, "get_sync_cursor")
    assert not hasattr(BaseHealthProvider, "set_sync_cursor")
    for method in ("get_cursor", "set_cursor", "clear_cursor"):
        assert hasattr(BaseHealthProvider, method)


@pytest.mark.asyncio
async def test_failure_records_typed_backoff_and_success_resets_it(integration):
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(UserIntegration).where(UserIntegration.id == integration.id))
        ).scalar_one()
        await state_service.hydrate(db, row)
        state_service.set_cursor(row, IntegrationSyncStream.OBSERVATIONS, "cursor-1")
        await state_service.flush(db, row)

        await state_service.record_failure(db, row, "rate-limited", retry_after_seconds=120)
        await db.commit()

        rows = (
            (
                await db.execute(
                    select(IntegrationSyncState).where(
                        IntegrationSyncState.user_integration_id == integration.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        stored = rows[0]
        assert stored.consecutive_failures == 1
        assert stored.last_error_class.value == "rate-limited"
        assert stored.last_error_at is not None
        # Retry-After (120s) is honored over the 60s first-step backoff.
        delta = (stored.next_retry_at - stored.last_error_at).total_seconds()
        assert 115 <= delta <= 125

        # Gate: this integration is in backoff for the injected window.
        gate = await state_service.backoff_gate(db, [integration.id], now=stored.last_error_at)
        assert integration.id in gate

        # A second failure doubles the streak and the delay (data, not code).
        await state_service.record_failure(db, row, "rate-limited")
        await db.commit()
        stored = (
            await db.execute(
                select(IntegrationSyncState).where(
                    IntegrationSyncState.user_integration_id == integration.id
                )
            )
        ).scalar_one()
        assert stored.consecutive_failures == 2
        delta = (stored.next_retry_at - stored.last_error_at).total_seconds()
        assert 115 <= delta <= 125

        # Success heals the row and clears the retry gate.
        await state_service.record_success(db, row, IntegrationSyncStream.OBSERVATIONS)
        await db.commit()
        db.expire_all()
        stored = (
            await db.execute(
                select(IntegrationSyncState).where(
                    IntegrationSyncState.user_integration_id == integration.id
                )
            )
        ).scalar_one()
        assert stored.consecutive_failures == 0
        assert stored.next_retry_at is None
        assert stored.last_error_class is None
        assert stored.last_success_at is not None

        gate = await state_service.backoff_gate(db, [integration.id])
        assert integration.id not in gate


@pytest.mark.asyncio
async def test_backoff_gate_ignores_past_retry_times(integration):
    async with AsyncSessionLocal() as db:
        db.add(
            IntegrationSyncState(
                tenant_id=integration.tenant_id,
                user_integration_id=integration.id,
                stream=IntegrationSyncStream.OBSERVATIONS,
                cursor_key="",
                consecutive_failures=1,
                last_error_class="io",
                last_error_at=datetime.now(UTC) - timedelta(hours=2),
                next_retry_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await db.commit()
        gate = await state_service.backoff_gate(db, [integration.id])
        assert gate == {}


def test_beat_reads_next_retry_at_and_no_redis_cooldown_remains():
    """Regression pin: backoff is the row, not a code-embedded Redis key."""
    from app.services import integration_sync_service
    from app.workers import tasks

    beat_src = inspect.getsource(tasks.sync_active_integrations)
    assert "backoff_gate" in beat_src
    assert "sync_cooldown" not in beat_src
    assert not hasattr(integration_sync_service, "set_rate_limit_cooldown")
    assert not hasattr(integration_sync_service, "is_rate_limited")

    run_src = inspect.getsource(integration_sync_service.run_sync)
    assert "record_failure" in run_src
    assert "record_success" in run_src
    assert "next_retry_at" not in run_src or "retry_after_seconds" in run_src
