"""Notification rule service — CRUD + event-driven evaluation.

A :class:`NotificationRule` is a user-configured check. When an
``Observation`` is created (lab/wearable/manual entry), the ingestion hook
in :mod:`app.services.fhir_service` calls :func:`evaluate_and_fire`, which
finds enabled rules for that biomarker + patient + tenant and tests the new
value against each rule's condition. Matching rules emit a notification via
:func:`app.services.notification_service.emit` (with ``source = RULE``) and
respect the per-rule cooldown to avoid alert storms.

CRUD helpers mirror the rest of the codebase: tenant-scoped, fail-soft,
``None`` on cross-tenant access (so endpoints can surface 404).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE
from app.models.biomarker_model import BiomarkerDefinition
from app.models.enums import (
    ComparisonOperator,
    NotificationCategory,
    NotificationRuleType,
    NotificationSeverity,
    NotificationSource,
    NotificationType,
    RecipientKind,
)
from app.models.fhir.patient import Observation
from app.models.notification_rule import NotificationRule
from app.services import notification_service

logger = logging.getLogger(__name__)


def _uuid(value: str | UUID | None) -> Optional[UUID]:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_rule(rule_data: dict, tenant_id: str | UUID) -> Optional[NotificationRule]:
    if not DATABASE_AVAILABLE:
        return None
    tenant_uuid = _uuid(tenant_id)
    rule = NotificationRule(
        tenant_id=tenant_uuid,
        rule_type=NotificationRuleType(rule_data["rule_type"]),
        biomarker_id=_uuid(rule_data.get("biomarker_id")),
        operator=ComparisonOperator(rule_data["operator"]) if rule_data.get("operator") else None,
        value=rule_data.get("value"),
        patient_id=_uuid(rule_data.get("patient_id")),
        severity=NotificationSeverity(rule_data.get("severity", "warning")),
        enabled=rule_data.get("enabled", True),
        cooldown_minutes=int(rule_data.get("cooldown_minutes", 60)),
        targets=rule_data.get("targets") or [],
        title_template=rule_data.get("title_template"),
        body_template=rule_data.get("body_template"),
    )
    async with AsyncSessionLocal() as session:
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
    return rule


async def get_rule(rule_id: str | UUID, tenant_id: str | UUID) -> Optional[NotificationRule]:
    if not DATABASE_AVAILABLE:
        return None
    rid = _uuid(rule_id)
    if rid is None:
        return None
    async with AsyncSessionLocal() as session:
        stmt = select(NotificationRule).where(NotificationRule.id == rid)
        tenant_uuid = _uuid(tenant_id)
        if tenant_uuid is not None:
            stmt = stmt.where(NotificationRule.tenant_id == tenant_uuid)
        return (await session.execute(stmt)).scalar_one_or_none()


async def list_rules(
    tenant_id: str | UUID,
    *,
    patient_id: str | UUID | None = None,
    biomarker_id: str | UUID | None = None,
    enabled: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[NotificationRule]:
    if not DATABASE_AVAILABLE:
        return []
    tenant_uuid = _uuid(tenant_id)
    stmt = select(NotificationRule)
    if tenant_uuid is not None:
        stmt = stmt.where(NotificationRule.tenant_id == tenant_uuid)
    patient_uuid = _uuid(patient_id)
    if patient_uuid is not None:
        stmt = stmt.where(NotificationRule.patient_id == patient_uuid)
    biomarker_uuid = _uuid(biomarker_id)
    if biomarker_uuid is not None:
        stmt = stmt.where(NotificationRule.biomarker_id == biomarker_uuid)
    if enabled is not None:
        stmt = stmt.where(NotificationRule.enabled == enabled)
    stmt = stmt.order_by(NotificationRule.created_at.desc()).limit(limit).offset(offset)
    async with AsyncSessionLocal() as session:
        return list((await session.execute(stmt)).scalars().all())


async def update_rule(
    rule_id: str | UUID, updates: dict, tenant_id: str | UUID
) -> Optional[NotificationRule]:
    rule = await get_rule(rule_id, tenant_id)
    if rule is None:
        return None
    field_map = {
        "operator": lambda v: ComparisonOperator(v) if v else None,
        "severity": lambda v: NotificationSeverity(v) if v else None,
        "rule_type": lambda v: NotificationRuleType(v) if v else None,
        "biomarker_id": _uuid,
        "patient_id": _uuid,
        "value": lambda v: v,
        "enabled": lambda v: v,
        "cooldown_minutes": lambda v: int(v) if v is not None else None,
        "targets": lambda v: v,
        "title_template": lambda v: v,
        "body_template": lambda v: v,
    }
    async with AsyncSessionLocal() as session:
        rule = await session.get(NotificationRule, rule.id)
        if rule is None:
            return None
        for key, value in updates.items():
            if key in field_map and value is not None:
                setattr(rule, key, field_map[key](value))
        await session.commit()
        await session.refresh(rule)
    return rule


async def delete_rule(rule_id: str | UUID, tenant_id: str | UUID) -> bool:
    if not DATABASE_AVAILABLE:
        return False
    rid = _uuid(rule_id)
    if rid is None:
        return False
    async with AsyncSessionLocal() as session:
        rule = await get_rule(rule_id, tenant_id)
        if rule is None:
            return False
        await session.delete(await session.get(NotificationRule, rule.id))
        await session.commit()
        return True


async def test_fire(rule_id: str | UUID, tenant_id: str | UUID) -> bool:
    """Emit a one-off test notification for a rule (ignores cooldown/data)."""
    rule = await get_rule(rule_id, tenant_id)
    if rule is None:
        return False
    name = "rule"
    if rule.biomarker_id is not None:
        async with AsyncSessionLocal() as session:
            biomarker = await session.get(BiomarkerDefinition, rule.biomarker_id)
            if biomarker:
                name = getattr(biomarker, "name", None) or rule.biomarker_id
    title = rule.title_template or f"Test alert: {name}"
    body = rule.body_template or "This is a test firing of your notification rule."
    targets = rule.targets or [{"kind": RecipientKind.TENANT.value, "id": str(tenant_id)}]
    await notification_service.emit(
        source=NotificationSource.RULE,
        type=NotificationType.BIOMARKER_THRESHOLD,
        category=NotificationCategory.ALERT,
        severity=rule.severity,
        title=title,
        body=body,
        tenant_id=tenant_id,
        patient_id=rule.patient_id,
        targets=targets,
        payload={"test": True, "rule_id": str(rule.id)},
        source_ref={"rule_id": str(rule.id), "test": True},
        link_communication=rule.patient_id is not None,
    )
    return True


# ---------------------------------------------------------------------------
# Evaluation (event-driven on observation create)
# ---------------------------------------------------------------------------


async def evaluate_and_fire(
    observation: Observation,
    patient_id: UUID,
    tenant_id: UUID,
) -> int:
    """Evaluate all enabled rules against ``observation``; fire matches.

    Returns the number of rules that fired. Called from
    :func:`app.services.fhir_service.create_observation` after the
    observation (and its biomarker link) is committed.
    """
    if not DATABASE_AVAILABLE or observation.biomarker_id is None:
        return 0

    value = _observation_value(observation)
    if value is None:
        return 0

    async with AsyncSessionLocal() as session:
        rules = await _candidate_rules(
            session, tenant_id, patient_id, observation.biomarker_id
        )
        biomarker = await session.get(BiomarkerDefinition, observation.biomarker_id)
        fired = 0
        now = datetime.now(timezone.utc)
        for rule in rules:
            if not _is_match(rule, value, biomarker):
                continue
            if _in_cooldown(rule, now):
                continue
            await _fire(session, rule, observation, biomarker, value, patient_id, tenant_id)
            rule.last_fired_at = now
            fired += 1
        if fired:
            await session.commit()
    return fired


async def _candidate_rules(
    session: AsyncSession,
    tenant_id: UUID,
    patient_id: UUID,
    biomarker_id: UUID,
) -> list[NotificationRule]:
    """Enabled biomarker rules for the patient OR the whole tenant."""
    stmt = select(NotificationRule).where(
        and_(
            NotificationRule.enabled.is_(True),
            NotificationRule.tenant_id == tenant_id,
            NotificationRule.biomarker_id == biomarker_id,
            NotificationRule.rule_type.in_(
                [
                    NotificationRuleType.BIOMARKER_THRESHOLD,
                    NotificationRuleType.OUT_OF_NORMAL_RANGE,
                ]
            ),
            or_(NotificationRule.patient_id.is_(None), NotificationRule.patient_id == patient_id),
        )
    )
    return list((await session.execute(stmt)).scalars().all())


def _is_match(
    rule: NotificationRule, value: float, biomarker: Optional[BiomarkerDefinition]
) -> bool:
    try:
        if rule.rule_type == NotificationRuleType.OUT_OF_NORMAL_RANGE:
            return _is_out_of_normal(value, biomarker)
        if rule.operator is None:
            return False
        threshold = rule.value
        if threshold is None:
            return False
        op = rule.operator
        if op == ComparisonOperator.GT:
            return value > threshold
        if op == ComparisonOperator.LT:
            return value < threshold
        if op == ComparisonOperator.GTE:
            return value >= threshold
        if op == ComparisonOperator.LTE:
            return value <= threshold
        if op == ComparisonOperator.EQ:
            return value == threshold
        if op == ComparisonOperator.OUT_OF_NORMAL:
            return _is_out_of_normal(value, biomarker)
    except Exception:
        logger.exception("Rule %s evaluation failed", getattr(rule, "id", "?"))
    return False


def _is_out_of_normal(value: float, biomarker: Optional[BiomarkerDefinition]) -> bool:
    if not biomarker:
        return False
    low = getattr(biomarker, "reference_range_min", None)
    high = getattr(biomarker, "reference_range_max", None)
    if low is not None and value < low:
        return True
    if high is not None and value > high:
        return True
    return False


def _in_cooldown(rule: NotificationRule, now: datetime) -> bool:
    if rule.last_fired_at is None:
        return False
    cooldown = timedelta(minutes=rule.cooldown_minutes or 0)
    return now - rule.last_fired_at < cooldown


async def _fire(
    session: AsyncSession,
    rule: NotificationRule,
    observation: Observation,
    biomarker: Optional[BiomarkerDefinition],
    value: float,
    patient_id: UUID,
    tenant_id: UUID,
) -> None:
    """Emit a RULE notification for a matching rule (no session commit here)."""
    name = getattr(biomarker, "name", None) or getattr(biomarker, "slug", "biomarker")
    title = rule.title_template or f"{name} alert"
    body = rule.body_template or _default_body(rule, value, biomarker)
    targets = rule.targets or [
        {"kind": RecipientKind.PATIENT.value, "id": str(patient_id)}
    ]
    await notification_service.emit(
        source=NotificationSource.RULE,
        type=NotificationType.BIOMARKER_THRESHOLD,
        category=NotificationCategory.ALERT,
        severity=rule.severity,
        title=title,
        body=body,
        patient_id=patient_id,
        tenant_id=tenant_id,
        targets=targets,
        payload={
            "value": value,
            "biomarker_id": str(observation.biomarker_id),
            "observation_id": str(observation.id),
            "rule_id": str(rule.id),
            "operator": rule.operator.value if rule.operator else None,
            "threshold": rule.value,
            "actions": [
                {
                    "id": "view",
                    "label": "View biomarker",
                    "type": "link",
                    "url": f"/patients/{patient_id}/biomarkers/{observation.biomarker_id}",
                }
            ],
        },
        source_ref={
            "rule_id": str(rule.id),
            "biomarker_id": str(observation.biomarker_id),
            "observation_id": str(observation.id),
        },
        link_communication=True,
    )


def _default_body(
    rule: NotificationRule, value: float, biomarker: Optional[BiomarkerDefinition]
) -> str:
    name = getattr(biomarker, "name", None) or getattr(biomarker, "slug", "biomarker")
    op = rule.operator.value if rule.operator else "out of range"
    threshold = rule.value
    if threshold is not None:
        return f"{name} is {value} ({op} {threshold})."
    return f"{name} is {value}, which is {op}."


def _observation_value(observation: Observation) -> Optional[float]:
    """Pick the numeric value to evaluate against."""
    raw = getattr(observation, "normalized_value", None)
    if raw is None:
        raw = getattr(observation, "raw_value", None)
    if raw is None:
        vq = getattr(observation, "value_quantity", None)
        if isinstance(vq, dict):
            raw = vq.get("value")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
