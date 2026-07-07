"""Notification rules — user-configurable checks that emit notifications.

A rule is evaluated against incoming data (currently :class:`Observation`
values via :mod:`app.services.notification_rule_service`). When the
condition holds, :func:`app.services.notification_service.emit` creates a
``Notification`` event with ``source = RULE`` and fans it out to the
rule's targets.

This supersedes the legacy ``AlertModel`` (config-only, no operator, no
biomarker link, no evaluator).
"""

from sqlalchemy import (
    Column,
    String,
    Text,
    Boolean,
    DateTime,
    Integer,
    Float,
    ForeignKey,
    Enum,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin
from app.models.enums import (
    NotificationRuleType,
    ComparisonOperator,
    NotificationSeverity,
)


def _enum_values(enum_cls):
    """Persist the enum ``.value`` (not the member name)."""
    return [e.value for e in enum_cls]


class NotificationRule(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    """A user-configurable notification check."""

    __tablename__ = "notification_rules"

    rule_type = Column(
        Enum(NotificationRuleType, values_callable=_enum_values),
        nullable=False,
        index=True,
    )

    # Biomarker link (nullable for EVENT_LIFECYCLE rules, which don't
    # evaluate a biomarker value).
    biomarker_id = Column(
        UUID(as_uuid=True),
        ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Condition
    operator = Column(
        Enum(ComparisonOperator, values_callable=_enum_values), nullable=True
    )
    value = Column(Float, nullable=True)

    # Optional scope to a single patient; null = all the owner's patients.
    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    severity = Column(
        Enum(NotificationSeverity, values_callable=_enum_values),
        default=NotificationSeverity.WARNING,
        nullable=False,
    )

    enabled = Column(Boolean, default=True, nullable=False)

    # Prevent alert storms: minimum gap between repeated fires.
    cooldown_minutes = Column(Integer, default=60, nullable=False)
    last_fired_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Recipient target specs: list of {"kind": "USER|PATIENT|DOCTOR|TENANT",
    # "id": "<uuid>"}. Resolution happens in notification_targets.resolve_targets.
    targets = Column(JSONB, nullable=False, default=list)

    # Optional message override; fall back to a generated message when null.
    title_template = Column(String(255), nullable=True)
    body_template = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "rule_type": self.rule_type.value,
            "biomarker_id": str(self.biomarker_id) if self.biomarker_id else None,
            "operator": self.operator.value if self.operator else None,
            "value": self.value,
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "severity": self.severity.value,
            "enabled": self.enabled,
            "cooldown_minutes": self.cooldown_minutes,
            "last_fired_at": self.last_fired_at.isoformat()
            if self.last_fired_at
            else None,
            "targets": self.targets or [],
            "title_template": self.title_template,
            "body_template": self.body_template,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


Index(
    "idx_notification_rule_lookup",
    NotificationRule.tenant_id,
    NotificationRule.biomarker_id,
    NotificationRule.enabled,
)
Index(
    "idx_notification_rule_patient",
    NotificationRule.patient_id,
    NotificationRule.enabled,
)
