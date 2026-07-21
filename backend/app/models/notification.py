"""Unified notification system — fan-out architecture.

Three tables separate the three concerns of a notification system:

* :class:`Notification` — the immutable **event** (one row per thing that
  happened). Carries content, source, clinical context, and the optional
  ``actions`` payload. ``patient_id`` / ``tenant_id`` are nullable so a
  system-wide or tenant-wide broadcast can be represented.
* :class:`NotificationRecipient` — the **inbox state**, one row per resolved
  human recipient. Owns the read/dismiss lifecycle. Indexed on
  ``(user_id, status)`` so "my unread inbox" is a single fast lookup.
* :class:`NotificationDelivery` — the **channel delivery log**, one row per
  (recipient, channel) attempt. Tracks pending/sent/delivered/failed per
  channel plus the error message, answering "was this delivered via push?".

:class:`NotificationTrigger` (scheduled/event rules) and
:class:`NotificationSubscription` (VAPID push subscriptions) are retained.
Biomarker/threshold rules live in :mod:`app.models.notification_rule`.
"""

from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    Enum,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.models.base import (
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    TimestampMixin,
)
from app.models.enums import (
    NotificationType,
    NotificationSource,
    NotificationCategory,
    NotificationSeverity,
    NotificationChannel,
    NotificationStatus,
    RecipientKind,
    RecipientStatus,
    TriggerType,
)


def _enum_values(enum_cls):
    """Persist the enum ``.value`` (not the member name) for enums whose
    value differs from its name (e.g. lowercase / symbolic values)."""
    return [e.value for e in enum_cls]


class NotificationTrigger(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    """Defines when a scheduled/recurring/event notification should fire."""

    __tablename__ = "notification_triggers"

    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    trigger_type = Column(
        Enum(TriggerType, values_callable=_enum_values), nullable=False
    )
    notification_type = Column(
        Enum(NotificationType, values_callable=_enum_values), nullable=False
    )

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
            "patient_id": str(self.patient_id) if self.patient_id else None,
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
    """The immutable notification event (what happened).

    Fan-out targets live on :class:`NotificationRecipient`; per-channel
    delivery state on :class:`NotificationDelivery`.
    """

    __tablename__ = "notifications"

    # Clinical context (nullable so system/tenant broadcasts can exist)
    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
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

    # Origin + classification
    source = Column(
        Enum(NotificationSource, values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    type = Column(
        Enum(NotificationType, values_callable=_enum_values), nullable=False, index=True
    )
    category = Column(
        Enum(NotificationCategory, values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    severity = Column(
        Enum(NotificationSeverity, values_callable=_enum_values),
        default=NotificationSeverity.INFO,
        nullable=False,
    )

    # Content
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)

    # Dynamic payload: ``{"actions": [...], "link": "...", ...}``
    # ``actions`` shape: [{"id","label","type":"link|post","url"|"endpoint",
    #                       "method","style"}]
    payload = Column(JSONB, nullable=True, default=dict)

    # Originating reference (integration domain, agent session id, rule id,
    # biomarker_id, observation_id, ...).
    source_ref = Column(JSONB, nullable=True, default=dict)

    # The user account that triggered the emission, if any (system/agent
    # emissions leave this null).
    sender_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    # Item 4 of integrations-sdk-improvements: digest collapsing.
    # When ``dedup_key`` is set, the partial unique index
    # ``uq_notification_dedup`` enforces one row per
    # ``(tenant_id, dedup_key)`` while the row's ``dedup_expires_at`` is
    # in the future. ``notification_service.emit`` consults these to
    # collapse repeated threshold / summary emissions into one inbox
    # entry inside the TTL window.
    dedup_key = Column(String(64), nullable=True, index=True)
    dedup_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "trigger_id": str(self.trigger_id) if self.trigger_id else None,
            "communication_id": str(self.communication_id)
            if self.communication_id
            else None,
            "source": self.source.value,
            "type": self.type.value,
            "category": self.category.value,
            "severity": self.severity.value,
            "title": self.title,
            "body": self.body,
            "payload": self.payload,
            "source_ref": self.source_ref,
            "sender_user_id": str(self.sender_user_id) if self.sender_user_id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "dedup_key": getattr(self, "dedup_key", None),
            "dedup_expires_at": self.dedup_expires_at.isoformat()
            if getattr(self, "dedup_expires_at", None)
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class NotificationRecipient(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Per-user inbox state for a notification (the fan-out result).

    One row per resolved human recipient. ``user_id`` is the delivery
    target (a login account). ``recipient_kind``/``recipient_ref`` retain
    the original target spec for traceability (e.g. "this user was
    targeted via PATIENT <id>").
    """

    __tablename__ = "notification_recipients"

    notification_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    recipient_kind = Column(
        Enum(RecipientKind, values_callable=_enum_values), nullable=False
    )
    recipient_ref = Column(UUID(as_uuid=True), nullable=True)

    status = Column(
        Enum(RecipientStatus, values_callable=_enum_values),
        default=RecipientStatus.UNREAD,
        nullable=False,
    )
    read_at = Column(DateTime(timezone=True), nullable=True)
    dismissed_at = Column(DateTime(timezone=True), nullable=True)

    notification = relationship("Notification", lazy="selectin", innerjoin=False)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "notification_id": str(self.notification_id),
            "user_id": str(self.user_id),
            "recipient_kind": self.recipient_kind.value,
            "recipient_ref": str(self.recipient_ref) if self.recipient_ref else None,
            "status": self.status.value,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "dismissed_at": self.dismissed_at.isoformat()
            if self.dismissed_at
            else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class NotificationDelivery(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Per-recipient, per-channel delivery attempt log."""

    __tablename__ = "notification_deliveries"

    notification_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel = Column(
        Enum(NotificationChannel, values_callable=_enum_values), nullable=False
    )
    status = Column(
        Enum(NotificationStatus, values_callable=_enum_values),
        default=NotificationStatus.PENDING,
        nullable=False,
    )

    attempted_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)

    # Optional link to the NotificationSubscription used (push only).
    subscription_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notification_subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "notification_id": str(self.notification_id),
            "user_id": str(self.user_id),
            "channel": self.channel.value,
            "status": self.status.value,
            "attempted_at": self.attempted_at.isoformat()
            if self.attempted_at
            else None,
            "delivered_at": self.delivered_at.isoformat()
            if self.delivered_at
            else None,
            "error": self.error,
            "subscription_id": str(self.subscription_id)
            if self.subscription_id
            else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class NotificationSubscription(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Stores Web Push (VAPID) subscriptions for PWAs."""

    __tablename__ = "notification_subscriptions"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id = Column(String(255), nullable=True)

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
Index(
    "idx_notification_recipient_user_status",
    NotificationRecipient.user_id,
    NotificationRecipient.status,
)
Index(
    "idx_notification_recipient_tenant_status",
    NotificationRecipient.tenant_id,
    NotificationRecipient.status,
)
Index(
    "idx_notification_delivery_lookup",
    NotificationDelivery.notification_id,
    NotificationDelivery.channel,
)
Index(
    "idx_trigger_next_run",
    NotificationTrigger.next_trigger,
    NotificationTrigger.enabled,
)
