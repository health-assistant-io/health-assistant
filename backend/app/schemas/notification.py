"""Pydantic schemas for the unified notification system."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationSeverity,
    NotificationSource,
    NotificationStatus,
    NotificationType,
    RecipientKind,
    RecipientStatus,
    TriggerType,
)


class TargetSpec(BaseModel):
    """A notification target spec (pre-resolution)."""

    kind: RecipientKind
    id: Optional[UUID] = None


class NotificationAction(BaseModel):
    """An actionable button attached to a notification."""

    id: str
    label: str
    type: str = Field(..., description="'link' or 'post'")
    url: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    style: Optional[str] = None


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: Optional[UUID] = None
    trigger_id: Optional[UUID] = None
    communication_id: Optional[UUID] = None
    source: NotificationSource
    type: NotificationType
    category: NotificationCategory
    severity: NotificationSeverity
    title: str
    body: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    source_ref: Optional[dict[str, Any]] = None
    sender_user_id: Optional[UUID] = None
    tenant_id: Optional[UUID] = None
    created_at: Optional[datetime] = None


class NotificationRecipientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recipient_id: UUID
    status: RecipientStatus
    read_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    notification: NotificationRead


class AdminFeedResponse(BaseModel):
    items: list[NotificationRead]
    total: int


class InboxResponse(BaseModel):
    items: list[NotificationRecipientRead]
    total: int


class UnreadCountResponse(BaseModel):
    count: int


class NotificationDeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    notification_id: UUID
    user_id: UUID
    channel: NotificationChannel
    status: NotificationStatus
    attempted_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    error: Optional[str] = None
    subscription_id: Optional[UUID] = None
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Notification rules
# ---------------------------------------------------------------------------


class NotificationRuleCreate(BaseModel):
    rule_type: str
    biomarker_id: Optional[UUID] = None
    operator: Optional[str] = None
    value: Optional[float] = None
    patient_id: Optional[UUID] = None
    severity: str = "warning"
    enabled: bool = True
    cooldown_minutes: int = 60
    targets: list[TargetSpec] = Field(default_factory=list)
    title_template: Optional[str] = None
    body_template: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        data["targets"] = [t.model_dump(mode="json") for t in self.targets]
        return data


class NotificationRuleUpdate(BaseModel):
    rule_type: Optional[str] = None
    biomarker_id: Optional[UUID] = None
    operator: Optional[str] = None
    value: Optional[float] = None
    patient_id: Optional[UUID] = None
    severity: Optional[str] = None
    enabled: Optional[bool] = None
    cooldown_minutes: Optional[int] = None
    targets: Optional[list[TargetSpec]] = None
    title_template: Optional[str] = None
    body_template: Optional[str] = None

    def to_updates(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True, exclude_unset=False)
        if self.targets is not None:
            data["targets"] = [t.model_dump(mode="json") for t in self.targets]
        return {k: v for k, v in data.items() if v is not None}


class NotificationRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: Optional[UUID] = None
    rule_type: str
    biomarker_id: Optional[UUID] = None
    operator: Optional[str] = None
    value: Optional[float] = None
    patient_id: Optional[UUID] = None
    severity: str
    enabled: bool
    cooldown_minutes: int
    last_fired_at: Optional[datetime] = None
    targets: list[dict[str, Any]] = Field(default_factory=list)
    title_template: Optional[str] = None
    body_template: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class NotificationRuleListResponse(BaseModel):
    items: list[NotificationRuleRead]
    total: int


class TriggerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: Optional[UUID] = None
    trigger_type: TriggerType
    notification_type: NotificationType
    config: Optional[dict[str, Any]] = None
    title: str
    body: Optional[str] = None
    enabled: bool
    last_triggered: Optional[datetime] = None
    next_trigger: Optional[datetime] = None
    reference_id: Optional[UUID] = None
    created_at: Optional[datetime] = None


class TriggerCreate(BaseModel):
    patient_id: Optional[UUID] = None
    notification_type: str = "MEDICATION_REMINDER"
    trigger_type: str = "TIME"
    config: dict[str, Any]
    title: str
    body: Optional[str] = None
    reference_id: Optional[UUID] = None
    enabled: bool = True


class SubscribeRequest(BaseModel):
    """Body for ``POST /notifications/subscribe``.

    The browser sends the Web Push subscription JSON (endpoint + keys) plus
    optional device metadata. Modeling this as a Pydantic body (rather than
    a bare ``dict`` + query params) ensures ``subscription`` is the actual
    push subscription, not the whole wrapped request envelope.
    """

    subscription: dict[str, Any]
    device_id: Optional[str] = None
    user_agent: Optional[str] = None
