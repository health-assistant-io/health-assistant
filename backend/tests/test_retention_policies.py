"""Retention policy + job tests (plan 6 W3).

Policies are data: changing a row moves the cutoff, and every job reads the
row. The tests cover the operational tables the plan names (``ai_call_logs``,
integration logs, notifications, ``task_logs``), the audit ``anonymize``
action, the terminal-only proposal rule, checkpoint pruning re-pointed at its
policy row, and the orphan-cleanup job (the hypertable cleanup promise).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.ai.graphs.checkpointer import CheckpointStore
from app.core.database import AsyncSessionLocal
from app.models.ai_task_model import AICallLog, AITaskDef
from app.models.audit_model import AuditEvent
from app.models.chat_model import ChatSession
from app.models.data_lifecycle_model import RetentionPolicy
from app.models.enums import (
    AICallRoute,
    AICallStatus,
    NotificationCategory,
    NotificationSource,
    NotificationType,
    ProposalSource,
    ProposalStatus,
)
from app.models.notification import Notification
from app.models.proposal import Proposal, ProposalKind
from app.models.task_log import TaskLog
from app.models.telemetry_model import TelemetryDataModel
from app.models.tenant_model import TenantModel
from app.models.user_integration import IntegrationDebugLog, IntegrationSyncLog, UserIntegration
from app.models.user_model import UserModel
from app.services.data_lifecycle.retention_service import (
    effective_policy,
    purge_orphaned_lifecycle_rows,
    run_retention,
)
from app.workers.ai_tasks import prune_checkpoint_threads

NOW = datetime.now(UTC)


async def _policy(db, scope_value: str) -> RetentionPolicy:
    return (
        await db.execute(
            select(RetentionPolicy).where(
                RetentionPolicy.scope == "table",
                RetentionPolicy.scope_value == scope_value,
            )
        )
    ).scalar_one()


async def _task_def_id(db) -> uuid.UUID:
    return (await db.execute(select(AITaskDef.id).limit(1))).scalar_one()


async def _ai_log(db, when: datetime) -> AICallLog:
    return AICallLog(
        task_type_id=await _task_def_id(db),
        route=AICallRoute.CHAT,
        started_at=when,
        latency_ms=1,
        status=AICallStatus.OK,
    )


@pytest.mark.asyncio
async def test_changing_the_policy_row_moves_the_cutoff():
    async with AsyncSessionLocal() as db:
        policy = await _policy(db, "ai_call_logs")
        original_days = policy.days
        try:
            old = await _ai_log(db, NOW - timedelta(days=40))
            recent = await _ai_log(db, NOW - timedelta(days=5))
            db.add_all([old, recent])
            await db.commit()

            # Default 180-day window keeps both.
            await run_retention(db, now=NOW)
            remaining = set((await db.execute(select(AICallLog.id))).scalars().all())
            assert old.id in remaining and recent.id in remaining

            # Operator edit: 30 days → the 40-day-old row is now outside it.
            policy.days = 30
            await db.commit()
            result = await run_retention(db, now=NOW)
            assert result["total_rows"] >= 1
            remaining = set((await db.execute(select(AICallLog.id))).scalars().all())
            assert old.id not in remaining
            assert recent.id in remaining
        finally:
            policy.days = original_days
            await db.commit()


@pytest.mark.asyncio
async def test_named_operational_logs_are_pruned():
    async with AsyncSessionLocal() as db:
        tenant = TenantModel(name="Ret", slug=f"ret-{uuid.uuid4().hex[:10]}")
        user = UserModel(
            email=f"ret-{uuid.uuid4().hex[:10]}@test.local",
            password_hash="x",
            tenant_id=None,
            role="USER",
        )
        db.add(tenant)
        await db.flush()
        user.tenant_id = tenant.id
        db.add(user)
        await db.flush()
        from app.models.fhir.patient import Patient

        patient = Patient(
            tenant_id=tenant.id,
            user_id=user.id,
            name=[{"family": "Ret"}],
            gender="OTHER",
        )
        db.add(patient)
        await db.flush()
        integration = UserIntegration(
            tenant_id=tenant.id, user_id=user.id, patient_id=patient.id, provider="x"
        )
        db.add(integration)
        await db.flush()

        old = NOW - timedelta(days=400)
        db.add_all(
            [
                IntegrationSyncLog(
                    tenant_id=tenant.id,
                    integration_id=integration.id,
                    status="success",
                    started_at=old,
                    completed_at=old,
                ),
                IntegrationDebugLog(
                    tenant_id=tenant.id,
                    integration_id=integration.id,
                    title="old",
                    timestamp=old,
                ),
                TaskLog(
                    task_name="t",
                    task_id="1",
                    level="info",
                    message="old",
                    created_at=old,
                ),
                Notification(
                    tenant_id=tenant.id,
                    source=NotificationSource.SYSTEM,
                    type=NotificationType.SYSTEM_UPDATE,
                    category=NotificationCategory.SYSTEM,
                    title="old",
                    created_at=old,
                ),
            ]
        )
        await db.commit()

        result = await run_retention(db, now=NOW)
        pruned = {item["table"] for item in result["tables"] if item["rows"]}
        assert {
            "integration_sync_logs",
            "integration_debug_logs",
            "task_logs",
            "notifications",
        } <= pruned

        async with AsyncSessionLocal() as check:
            for model in (IntegrationSyncLog, IntegrationDebugLog, TaskLog, Notification):
                assert (
                    await check.execute(select(model).where(model.tenant_id == tenant.id))
                ).scalars().all() == []

        await db.execute(
            TenantModel.__table__.delete().where(TenantModel.__table__.c.id == tenant.id)
        )
        await db.commit()


@pytest.mark.asyncio
async def test_audit_policy_anonymizes_instead_of_deleting():
    async with AsyncSessionLocal() as db:
        old = AuditEvent(
            action="read_patient",
            resource_type="Patient",
            old_value={"phi": "old"},
            new_value={"phi": "old"},
            created_at=NOW - timedelta(days=4000),
        )
        recent = AuditEvent(
            action="read_patient",
            resource_type="Patient",
            old_value={"phi": "recent"},
            new_value={"phi": "recent"},
        )
        db.add_all([old, recent])
        await db.commit()
        old_id, recent_id = old.id, recent.id

        await run_retention(db, now=NOW)
        db.expire_all()

        refreshed = (
            await db.execute(select(AuditEvent).where(AuditEvent.id == old_id))
        ).scalar_one()
        assert refreshed is not None, "audit rows are proof-of-existence, never deleted"
        assert refreshed.old_value is None and refreshed.new_value is None
        blocked = (
            await db.execute(select(AuditEvent).where(AuditEvent.id == recent_id))
        ).scalar_one()
        assert blocked.old_value == {"phi": "recent"}

        await db.execute(
            AuditEvent.__table__.delete().where(AuditEvent.__table__.c.id.in_([old_id, recent_id]))
        )
        await db.commit()


@pytest.mark.asyncio
async def test_proposals_retention_prunes_terminal_rows_only():
    async with AsyncSessionLocal() as db:
        tenant = TenantModel(name="Prop", slug=f"prop-{uuid.uuid4().hex[:10]}")
        db.add(tenant)
        await db.flush()
        kind = (await db.execute(select(ProposalKind).limit(1))).scalar_one()
        old = NOW - timedelta(days=400)
        pending = Proposal(
            tenant_id=tenant.id,
            source=ProposalSource.CHAT,
            kind_id=kind.id,
            payload={},
            status=ProposalStatus.PROPOSED,
            created_at=old,
        )
        rejected = Proposal(
            tenant_id=tenant.id,
            source=ProposalSource.CHAT,
            kind_id=kind.id,
            payload={},
            status=ProposalStatus.REJECTED,
            created_at=old,
        )
        db.add_all([pending, rejected])
        await db.commit()

        await run_retention(db, now=NOW)
        ids = set((await db.execute(select(Proposal.id))).scalars().all())
        assert pending.id in ids, "a pending proposal is work in flight, not history"
        assert rejected.id not in ids

        await db.execute(
            TenantModel.__table__.delete().where(TenantModel.__table__.c.id == tenant.id)
        )
        await db.commit()


@pytest.mark.asyncio
async def test_checkpoint_prune_reads_its_policy_row():
    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    stale, fresh = uuid.uuid4(), uuid.uuid4()
    try:
        async with AsyncSessionLocal() as db:
            db.add(TenantModel(id=tenant_id, name="CP", slug=f"cp-{tenant_id}"))
            await db.flush()
            db.add(
                UserModel(
                    id=user_id,
                    email=f"cp-{user_id}@test.local",
                    password_hash="x",
                    tenant_id=tenant_id,
                    role="USER",
                )
            )
            await db.flush()
            db.add_all(
                [
                    ChatSession(id=stale, tenant_id=tenant_id, user_id=user_id, title="stale"),
                    ChatSession(id=fresh, tenant_id=tenant_id, user_id=user_id, title="fresh"),
                ]
            )
            await db.commit()
            await db.execute(
                ChatSession.__table__.update()
                .where(ChatSession.__table__.c.id == stale)
                .values(updated_at=NOW - timedelta(days=5))
            )
            await db.commit()
            for thread in (stale, fresh):
                await db.execute(
                    text(
                        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
                        "checkpoint, metadata) VALUES (:t, '', :c, '{}'::jsonb, '{}'::jsonb)"
                    ),
                    {"t": str(thread), "c": uuid.uuid4().hex},
                )
            await db.commit()

        async with AsyncSessionLocal() as db:
            policy = await _policy(db, "checkpoints")
            original_days = policy.days
            try:
                policy.days = 30
                await db.commit()
                await asyncio.to_thread(prune_checkpoint_threads)
                async with AsyncSessionLocal() as check:
                    threads = set(
                        (await check.execute(text("SELECT DISTINCT thread_id FROM checkpoints")))
                        .scalars()
                        .all()
                    )
                assert str(stale) in threads, "5 days < 30-day window must survive"

                policy.days = 1
                await db.commit()
                await asyncio.to_thread(prune_checkpoint_threads)
                async with AsyncSessionLocal() as check:
                    threads = set(
                        (await check.execute(text("SELECT DISTINCT thread_id FROM checkpoints")))
                        .scalars()
                        .all()
                    )
                assert str(stale) not in threads
                assert str(fresh) in threads
            finally:
                policy.days = original_days
                await db.commit()
    finally:
        await store.close()
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("DELETE FROM checkpoints WHERE thread_id = ANY(:t)"),
                {"t": [str(stale), str(fresh)]},
            )
            await db.execute(
                ChatSession.__table__.delete().where(ChatSession.__table__.c.id.in_([stale, fresh]))
            )
            await db.execute(
                UserModel.__table__.delete().where(UserModel.__table__.c.id == user_id)
            )
            await db.execute(
                TenantModel.__table__.delete().where(TenantModel.__table__.c.id == tenant_id)
            )
            await db.commit()


@pytest.mark.asyncio
async def test_orphaned_hypertable_rows_are_purged():
    orphan_tenant, orphan_patient = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            TelemetryDataModel(
                tenant_id=orphan_tenant,
                patient_id=orphan_patient,
                device_id=uuid.uuid4(),
                timestamp=NOW,
                slug="steps",
                value=1,
                raw_value=1,
            )
        )
        await db.commit()
        result = await purge_orphaned_lifecycle_rows(db)
        assert result["telemetry_data_tenant"] >= 1
        remaining = (
            (
                await db.execute(
                    select(TelemetryDataModel).where(TelemetryDataModel.tenant_id == orphan_tenant)
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []


@pytest.mark.asyncio
async def test_a_disabled_table_row_wins_over_the_class_fallback():
    async with AsyncSessionLocal() as db:
        policy = await effective_policy(db, "ai_task_defs", "operational")
        assert policy.source == "table"
        assert policy.enabled is False, "a table row (even disabled) is the decision"
        audit_policy = await effective_policy(db, "catalog_audit_log", "operational")
        assert audit_policy.source == "table" and audit_policy.enabled is True


def test_hitl_seen_codes_cursor_token_is_bounded():
    """F13: the FHIR seen-codes commit token cannot grow without a bound."""
    from app.extensions.builtin.fhir_server.provider import (
        HITL_SEEN_CODES_LIMIT,
        bound_seen_codes,
    )

    codes = [str(index) for index in range(HITL_SEEN_CODES_LIMIT + 25)]
    bounded = bound_seen_codes(codes)
    assert len(bounded) == HITL_SEEN_CODES_LIMIT
    assert bounded[-1] == str(HITL_SEEN_CODES_LIMIT + 24)
    assert bounded[0] == "25"
    assert bound_seen_codes(["a", "b"]) == ["a", "b"]
