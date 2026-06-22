from sqlalchemy import (
    Column,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Enum,
    Text,
    JSON,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.enums import NotificationType, NotificationChannel, NotificationStatus, TriggerType
from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin


class NotificationTrigger(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    __tablename__ = "notification_triggers"

    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trigger_type = Column(Enum(TriggerType), nullable=False)
    notification_type = Column(Enum(NotificationType), nullable=False)

    # Configuration for the trigger
    # e.g., {"at": "2024-03-20T10:00:00", "repeat": "daily"}
    # or {"resource": "heart_rate", "operator": ">", "value": 100}
    config = Column(JSONB, nullable=False, default=dict)

    # The message template or dynamic data
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)

    enabled = Column(Boolean, default=True)
    last_triggered = Column(DateTime(timezone=True), nullable=True)
    next_trigger = Column(DateTime(timezone=True), nullable=True, index=True)

    # Reference to original resource (e.g. Medication ID, Examination ID)
    reference_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "patient_id": str(self.patient_id),
            "trigger_type": self.trigger_type.value,
            "notification_type": self.notification_type.value,
            "config": self.config,
            "title": self.title,
            "body": self.body,
            "enabled": self.enabled,
            "last_triggered": self.last_triggered.isoformat()
            if self.last_triggered
            else None,
            "next_trigger": self.next_trigger.isoformat()
            if self.next_trigger
            else None,
            "reference_id": str(self.reference_id) if self.reference_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Notification(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Represents a single delivered notification (FHIR Communication mapping)"""

    __tablename__ = "notifications"

    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trigger_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notification_triggers.id", ondelete="SET NULL"),
        nullable=True,
    )
    communication_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_communications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    type = Column(Enum(NotificationType), nullable=False)
    status = Column(
        Enum(NotificationStatus), default=NotificationStatus.PENDING, nullable=False
    )
    channel = Column(
        Enum(NotificationChannel), default=NotificationChannel.IN_APP, nullable=False
    )

    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)

    # Dynamic payload (e.g., link to examination, icon name)
    payload = Column(JSONB, nullable=True, default=dict)

    delivered_at = Column(DateTime(timezone=True), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "patient_id": str(self.patient_id),
            "trigger_id": str(self.trigger_id) if self.trigger_id else None,
            "communication_id": str(self.communication_id) if self.communication_id else None,
            "type": self.type.value,
            "status": self.status.value,
            "channel": self.channel.value,
            "title": self.title,
            "body": self.body,
            "payload": self.payload,
            "delivered_at": self.delivered_at.isoformat()
            if self.delivered_at
            else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class NotificationSubscription(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Stores Web Push (VAPID) subscriptions for PWAs"""

    __tablename__ = "notification_subscriptions"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id = Column(String(255), nullable=True)  # Optional unique device identifier

    # Web Push Subscription JSON (endpoint, keys)
    subscription_data = Column(JSONB, nullable=False)

    user_agent = Column(String(512), nullable=True)
    is_active = Column(Boolean, default=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "device_id": self.device_id,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# Indices for performance
Index("idx_notification_patient_status", Notification.patient_id, Notification.status)
Index(
    "idx_trigger_next_run",
    NotificationTrigger.next_trigger,
    NotificationTrigger.enabled,
)
