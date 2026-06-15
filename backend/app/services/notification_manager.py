from typing import Optional, List, Dict, Any, Union
from uuid import UUID
from datetime import datetime, timezone, timedelta
import logging
import json
from sqlalchemy import select, update, delete, and_, or_
from app.models.notification import (
    NotificationTrigger,
    Notification,
    NotificationSubscription,
    NotificationType,
    NotificationChannel,
    NotificationStatus,
    TriggerType,
)
from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE
from app.core.config import settings

logger = logging.getLogger(__name__)


class NotificationManager:
    """Orchestrates notification lifecycle: triggers, creation, and delivery."""

    @staticmethod
    def calculate_next_occurrence(at_time_str: str, days: List[str] = None) -> datetime:
        """Calculates the next occurrence of a wall-clock time."""
        now = datetime.now(timezone.utc)
        try:
            # Handle HH:MM format
            hour, minute = map(int, at_time_str.split(":"))
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            if next_run <= now:
                next_run += timedelta(days=1)

            # If specific days are provided, find the next matching day
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
            logger.error(f"Error calculating next occurrence for {at_time_str}: {e}")
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

        # Calculate initial next_trigger
        next_trigger = None
        if trigger_type == TriggerType.TIME:
            at_str = config.get("at")
            if at_str:
                try:
                    next_trigger = datetime.fromisoformat(at_str.replace("Z", "+00:00"))
                except ValueError:
                    # Fallback for HH:MM if type is TIME (though usually it's ISO)
                    next_trigger = cls.calculate_next_occurrence(at_str)
        elif trigger_type == TriggerType.RECURRING:
            at_str = config.get("at")
            if at_str:
                next_trigger = cls.calculate_next_occurrence(at_str, config.get("days"))
            else:
                # Fallback to interval
                interval_mins = config.get("interval_minutes", 1440)
                next_trigger = datetime.now(timezone.utc) + timedelta(minutes=interval_mins)

        new_trigger = NotificationTrigger(
            patient_id=UUID(str(patient_id)),
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
    async def delete_triggers_by_reference(reference_id: Union[str, UUID]) -> bool:
        """Deletes all triggers associated with a specific resource ID (e.g. medication_id)."""
        if not DATABASE_AVAILABLE:
            return False

        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(NotificationTrigger).where(
                    NotificationTrigger.reference_id == UUID(str(reference_id))
                )
            )
            await session.commit()
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
        # 1. Remove old triggers
        await cls.delete_triggers_by_reference(medication_id)

        # 2. Create new triggers based on timing data
        if not timing_data:
            return

        # Handle both FHIR-style and internal-style timing
        repeat = timing_data.get("repeat")

        # If internal style, map it or handle directly
        if not repeat and "frequency" in timing_data:
            # Internal schema detected
            times_of_day = timing_data.get("time_of_day", [])
            frequency = timing_data.get("frequency")
            days_of_week = timing_data.get("days_of_week", [])
        elif repeat:
            # FHIR schema detected
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

    @staticmethod
    async def get_active_notifications(
        patient_id: Union[str, UUID], limit: int = 20, unread_only: bool = False
    ) -> List[Notification]:
        """Fetch notifications for a patient."""
        if not DATABASE_AVAILABLE:
            return []

        async with AsyncSessionLocal() as session:
            query = select(Notification).where(
                Notification.patient_id == UUID(str(patient_id))
            )
            if unread_only:
                query = query.where(Notification.status == NotificationStatus.PENDING)

            query = query.order_by(Notification.created_at.desc()).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    @staticmethod
    async def mark_as_read(notification_id: Union[str, UUID]) -> bool:
        """Mark a notification as read."""
        if not DATABASE_AVAILABLE:
            return False

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Notification)
                .where(Notification.id == UUID(str(notification_id)))
                .values(status=NotificationStatus.READ, read_at=datetime.now(timezone.utc))
            )
            await session.commit()
            return True

    @staticmethod
    async def mark_as_delivered(notification_id: Union[str, UUID]) -> bool:
        """Mark a notification as delivered."""
        if not DATABASE_AVAILABLE:
            return False

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Notification)
                .where(Notification.id == UUID(str(notification_id)))
                .values(
                    status=NotificationStatus.DELIVERED, delivered_at=datetime.now(timezone.utc)
                )
            )
            await session.commit()
            return True

    @staticmethod
    async def subscribe_user(
        user_id: Union[str, UUID],
        subscription_data: Dict[str, Any],
        tenant_id: Union[str, UUID],
        device_id: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> NotificationSubscription:
        """Saves or updates a Web Push subscription."""
        async with AsyncSessionLocal() as session:
            # Check for existing subscription for this device/user
            # If device_id is provided, match by device.
            # If not, we might want to match by user_agent or endpoint to avoid duplicates
            endpoint = subscription_data.get("endpoint")

            query = select(NotificationSubscription).where(
                NotificationSubscription.user_id == UUID(str(user_id))
            )

            if device_id:
                query = query.where(NotificationSubscription.device_id == device_id)
            elif endpoint:
                # Fallback to matching by endpoint if no device_id
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

    @classmethod
    async def process_due_triggers(cls):
        """Finds and processes all triggers that are due for execution."""
        if not DATABASE_AVAILABLE:
            return

        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            # Select triggers where next_trigger <= now and enabled=True
            query = select(NotificationTrigger).where(
                and_(
                    NotificationTrigger.enabled == True,
                    NotificationTrigger.next_trigger <= now,
                )
            )
            result = await session.execute(query)
            due_triggers = result.scalars().all()

            for trigger in due_triggers:
                try:
                    await cls.fire_notification(trigger)

                    # Update trigger state
                    trigger.last_triggered = now

                    # Calculate next trigger if recurring
                    if trigger.trigger_type == TriggerType.RECURRING:
                        at_str = trigger.config.get("at")
                        if at_str:
                            trigger.next_trigger = cls.calculate_next_occurrence(
                                at_str, trigger.config.get("days")
                            )
                        else:
                            # Simple interval logic fallback
                            interval_mins = trigger.config.get("interval_minutes", 1440)
                            trigger.next_trigger = now + timedelta(
                                minutes=interval_mins
                            )
                    else:
                        # Non-recurring triggers should be disabled after firing
                        trigger.enabled = False
                        trigger.next_trigger = None
                except Exception as e:
                    logger.error(f"Failed to process trigger {trigger.id}: {e}")

            await session.commit()

    @staticmethod
    async def fire_notification(trigger: NotificationTrigger):
        """Creates Notification instances and enqueues delivery."""
        from app.workers.tasks import deliver_notification
        from app.models.user_model import UserModel

        async with AsyncSessionLocal() as session:
            # 1. Always create an IN_APP notification record
            in_app_notif = Notification(
                patient_id=trigger.patient_id,
                tenant_id=trigger.tenant_id,
                trigger_id=trigger.id,
                type=trigger.notification_type,
                title=trigger.title,
                body=trigger.body,
                status=NotificationStatus.PENDING,
                channel=NotificationChannel.IN_APP,
                payload={
                    "reference_id": str(trigger.reference_id)
                    if trigger.reference_id
                    else None,
                    "trigger_config": trigger.config,
                },
            )
            session.add(in_app_notif)

            # 2. Check if we should also create a PUSH notification record
            # Find users in the tenant
            users_res = await session.execute(
                select(UserModel.id).where(UserModel.tenant_id == trigger.tenant_id)
            )
            user_ids = [u[0] for u in users_res.all()]

            # Check for push subscriptions
            subs_res = await session.execute(
                select(NotificationSubscription.id).where(
                    and_(
                        NotificationSubscription.user_id.in_(user_ids),
                        NotificationSubscription.is_active == True,
                    )
                )
            )
            has_push = subs_res.first() is not None

            push_notif = None
            if has_push:
                push_notif = Notification(
                    patient_id=trigger.patient_id,
                    tenant_id=trigger.tenant_id,
                    trigger_id=trigger.id,
                    type=trigger.notification_type,
                    title=trigger.title,
                    body=trigger.body,
                    status=NotificationStatus.PENDING,
                    channel=NotificationChannel.PUSH,
                    payload={
                        "reference_id": str(trigger.reference_id)
                        if trigger.reference_id
                        else None,
                        "trigger_config": trigger.config,
                    },
                )
                session.add(push_notif)

            await session.commit()
            await session.refresh(in_app_notif)

            # Offload delivery to Celery for both
            deliver_notification.delay(str(in_app_notif.id))
            if push_notif:
                await session.refresh(push_notif)
                deliver_notification.delay(str(push_notif.id))
                logger.info(
                    f"Notifications {in_app_notif.id} (IN_APP) and {push_notif.id} (PUSH) created and queued"
                )
            else:
                logger.info(
                    f"Notification {in_app_notif.id} (IN_APP) created and queued"
                )

    @classmethod
    async def trigger_event(
        cls, event_name: str, patient_id: UUID, tenant_id: UUID, data: Dict[str, Any]
    ):
        """Triggers notifications based on system events."""
        async with AsyncSessionLocal() as session:
            query = select(NotificationTrigger).where(
                and_(
                    NotificationTrigger.patient_id == patient_id,
                    NotificationTrigger.enabled == True,
                    NotificationTrigger.trigger_type == TriggerType.EVENT,
                    NotificationTrigger.config["event_name"].astext == event_name,
                )
            )
            result = await session.execute(query)
            matching_triggers = result.scalars().all()

            for trigger in matching_triggers:
                await cls.fire_notification(trigger)
