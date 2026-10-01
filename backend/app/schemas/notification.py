"""Pydantic schemas for the unified notification system."""

from __future__ import annotations

from datetime import datetime
from typing import Any
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
    id: UUID | None = None


class NotificationAction(BaseModel):
    """An actionable button attached to a notification."""

    id: str
    label: str
    type: str = Field(..., description="'link' or 'post'")
    url: str | None = None
    endpoint: str | None = None
    method: str | None = None
    style: str | None = None


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID | None = None
    trigger_id: UUID | None = None
    communication_id: UUID | None = None
    source: NotificationSource
    type: NotificationType
    category: NotificationCategory
    severity: NotificationSeverity
    title: str
    body: str | None = None
    payload: dict[str, Any] | None = None
    source_ref: dict[str, Any] | None = None
    sender_user_id: UUID | None = None
    tenant_id: UUID | None = None
    created_at: datetime | None = None


class NotificationRecipientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recipient_id: UUID
    status: RecipientStatus
    read_at: datetime | None = None
    dismissed_at: datetime | None = None
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
    attempted_at: datetime | None = None
    delivered_at: datetime | None = None
    error: str | None = None
    subscription_id: UUID | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Notification rules
# ---------------------------------------------------------------------------


class NotificationRuleCreate(BaseModel):
    rule_type: str
    biomarker_id: UUID | None = None
    operator: str | None = None
    value: float | None = None
    patient_id: UUID | None = None
    severity: str = "warning"
    enabled: bool = True
    cooldown_minutes: int = 60
    targets: list[TargetSpec] = Field(default_factory=list)
    title_template: str | None = None
    body_template: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        data["targets"] = [t.model_dump(mode="json") for t in self.targets]
        return data


class NotificationRuleUpdate(BaseModel):
    rule_type: str | None = None
    biomarker_id: UUID | None = None
    operator: str | None = None
    value: float | None = None
    patient_id: UUID | None = None
    severity: str | None = None
    enabled: bool | None = None
    cooldown_minutes: int | None = None
    targets: list[TargetSpec] | None = None
    title_template: str | None = None
    body_template: str | None = None

    def to_updates(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True, exclude_unset=False)
        if self.targets is not None:
            data["targets"] = [t.model_dump(mode="json") for t in self.targets]
        return {k: v for k, v in data.items() if v is not None}


class NotificationRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    rule_type: str
    biomarker_id: UUID | None = None
    operator: str | None = None
    value: float | None = None
    patient_id: UUID | None = None
    severity: str
    enabled: bool
    cooldown_minutes: int
    last_fired_at: datetime | None = None
    targets: list[dict[str, Any]] = Field(default_factory=list)
    title_template: str | None = None
    body_template: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NotificationRuleListResponse(BaseModel):
    items: list[NotificationRuleRead]
    total: int


class TriggerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID | None = None
    trigger_type: TriggerType
    notification_type: NotificationType
    config: dict[str, Any] | None = None
    title: str
    body: str | None = None
    enabled: bool
    last_triggered: datetime | None = None
    next_trigger: datetime | None = None
    reference_id: UUID | None = None
    created_at: datetime | None = None


class TriggerCreate(BaseModel):
    patient_id: UUID | None = None
    notification_type: str = "MEDICATION_REMINDER"
    trigger_type: str = "TIME"
    config: dict[str, Any]
    title: str
    body: str | None = None
    reference_id: UUID | None = None
    enabled: bool = True


class SubscribeRequest(BaseModel):
    """Body for ``POST /notifications/subscribe``.

    The browser sends the Web Push subscription JSON (endpoint + keys) plus
    optional device metadata. Modeling this as a Pydantic body (rather than
    a bare ``dict`` + query params) ensures ``subscription`` is the actual
    push subscription, not the whole wrapped request envelope.
    """

    subscription: dict[str, Any]
    device_id: str | None = None
    user_agent: str | None = None


# ---------------------------------------------------------------------------
# Notification preferences (unified per-kind mute/manage model)
# ---------------------------------------------------------------------------


class NotificationKindState(BaseModel):
    """One addressable kind with its current enabled state.

    Returned by ``GET /notifications/preferences``. The ``kind_id`` is the
    single contract — the frontend never derives it from source/type.
    """

    kind_id: str
    label: str
    group: str
    manage_url: str
    mutable: bool
    default_enabled: bool = True
    enabled: bool


class NotificationPreferencesResponse(BaseModel):
    preferences: list[NotificationKindState]


class NotificationPreferenceUpdate(BaseModel):
    """Body for ``PUT /notifications/preferences/{kind_id}``."""

    enabled: bool


class NotificationPreferencesHint(BaseModel):
    """The hint stamped into ``payload.preferences`` at emit time.

    Read by the notification detail modal / bell dropdown to render a
    per-notification "Turn off this kind" button + "Notification settings"
    link. Absent on emissions whose kind couldn't be resolved (the frontend
    then hides the controls — graceful degradation).
    """

    kind_id: str
    label: str
    manage_url: str
    mutable: bool
