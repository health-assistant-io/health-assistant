"""Scheduled/recurring notification trigger orchestration.

The trigger framework (TIME / RECURRING schedules) remains here. The
firing path now delegates to :mod:`app.services.notification_service.emit`
so every emitted notification flows through the unified fan-out model
(recipients + per-channel deliveries) rather than hand-rolled rows.

Event-style triggers (``TriggerType.EVENT``) and the legacy
``biomarker_update`` event hook have been removed — event-driven biomarker
checks now live in :mod:`app.services.notification_rule_service`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE
from app.models.enums import (
    NotificationCategory,
    NotificationSeverity,
    NotificationSource,
    NotificationType,
    RecipientKind,
    TriggerType,
)
from app.models.notification import (
    NotificationSubscription,
    NotificationTrigger,
)

logger = logging.getLogger(__name__)


class NotificationManager:
    """Orchestrates the trigger lifecycle (schedule → fire)."""

    @staticmethod
    def calculate_next_occurrence(at_time_str: str, days: List[str] = None) -> datetime:
        """Calculates the next occurrence of a wall-clock time."""
        now = datetime.now(timezone.utc)
        try:
            hour, minute = map(int, at_time_str.split(":"))
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            if days:
                day_map = {
                    "mon": 0,
                    "tue": 1,
                    "wed": 2,
                    "thu": 3,
                    "fri": 4,
                    "sat": 5,
                    "sun": 6,
                }
                target_days = [
                    day_map[d.lower()[:3]] for d in days if d.lower()[:3] in day_map
                ]
                if target_days:
                    while next_run.weekday() not in target_days:
                        next_run += timedelta(days=1)
            return next_run
        except Exception as e:
            logger.error("Error calculating next occurrence for %s: %s", at_time_str, e)
            return now + timedelta(days=1)

    @classmethod
    async def create_trigger(
        cls,
        patient_id: Union[str, UUID],
        notification_type: NotificationType,
        trigger_type: TriggerType,
        config: Dict[str, Any],
        title: str,
        body: Optional[str] = None,
        tenant_id: Optional[Union[str, UUID]] = None,
        reference_id: Optional[Union[str, UUID]] = None,
        enabled: bool = True,
    ) -> Optional[NotificationTrigger]:
        """Creates a new notification trigger rule."""
        if not DATABASE_AVAILABLE:
            return None

        next_trigger = None
        if trigger_type == TriggerType.TIME:
            at_str = config.get("at")
            if at_str:
                try:
                    next_trigger = datetime.fromisoformat(at_str.replace("Z", "+00:00"))
                except ValueError:
                    next_trigger = cls.calculate_next_occurrence(at_str)
        elif trigger_type == TriggerType.RECURRING:
            at_str = config.get("at")
            if at_str:
                next_trigger = cls.calculate_next_occurrence(at_str, config.get("days"))
            else:
                interval_mins = config.get("interval_minutes", 1440)
                next_trigger = datetime.now(timezone.utc) + timedelta(
                    minutes=interval_mins
                )

        new_trigger = NotificationTrigger(
            patient_id=UUID(str(patient_id)) if patient_id else None,
            notification_type=notification_type,
            trigger_type=trigger_type,
            config=config,
            title=title,
            body=body,
            tenant_id=UUID(str(tenant_id)) if tenant_id else None,
            reference_id=UUID(str(reference_id)) if reference_id else None,
            enabled=enabled,
            next_trigger=next_trigger,
        )

        async with AsyncSessionLocal() as session:
            session.add(new_trigger)
            await session.commit()
            await session.refresh(new_trigger)
            return new_trigger

    @staticmethod
    async def subscribe_user(
        user_id: Union[str, UUID],
        subscription_data: Dict[str, Any],
        tenant_id: Union[str, UUID],
        device_id: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> NotificationSubscription:
        """Saves or updates a Web Push subscription (upsert by device/endpoint)."""
        if not DATABASE_AVAILABLE:
            from sqlalchemy.exc import SQLAlchemyError

            raise SQLAlchemyError("database unavailable")

        endpoint = subscription_data.get("endpoint")
        async with AsyncSessionLocal() as session:
            query = select(NotificationSubscription).where(
                NotificationSubscription.user_id == UUID(str(user_id))
            )
            if device_id:
                query = query.where(NotificationSubscription.device_id == device_id)
            elif endpoint:
                query = query.where(
                    NotificationSubscription.subscription_data["endpoint"].astext
                    == endpoint
                )
            result = await session.execute(query)
            existing = result.scalar_one_or_none()

            if existing:
                existing.subscription_data = subscription_data
                existing.user_agent = user_agent
                existing.is_active = True
                await session.commit()
                return existing

            new_sub = NotificationSubscription(
                user_id=UUID(str(user_id)),
                tenant_id=UUID(str(tenant_id)),
                subscription_data=subscription_data,
                device_id=device_id,
                user_agent=user_agent,
            )
            session.add(new_sub)
            await session.commit()
            await session.refresh(new_sub)
            return new_sub

    @staticmethod
    async def delete_triggers_by_reference(reference_id: Union[str, UUID]) -> bool:
        """Deletes all triggers associated with a specific resource ID."""
        if not DATABASE_AVAILABLE:
            return False
        from sqlalchemy import delete

        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(NotificationTrigger).where(
                    NotificationTrigger.reference_id == UUID(str(reference_id))
                )
            )
            await session.commit()
            return True

    @staticmethod
    async def list_triggers_for_patient(
        patient_id: Union[str, UUID], tenant_id: Union[str, UUID]
    ) -> List[dict]:
        """List all enabled/disabled triggers for a patient (tenant-scoped)."""
        if not DATABASE_AVAILABLE:
            return []
        async with AsyncSessionLocal() as session:
            stmt = select(NotificationTrigger).where(
                and_(
                    NotificationTrigger.patient_id == UUID(str(patient_id)),
                    NotificationTrigger.tenant_id == UUID(str(tenant_id)),
                )
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [t.to_dict() for t in rows]

    @staticmethod
    async def list_triggers_for_tenant(
        tenant_id: Union[str, UUID],
    ) -> List[dict]:
        """List all triggers for a tenant (used by the global Notification Center)."""
        if not DATABASE_AVAILABLE:
            return []
        async with AsyncSessionLocal() as session:
            stmt = select(NotificationTrigger).where(
                NotificationTrigger.tenant_id == UUID(str(tenant_id))
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [t.to_dict() for t in rows]

    @staticmethod
    async def delete_trigger(trigger_id: UUID, tenant_id: Union[str, UUID]) -> dict:
        """Delete a single trigger by id (tenant-scoped; cross-tenant = no-op)."""
        if not DATABASE_AVAILABLE:
            return {"status": "error", "message": "database unavailable"}
        from sqlalchemy import delete as sa_delete

        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_delete(NotificationTrigger).where(
                    and_(
                        NotificationTrigger.id == trigger_id,
                        NotificationTrigger.tenant_id == UUID(str(tenant_id)),
                    )
                )
            )
            await session.commit()
        return {"status": "success"}

    @classmethod
    async def fire_trigger_by_id(
        cls, trigger_id: UUID, tenant_id: Union[str, UUID]
    ) -> bool:
        """Load a tenant-scoped trigger and fire it immediately. False if missing."""
        if not DATABASE_AVAILABLE:
            return False
        async with AsyncSessionLocal() as session:
            stmt = select(NotificationTrigger).where(
                and_(
                    NotificationTrigger.id == trigger_id,
                    NotificationTrigger.tenant_id == UUID(str(tenant_id)),
                )
            )
            trigger = (await session.execute(stmt)).scalar_one_or_none()
        if trigger is None:
            return False
        await cls.fire_notification(trigger)
        return True

    @classmethod
    async def sync_medication_triggers(
        cls,
        patient_id: Union[str, UUID],
        medication_id: Union[str, UUID],
        medication_name: str,
        timing_data: Dict[str, Any],
        tenant_id: Union[str, UUID],
    ):
        """Synchronizes notification triggers with the latest medication timing."""
        await cls.delete_triggers_by_reference(medication_id)
        if not timing_data:
            return

        repeat = timing_data.get("repeat")
        if not repeat and "frequency" in timing_data:
            times_of_day = timing_data.get("time_of_day", [])
            frequency = timing_data.get("frequency")
            days_of_week = timing_data.get("days_of_week", [])
        elif repeat:
            times_of_day = repeat.get("timeOfDay", [])
            frequency = repeat.get("frequency")
            days_of_week = repeat.get("dayOfWeek", [])
        else:
            return

        if not isinstance(times_of_day, list):
            times_of_day = [times_of_day]
        if not isinstance(days_of_week, list):
            days_of_week = [days_of_week]

        if times_of_day:
            for at_time in times_of_day:
                clean_time = at_time[:5] if len(at_time) >= 5 else at_time
                await cls.create_trigger(
                    patient_id=patient_id,
                    notification_type=NotificationType.MEDICATION_REMINDER,
                    trigger_type=TriggerType.RECURRING,
                    config={
                        "at": clean_time,
                        "days": days_of_week,
                        "medication_name": medication_name,
                    },
                    title=f"Time to take your {medication_name}",
                    body=f"Please take your scheduled dose of {medication_name}.",
                    tenant_id=tenant_id,
                    reference_id=medication_id,
                )
        elif frequency:
            await cls.create_trigger(
                patient_id=patient_id,
                notification_type=NotificationType.MEDICATION_REMINDER,
                trigger_type=TriggerType.RECURRING,
                config={
                    "interval_minutes": int(1440 / frequency),
                    "medication_name": medication_name,
                },
                title=f"Time to take your {medication_name}",
                body=f"Please take your scheduled dose of {medication_name}.",
                tenant_id=tenant_id,
                reference_id=medication_id,
            )

    @classmethod
    async def process_due_triggers(cls, session: Optional[AsyncSession] = None):
        """Finds and processes all triggers that are due for execution.

        ``session`` lets the Celery periodic task inject a worker-scoped
        session bound to the ``NullPool`` engine.
        """
        if not DATABASE_AVAILABLE:
            return
        if session is None:
            async with AsyncSessionLocal() as own:
                await cls._run_due_triggers(own)
            return
        await cls._run_due_triggers(session)

    @classmethod
    async def _run_due_triggers(cls, session: AsyncSession):
        now = datetime.now(timezone.utc)
        query = select(NotificationTrigger).where(
            and_(
                NotificationTrigger.enabled.is_(True),
                NotificationTrigger.next_trigger <= now,
            )
        )
        due_triggers = (await session.execute(query)).scalars().all()

        for trigger in due_triggers:
            try:
                await cls.fire_notification(trigger, session=session)
                trigger.last_triggered = now
                if trigger.trigger_type == TriggerType.RECURRING:
                    at_str = trigger.config.get("at")
                    if at_str:
                        trigger.next_trigger = cls.calculate_next_occurrence(
                            at_str, trigger.config.get("days")
                        )
                    else:
                        interval_mins = trigger.config.get("interval_minutes", 1440)
                        trigger.next_trigger = now + timedelta(minutes=interval_mins)
                else:
                    trigger.enabled = False
                    trigger.next_trigger = None
            except Exception as e:
                logger.exception("Failed to process trigger %s: %s", trigger.id, e)
                await session.rollback()

        await session.commit()

    @classmethod
    async def fire_notification(
        cls,
        trigger: NotificationTrigger,
        session: Optional[AsyncSession] = None,
    ) -> None:
        """Emit a SCHEDULED notification for a trigger via the unified service.

        ``session`` is forwarded to :func:`emit` so the Celery beat task can
        inject its worker-scoped ``NullPool`` session (avoiding the asyncpg
        loop-affinity crash). When omitted, ``emit`` opens its own session.
        """
        from app.services.notification_service import emit

        targets = []
        if trigger.patient_id:
            targets.append(
                {"kind": RecipientKind.PATIENT.value, "id": str(trigger.patient_id)}
            )
        elif trigger.tenant_id:
            targets.append(
                {"kind": RecipientKind.TENANT.value, "id": str(trigger.tenant_id)}
            )

        await emit(
            source=NotificationSource.SCHEDULED,
            type=trigger.notification_type,
            category=NotificationCategory.REMINDER,
            severity=NotificationSeverity.INFO,
            title=trigger.title,
            body=trigger.body,
            patient_id=trigger.patient_id,
            tenant_id=trigger.tenant_id,
            targets=targets,
            payload={
                "reference_id": str(trigger.reference_id)
                if trigger.reference_id
                else None,
                "trigger_config": trigger.config,
            },
            source_ref={"trigger_id": str(trigger.id)},
            trigger_id=trigger.id,
            session=session,
        )
