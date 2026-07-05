"""Integration tests for the unified notification system.

Covers the Phase 1 fan-out model:
* target resolution (PATIENT → user + examining doctors; TENANT → all;
  dedup; unlinked clinical records skipped; cross-tenant isolation).
* ``emit`` creates 1 Notification + N NotificationRecipient + per-channel
  NotificationDelivery rows, and respects push-subscription presence.
* biomarker rule evaluation (threshold match fires; cooldown suppresses;
  out-of-normal works; non-match does not fire).
* inbox isolation across tenants.

These run against the real migrated test DB (conftest runs
``alembic upgrade head`` at session start).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from unittest.mock import AsyncMock

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.doctor_model import DoctorModel
from app.models.enums import (
    ComparisonOperator,
    NotificationCategory,
    NotificationChannel,
    NotificationRuleType,
    NotificationSeverity,
    NotificationSource,
    NotificationStatus,
    NotificationType,
    RecipientKind,
    RecipientStatus,
    Role,
)
from app.models.fhir.patient import Patient
from app.models.notification import (
    Notification,
    NotificationDelivery,
    NotificationRecipient,
)
from app.models.user_model import UserModel
from app.services import notification_rule_service, notification_service
from app.services.notification_targets import resolve_targets
from app.models.examination_model import ExaminationModel
from app.models.associations import examination_doctors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(
    tenant_id, role=Role.USER.value, email_suffix=""
) -> UserModel:
    user = UserModel(
        email=f"user{email_suffix}{uuid4().hex[:6]}@test.io",
        tenant_id=tenant_id,
        role=role,
    )
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def _make_patient(tenant_id, user_id=None) -> Patient:
    patient = Patient(
        tenant_id=tenant_id,
        user_id=user_id,
        name=[{"text": "Test Patient"}],
        gender="UNKNOWN",
    )
    async with AsyncSessionLocal() as session:
        session.add(patient)
        await session.commit()
        await session.refresh(patient)
    return patient


async def _make_doctor(tenant_id, user_id=None) -> DoctorModel:
    doctor = DoctorModel(tenant_id=tenant_id, user_id=user_id, name=f"Dr {uuid4().hex[:4]}")
    async with AsyncSessionLocal() as session:
        session.add(doctor)
        await session.commit()
        await session.refresh(doctor)
    return doctor


async def _make_tenant():
    from app.models.tenant_model import TenantModel

    slug = f"tenant-{uuid4().hex[:8]}"
    tenant = TenantModel(name=slug, slug=slug)
    async with AsyncSessionLocal() as session:
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
    return tenant.id


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_patient_target_includes_patient_user_and_doctors():
    tenant = await _make_tenant()
    patient_user = await _make_user(tenant, email_suffix="p")
    doctor_user = await _make_user(tenant, email_suffix="d")
    patient = await _make_patient(tenant, user_id=patient_user.id)
    doctor = await _make_doctor(tenant, user_id=doctor_user.id)

    # Link doctor to patient via an examination.
    async with AsyncSessionLocal() as session:
        exam = ExaminationModel(tenant_id=tenant, patient_id=patient.id)
        session.add(exam)
        await session.flush()
        await session.execute(
            examination_doctors.insert().values(
                examination_id=exam.id, doctor_id=doctor.id
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        resolved = await resolve_targets(
            session,
            tenant,
            [{"kind": RecipientKind.PATIENT.value, "id": str(patient.id)}],
        )
    assert resolved == {patient_user.id, doctor_user.id}


@pytest.mark.asyncio
async def test_resolve_unlinked_patient_is_dropped():
    tenant = await _make_tenant()
    patient = await _make_patient(tenant, user_id=None)  # unlinked
    async with AsyncSessionLocal() as session:
        resolved = await resolve_targets(
            session,
            tenant,
            [{"kind": RecipientKind.PATIENT.value, "id": str(patient.id)}],
        )
    assert resolved == set()


@pytest.mark.asyncio
async def test_resolve_tenant_includes_all_users():
    tenant = await _make_tenant()
    u1 = await _make_user(tenant, email_suffix="1")
    u2 = await _make_user(tenant, email_suffix="2")
    async with AsyncSessionLocal() as session:
        resolved = await resolve_targets(
            session, tenant, [{"kind": RecipientKind.TENANT.value}]
        )
    assert resolved == {u1.id, u2.id}


@pytest.mark.asyncio
async def test_resolve_cross_tenant_patient_yields_nothing():
    tenant_a = await _make_tenant()
    tenant_b = await _make_tenant()
    patient = await _make_patient(tenant_a)  # belongs to A
    async with AsyncSessionLocal() as session:
        resolved = await resolve_targets(
            session,
            tenant_b,  # querying from tenant B
            [{"kind": RecipientKind.PATIENT.value, "id": str(patient.id)}],
        )
    assert resolved == set()


@pytest.mark.asyncio
async def test_resolve_deduplicates_users():
    tenant = await _make_tenant()
    user = await _make_user(tenant, email_suffix="dup")
    # Same user referenced as USER and as DOCTOR.
    doctor = await _make_doctor(tenant, user_id=user.id)
    async with AsyncSessionLocal() as session:
        resolved = await resolve_targets(
            session,
            tenant,
            [
                {"kind": RecipientKind.USER.value, "id": str(user.id)},
                {"kind": RecipientKind.DOCTOR.value, "id": str(doctor.id)},
            ],
        )
    assert resolved == {user.id}


# ---------------------------------------------------------------------------
# emit fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_creates_recipient_and_in_app_delivery(monkeypatch):
    # Avoid Redis/Celery side effects during the test.
    monkeypatch.setattr(notification_service, "publish_message", AsyncMock())
    monkeypatch.setattr(
        "app.workers.tasks.deliver_notification.delay", lambda *a, **k: None
    )
    tenant = await _make_tenant()
    user = await _make_user(tenant, email_suffix="emit")

    notification = await notification_service.emit(
        source=NotificationSource.SYSTEM,
        type=NotificationType.SYSTEM_BROADCAST,
        category=NotificationCategory.SYSTEM,
        title="Hello",
        body="World",
        tenant_id=tenant,
        targets=[{"kind": RecipientKind.USER.value, "id": str(user.id)}],
    )
    assert notification is not None

    async with AsyncSessionLocal() as session:
        recipients = (
            await session.execute(
                select(NotificationRecipient).where(
                    NotificationRecipient.notification_id == notification.id
                )
            )
        ).scalars().all()
        deliveries = (
            await session.execute(
                select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == notification.id
                )
            )
        ).scalars().all()

    assert len(recipients) == 1
    assert recipients[0].user_id == user.id
    assert recipients[0].status == RecipientStatus.UNREAD
    # IN_APP delivery is marked DELIVERED at emit time; no PUSH sub → no push row.
    assert any(d.channel == NotificationChannel.IN_APP for d in deliveries)
    assert all(d.channel == NotificationChannel.IN_APP for d in deliveries)
    in_app = next(d for d in deliveries if d.channel == NotificationChannel.IN_APP)
    assert in_app.status == NotificationStatus.DELIVERED


@pytest.mark.asyncio
async def test_emit_no_push_delivery_without_subscription(monkeypatch):
    monkeypatch.setattr(notification_service, "publish_message", AsyncMock())
    monkeypatch.setattr(
        "app.workers.tasks.deliver_notification.delay", lambda *a, **k: None
    )
    tenant = await _make_tenant()
    user = await _make_user(tenant, email_suffix="nopush")

    notification = await notification_service.emit(
        source=NotificationSource.SYSTEM,
        type=NotificationType.SYSTEM_BROADCAST,
        category=NotificationCategory.SYSTEM,
        title="t",
        tenant_id=tenant,
        targets=[{"kind": RecipientKind.USER.value, "id": str(user.id)}],
    )
    async with AsyncSessionLocal() as session:
        push_rows = (
            await session.execute(
                select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == notification.id,
                    NotificationDelivery.channel == NotificationChannel.PUSH,
                )
            )
        ).scalars().all()
    assert push_rows == []


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


async def _make_biomarker(tenant_id, low=70.0, high=100.0) -> BiomarkerDefinition:
    bio = BiomarkerDefinition(
        tenant_id=tenant_id,
        slug=f"bio-{uuid4().hex[:6]}",
        coding_system="custom",
        name="Glucose",
        reference_range_min=low,
        reference_range_max=high,
    )
    async with AsyncSessionLocal() as session:
        session.add(bio)
        await session.commit()
        await session.refresh(bio)
    return bio


@pytest.mark.asyncio
async def test_rule_threshold_match_fires(monkeypatch):
    monkeypatch.setattr(notification_service, "publish_message", AsyncMock())
    monkeypatch.setattr(
        "app.workers.tasks.deliver_notification.delay", lambda *a, **k: None
    )
    tenant = await _make_tenant()
    user = await _make_user(tenant, email_suffix="rule")
    patient = await _make_patient(tenant, user_id=user.id)
    bio = await _make_biomarker(tenant, low=70, high=100)

    await notification_rule_service.create_rule(
        {
            "rule_type": NotificationRuleType.BIOMARKER_THRESHOLD.value,
            "biomarker_id": str(bio.id),
            "operator": ComparisonOperator.GT.value,
            "value": 100,
            "patient_id": str(patient.id),
            "severity": "critical",
            "targets": [{"kind": RecipientKind.PATIENT.value, "id": str(patient.id)}],
        },
        tenant_id=tenant,
    )

    from app.models.fhir.patient import Observation

    obs = Observation(
        tenant_id=tenant,
        subject={'reference': f'Patient/{patient.id}'},
        biomarker_id=bio.id,
        raw_value=150.0,
        normalized_value=150.0,
        status="final",
        code={},
    )
    async with AsyncSessionLocal() as session:
        session.add(obs)
        await session.commit()
        await session.refresh(obs)

    fired = await notification_rule_service.evaluate_and_fire(obs, patient.id, tenant)
    assert fired == 1

    async with AsyncSessionLocal() as session:
        notif = (
            await session.execute(
                select(Notification).where(
                    Notification.source == NotificationSource.RULE,
                    Notification.tenant_id == tenant,
                    Notification.patient_id == patient.id,
                )
            )
        ).scalar_one_or_none()
    assert notif is not None
    assert notif.type == NotificationType.BIOMARKER_THRESHOLD
    assert notif.severity == NotificationSeverity.CRITICAL


@pytest.mark.asyncio
async def test_rule_no_match_does_not_fire(monkeypatch):
    monkeypatch.setattr(notification_service, "publish_message", AsyncMock())
    monkeypatch.setattr(
        "app.workers.tasks.deliver_notification.delay", lambda *a, **k: None
    )
    tenant = await _make_tenant()
    user = await _make_user(tenant, email_suffix="nomatch")
    patient = await _make_patient(tenant, user_id=user.id)
    bio = await _make_biomarker(tenant)

    await notification_rule_service.create_rule(
        {
            "rule_type": NotificationRuleType.BIOMARKER_THRESHOLD.value,
            "biomarker_id": str(bio.id),
            "operator": ComparisonOperator.GT.value,
            "value": 200,
            "patient_id": str(patient.id),
            "targets": [{"kind": RecipientKind.PATIENT.value, "id": str(patient.id)}],
        },
        tenant_id=tenant,
    )

    from app.models.fhir.patient import Observation

    obs = Observation(
        tenant_id=tenant,
        subject={'reference': f'Patient/{patient.id}'},
        biomarker_id=bio.id,
        raw_value=80.0,
        normalized_value=80.0,
        status="final",
        code={},
    )
    async with AsyncSessionLocal() as session:
        session.add(obs)
        await session.commit()
        await session.refresh(obs)

    fired = await notification_rule_service.evaluate_and_fire(obs, patient.id, tenant)
    assert fired == 0


@pytest.mark.asyncio
async def test_rule_cooldown_suppresses_second_fire(monkeypatch):
    monkeypatch.setattr(notification_service, "publish_message", AsyncMock())
    monkeypatch.setattr(
        "app.workers.tasks.deliver_notification.delay", lambda *a, **k: None
    )
    tenant = await _make_tenant()
    user = await _make_user(tenant, email_suffix="cd")
    patient = await _make_patient(tenant, user_id=user.id)
    bio = await _make_biomarker(tenant)

    await notification_rule_service.create_rule(
        {
            "rule_type": NotificationRuleType.BIOMARKER_THRESHOLD.value,
            "biomarker_id": str(bio.id),
            "operator": ComparisonOperator.GT.value,
            "value": 100,
            "patient_id": str(patient.id),
            "cooldown_minutes": 60,
            "targets": [{"kind": RecipientKind.PATIENT.value, "id": str(patient.id)}],
        },
        tenant_id=tenant,
    )

    from app.models.fhir.patient import Observation

    obs1 = Observation(
        tenant_id=tenant,
        subject={'reference': f'Patient/{patient.id}'},
        biomarker_id=bio.id,
        raw_value=150.0,
        normalized_value=150.0,
        status="final",
        code={},
    )
    obs2 = Observation(
        tenant_id=tenant,
        subject={'reference': f'Patient/{patient.id}'},
        biomarker_id=bio.id,
        raw_value=160.0,
        normalized_value=160.0,
        status="final",
        code={},
    )
    async with AsyncSessionLocal() as session:
        session.add_all([obs1, obs2])
        await session.commit()
        await session.refresh(obs1)
        await session.refresh(obs2)

    first = await notification_rule_service.evaluate_and_fire(obs1, patient.id, tenant)
    second = await notification_rule_service.evaluate_and_fire(obs2, patient.id, tenant)
    assert first == 1
    assert second == 0  # suppressed by cooldown


# ---------------------------------------------------------------------------
# Inbox isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_is_tenant_isolated(monkeypatch):
    monkeypatch.setattr(notification_service, "publish_message", AsyncMock())
    monkeypatch.setattr(
        "app.workers.tasks.deliver_notification.delay", lambda *a, **k: None
    )
    tenant_a = await _make_tenant()
    tenant_b = await _make_tenant()
    user_a = await _make_user(tenant_a, email_suffix="A")
    user_b = await _make_user(tenant_b, email_suffix="B")

    await notification_service.emit(
        source=NotificationSource.SYSTEM,
        type=NotificationType.SYSTEM_BROADCAST,
        category=NotificationCategory.SYSTEM,
        title="tenant A only",
        tenant_id=tenant_a,
        targets=[{"kind": RecipientKind.USER.value, "id": str(user_a.id)}],
    )

    items_a, _ = await notification_service.get_inbox(
        user_id=user_a.id, tenant_id=tenant_a
    )
    items_b, _ = await notification_service.get_inbox(
        user_id=user_b.id, tenant_id=tenant_b
    )
    assert len(items_a) == 1
    assert items_b == []


# ---------------------------------------------------------------------------
# Source wiring (Phase 4): system broadcast + integration owner targeting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_broadcast_to_tenant_reaches_all_users(monkeypatch):
    """A tenant-scoped SYSTEM broadcast fans out to every user in the tenant."""
    monkeypatch.setattr(notification_service, "publish_message", AsyncMock())
    monkeypatch.setattr(
        "app.workers.tasks.deliver_notification.delay", lambda *a, **k: None
    )
    tenant = await _make_tenant()
    u1 = await _make_user(tenant, email_suffix="b1")
    u2 = await _make_user(tenant, email_suffix="b2")
    u3 = await _make_user(tenant, email_suffix="b3")

    notification = await notification_service.emit(
        source=NotificationSource.SYSTEM,
        type=NotificationType.SYSTEM_BROADCAST,
        category=NotificationCategory.SYSTEM,
        severity=NotificationSeverity.WARNING,
        title="Maintenance window",
        body="The system will be briefly unavailable tonight.",
        tenant_id=tenant,
        targets=[{"kind": RecipientKind.TENANT.value, "id": str(tenant)}],
    )
    assert notification is not None

    for user in (u1, u2, u3):
        items, _ = await notification_service.get_inbox(
            user_id=user.id, tenant_id=tenant
        )
        assert len(items) == 1
        assert items[0]["notification"]["source"] == NotificationSource.SYSTEM.value
        assert items[0]["notification"]["severity"] == NotificationSeverity.WARNING.value


@pytest.mark.asyncio
async def test_hitl_proposal_helper_emits_notification(monkeypatch):
    """The HITL propose-tool helper emits an AGENT/HITL_TASK notification."""
    monkeypatch.setattr(notification_service, "publish_message", AsyncMock())
    monkeypatch.setattr(
        "app.workers.tasks.deliver_notification.delay", lambda *a, **k: None
    )
    from app.ai.tools.hitl_proposals import _notify_hitl_proposal
    from app.ai.tools.registry import ToolContext

    tenant = await _make_tenant()
    user = await _make_user(tenant, email_suffix="hitl")
    patient = await _make_patient(tenant, user_id=user.id)

    async with AsyncSessionLocal() as session:
        ctx = ToolContext(
            db=session,
            tenant_id=tenant,
            patient_id=patient.id,
            user_id=user.id,
        )
        await _notify_hitl_proposal(
            ctx,
            {
                "proposal_id": "abc",
                "task_type": "create_clinical_event",
                "title": "Create Clinical Event: Pregnancy",
            },
        )

    items, _ = await notification_service.get_inbox(
        user_id=user.id, tenant_id=tenant
    )
    assert len(items) == 1
    notif = items[0]["notification"]
    assert notif["source"] == NotificationSource.AGENT.value
    assert notif["type"] == NotificationType.HITL_TASK.value
    assert notif["category"] == NotificationCategory.HITL.value
    # The "Open chat" action is present.
    actions = notif["payload"].get("actions", [])
    assert any(a["id"] == "open_chat" for a in actions)
