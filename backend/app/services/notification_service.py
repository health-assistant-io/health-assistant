"""Unified notification emission, fan-out, and inbox service.

This is the single entry point for producing notifications. Every source
(integrations, the AI agent/HITL layer, biomarker rules, the scheduler,
system startup) calls :func:`emit`. The function:

1. Creates one immutable :class:`Notification` event row.
2. Resolves the target specs into concrete ``user_id``s.
3. Fans out to :class:`NotificationRecipient` (inbox state) and
   :class:`NotificationDelivery` (per-channel delivery log).
4. Optionally links a FHIR :class:`CommunicationModel` for clinical sources.
5. Publishes a real-time message to each recipient's Redis channel.
6. Enqueues a Celery delivery task for push channels.

The module also exposes the inbox read/mutation helpers used by the
``/notifications`` endpoints.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE
from app.core.redis import publish_message
from app.models.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationSeverity,
    NotificationSource,
    NotificationStatus,
    NotificationType,
    RecipientKind,
    RecipientStatus,
)
from app.models.fhir.communication import CommunicationModel
from app.models.notification import (
    Notification,
    NotificationDelivery,
    NotificationRecipient,
    NotificationSubscription,
)
from app.models.user_model import UserModel
from app.services.notification_targets import resolve_targets

logger = logging.getLogger(__name__)


# Sources that warrant a linked FHIR Communication (clinical record exposure).
_CLINICAL_SOURCES = frozenset(
    {NotificationSource.RULE, NotificationSource.CLINICAL, NotificationSource.AGENT}
)


def _uuid(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def emit(
    *,
    source: NotificationSource,
    type: NotificationType,
    category: NotificationCategory,
    title: str,
    targets: Sequence[dict],
    body: Optional[str] = None,
    payload: Optional[dict] = None,
    source_ref: Optional[dict] = None,
    severity: NotificationSeverity = NotificationSeverity.INFO,
    patient_id: str | UUID | None = None,
    tenant_id: str | UUID | None = None,
    channels: Sequence[NotificationChannel] = (
        NotificationChannel.IN_APP,
        NotificationChannel.PUSH,
    ),
    sender_user_id: str | UUID | None = None,
    trigger_id: str | UUID | None = None,
    link_communication: bool = False,
    session: Optional[AsyncSession] = None,
) -> Optional[Notification]:
    """Create a notification event and fan it out to its recipients.

    Parameters mirror the :class:`Notification` fields. ``targets`` is a
    list of ``{"kind", "id"}`` specs (see
    :mod:`app.services.notification_targets`).

    Session handling (important for the Celery worker path):

    * By default ``emit`` opens its own ``AsyncSessionLocal`` session,
      commits, dispatches (Redis publish + Celery), and returns.
    * When ``session`` is supplied (the Celery beat task injects its
      worker-scoped ``NullPool`` session to avoid the asyncpg loop-affinity
      crash), ``emit`` writes + commits on **that** session and still
      dispatches. The caller retains lifecycle ownership (no close here).

    Returns the created ``Notification`` (or ``None`` when the DB is
    unavailable or the write failed).
    """
    if not DATABASE_AVAILABLE:
        logger.warning("Notification emit skipped: database unavailable")
        return None

    tenant_uuid = _uuid(tenant_id)
    patient_uuid = _uuid(patient_id)
    sender_uuid = _uuid(sender_user_id)
    trigger_uuid = _uuid(trigger_id)

    owns_session = session is None

    async def _write(
        s: AsyncSession,
    ) -> tuple[Notification, set[UUID], list[tuple[UUID, UUID, NotificationChannel]]]:
        communication_id = None
        if (
            link_communication
            and patient_uuid is not None
            and source in _CLINICAL_SOURCES
        ):
            communication_id = await _create_communication(
                s,
                tenant_uuid,
                patient_uuid,
                title,
                body,
                payload,
                category.value,
                sender_uuid,
            )

        notification = Notification(
            tenant_id=tenant_uuid,
            patient_id=patient_uuid,
            trigger_id=trigger_uuid,
            communication_id=communication_id,
            source=source,
            type=type,
            category=category,
            severity=severity,
            title=title,
            body=body,
            payload=payload or {},
            source_ref=source_ref or {},
            sender_user_id=sender_uuid,
        )
        s.add(notification)
        await s.flush()  # populate notification.id

        resolved = await resolve_targets(s, tenant_uuid, targets)

        # Apply per-user per-source/channel preferences (USER > TENANT >
        # SYSTEM > default). Users who opted out of the source entirely are
        # dropped; users who opted out of a channel drop just that channel.
        user_channels = await _resolve_user_channel_preferences(
            s, resolved, source, channels
        )

        to_queue: list[tuple[UUID, UUID, NotificationChannel]] = []
        for user_id in resolved:
            wanted_channels = user_channels.get(user_id, list(channels))
            if not wanted_channels:
                continue
            kind, ref = _infer_recipient_meta(user_id, targets)
            s.add(
                NotificationRecipient(
                    notification_id=notification.id,
                    user_id=user_id,
                    tenant_id=tenant_uuid,
                    recipient_kind=kind,
                    recipient_ref=ref,
                    status=RecipientStatus.UNREAD,
                )
            )
            for channel in wanted_channels:
                if (
                    channel == NotificationChannel.PUSH
                    and not await _has_push_subscription(s, user_id)
                ):
                    continue
                delivery_status = (
                    NotificationStatus.DELIVERED
                    if channel == NotificationChannel.IN_APP
                    else NotificationStatus.PENDING
                )
                s.add(
                    NotificationDelivery(
                        notification_id=notification.id,
                        user_id=user_id,
                        tenant_id=tenant_uuid,
                        channel=channel,
                        status=delivery_status,
                        delivered_at=_now()
                        if channel == NotificationChannel.IN_APP
                        else None,
                    )
                )
                if channel != NotificationChannel.IN_APP:
                    to_queue.append((notification.id, user_id, channel))
        # Include the opted-out users in the announce set so the event row is
        # logged for admin visibility — but they get no inbox row + no delivery.
        return notification, resolved, to_queue

    try:
        if owns_session:
            async with AsyncSessionLocal() as session:
                notification, user_ids, to_queue = await _write(session)
                await session.commit()
                await session.refresh(notification)
        else:
            notification, user_ids, to_queue = await _write(session)
            await session.commit()
            await session.refresh(notification)

        if not user_ids:
            logger.info("Notification emitted with no resolved recipients: %s", title)

        # Real-time fan-out + push dispatch happen AFTER commit so we never
        # announce a row that failed to persist.
        await _announce(notification, user_ids)
        _enqueue_push_delivery(notification.id, to_queue)

        return notification
    except Exception as exc:
        logger.exception("Failed to emit notification %r: %s", title, exc)
        return None


def _infer_recipient_meta(
    user_id: UUID, targets: Sequence[dict]
) -> tuple[RecipientKind, Optional[UUID]]:
    """Best-effort recover the original target spec for a resolved user.

    Prefers a direct USER spec; otherwise returns the first PATIENT/DOCTOR
    spec (the resolution provenance). Falls back to a USER kind.
    """
    for spec in targets or []:
        kind = spec.get("kind")
        if kind == RecipientKind.USER.value and _uuid(spec.get("id")) == user_id:
            return RecipientKind.USER, user_id
    for spec in targets or []:
        kind = spec.get("kind")
        if kind in (RecipientKind.PATIENT.value, RecipientKind.DOCTOR.value):
            return RecipientKind(kind), _uuid(spec.get("id"))
    return RecipientKind.USER, user_id


async def _has_push_subscription(session: AsyncSession, user_id: UUID) -> bool:
    stmt = select(NotificationSubscription.id).where(
        and_(
            NotificationSubscription.user_id == user_id,
            NotificationSubscription.is_active.is_(True),
        )
    )
    return (await session.execute(stmt)).first() is not None


async def _resolve_user_channel_preferences(
    session: AsyncSession,
    user_ids: set[UUID],
    source: NotificationSource,
    channels: Sequence[NotificationChannel],
) -> dict[UUID, list[NotificationChannel]]:
    """For each user, return the channels they actually want for ``source``.

    Reads the tiered settings store (USER > TENANT > SYSTEM > default) and
    filters out:
    * users who have disabled this source entirely
      (``notifications.sources.<SOURCE> = false``)
    * channels disabled per-user (``notifications.channels.<CHANNEL> = false``)

    Returns ``{user_id: [channels they should receive on]}``. Users who
    opted out of the source entirely map to an empty list.

    Channel defaults: IN_APP=True, PUSH=True, EMAIL=False (no SMTP wired).
    Source defaults: True for all sources.
    """
    if not user_ids:
        return {}

    from app.models.user_model import UserModel
    from app.models.tenant_model import TenantModel
    from app.models.system_setting import SystemSetting

    source_key = f"notifications.sources.{source.value}"
    channel_keys = {f"notifications.channels.{c.value}" for c in channels}
    relevant_keys = {source_key} | channel_keys

    # Batched loads: one query each for users, tenants, system.
    user_rows = (
        await session.execute(
            select(UserModel.id, UserModel.settings, UserModel.tenant_id).where(
                UserModel.id.in_(user_ids)
            )
        )
    ).all()
    tenant_ids = {row[2] for row in user_rows if row[2] is not None}
    tenant_rows: dict[UUID, dict] = {}
    if tenant_ids:
        for tid, settings_json in (
            await session.execute(
                select(TenantModel.id, TenantModel.settings).where(
                    TenantModel.id.in_(tenant_ids)
                )
            )
        ).all():
            tenant_rows[tid] = settings_json or {}
    system_overrides: dict[str, Any] = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(SystemSetting.key, SystemSetting.value).where(
                    SystemSetting.key.in_(relevant_keys)
                )
            )
        ).all()
    }

    channel_defaults = {
        NotificationChannel.IN_APP: True,
        NotificationChannel.PUSH: True,
        NotificationChannel.EMAIL: False,
    }

    def resolve(
        key: str,
        default: bool,
        user_settings: dict,
        tenant_id: UUID | None,
    ) -> bool:
        if key in user_settings:
            return bool(user_settings[key])
        tenant_settings = tenant_rows.get(tenant_id, {}) if tenant_id else {}
        if key in tenant_settings:
            return bool(tenant_settings[key])
        if key in system_overrides:
            return bool(system_overrides[key])
        return default

    out: dict[UUID, list[NotificationChannel]] = {}
    for uid, user_settings_raw, tenant_id in user_rows:
        user_settings = user_settings_raw or {}

        if not resolve(source_key, True, user_settings, tenant_id):
            out[uid] = []
            continue

        kept = [
            channel
            for channel in channels
            if resolve(
                f"notifications.channels.{channel.value}",
                channel_defaults.get(channel, True),
                user_settings,
                tenant_id,
            )
        ]
        out[uid] = kept
    return out


async def _create_communication(
    session: AsyncSession,
    tenant_id: Optional[UUID],
    patient_id: UUID,
    title: str,
    body: Optional[str],
    payload: Optional[dict],
    category: str,
    sender_user_id: Optional[UUID],
) -> Optional[UUID]:
    """Create a FHIR Communication row and return its id (best-effort)."""
    try:
        comm = CommunicationModel(
            tenant_id=tenant_id,
            subject_patient_id=patient_id,
            status="completed",
            category=[{"text": category}],
            topic={"text": title},
            payload=[{"contentString": body or title}] if body or title else None,
            sent=_now(),
            sender=(
                {"reference": f"Practitioner/{sender_user_id}", "display": "system"}
                if sender_user_id
                else None
            ),
            recipient=[{"reference": f"Patient/{patient_id}"}],
        )
        session.add(comm)
        await session.flush()
        return comm.id
    except Exception:
        logger.exception("Failed to create linked Communication; continuing")
        return None


async def _announce(notification: Notification, user_ids: Iterable[UUID]) -> None:
    """Publish a real-time notification message to each recipient's channel."""
    payload = json.dumps(
        {
            "type": "notification",
            "notification": _public_payload(notification),
        },
        default=str,
    )
    for user_id in user_ids:
        try:
            await publish_message(f"user:{user_id}:notifications", payload)
        except Exception:
            logger.exception("Redis publish failed for user %s", user_id)


def _public_payload(notification: Notification) -> dict:
    return {
        "id": str(notification.id),
        "source": notification.source.value,
        "type": notification.type.value,
        "category": notification.category.value,
        "severity": notification.severity.value,
        "title": notification.title,
        "body": notification.body,
        "payload": notification.payload or {},
        "patient_id": str(notification.patient_id) if notification.patient_id else None,
        "created_at": notification.created_at.isoformat()
        if notification.created_at
        else None,
    }


def _enqueue_push_delivery(
    notification_id: UUID, deliveries: Sequence[tuple[UUID, UUID, NotificationChannel]]
) -> None:
    """Offload push/email delivery to Celery (imported lazily to avoid cycles)."""
    if not deliveries:
        return
    try:
        from app.workers.tasks import deliver_notification

        deliver_notification.delay(str(notification_id))
    except Exception:
        logger.exception("Failed to enqueue delivery for %s", notification_id)


# ---------------------------------------------------------------------------
# Inbox read + mutation helpers (used by the /notifications endpoints)
# ---------------------------------------------------------------------------


async def get_inbox(
    user_id: str | UUID,
    tenant_id: str | UUID,
    *,
    status: Optional[RecipientStatus] = None,
    category: Optional[NotificationCategory] = None,
    source: Optional[NotificationSource] = None,
    patient_id: str | UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return ``(items, total)`` for a user's inbox, newest first."""
    if not DATABASE_AVAILABLE:
        return [], 0

    user_uuid = _uuid(user_id)
    tenant_uuid = _uuid(tenant_id)
    base = (
        select(NotificationRecipient, Notification)
        .join(Notification, NotificationRecipient.notification_id == Notification.id)
        .where(
            and_(
                NotificationRecipient.user_id == user_uuid,
                NotificationRecipient.tenant_id == tenant_uuid,
            )
        )
    )
    if status is not None:
        base = base.where(NotificationRecipient.status == status)
    if category is not None:
        base = base.where(Notification.category == category)
    if source is not None:
        base = base.where(Notification.source == source)
    patient_uuid = _uuid(patient_id)
    if patient_uuid is not None:
        base = base.where(Notification.patient_id == patient_uuid)

    count_stmt = select(func.count()).select_from(base.subquery())
    async with AsyncSessionLocal() as session:
        total = (await session.execute(count_stmt)).scalar() or 0
        rows = (
            await session.execute(
                base.order_by(NotificationRecipient.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()

    items = [_inbox_item(recipient, notification) for recipient, notification in rows]
    return items, total


async def get_unread_count(user_id: str | UUID, tenant_id: str | UUID) -> int:
    if not DATABASE_AVAILABLE:
        return 0
    user_uuid = _uuid(user_id)
    tenant_uuid = _uuid(tenant_id)
    stmt = (
        select(func.count())
        .select_from(NotificationRecipient)
        .where(
            and_(
                NotificationRecipient.user_id == user_uuid,
                NotificationRecipient.tenant_id == tenant_uuid,
                NotificationRecipient.status == RecipientStatus.UNREAD,
            )
        )
    )
    async with AsyncSessionLocal() as session:
        return (await session.execute(stmt)).scalar() or 0


async def mark_read(
    recipient_id: str | UUID, user_id: str | UUID, tenant_id: str | UUID
) -> bool:
    return await _set_recipient_status(
        recipient_id, user_id, tenant_id, RecipientStatus.READ, "read_at"
    )


async def mark_dismissed(
    recipient_id: str | UUID, user_id: str | UUID, tenant_id: str | UUID
) -> bool:
    return await _set_recipient_status(
        recipient_id, user_id, tenant_id, RecipientStatus.DISMISSED, "dismissed_at"
    )


async def mark_all_read(user_id: str | UUID, tenant_id: str | UUID) -> int:
    if not DATABASE_AVAILABLE:
        return 0
    user_uuid = _uuid(user_id)
    tenant_uuid = _uuid(tenant_id)
    now = _now()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(NotificationRecipient)
            .where(
                and_(
                    NotificationRecipient.user_id == user_uuid,
                    NotificationRecipient.tenant_id == tenant_uuid,
                    NotificationRecipient.status == RecipientStatus.UNREAD,
                )
            )
            .values(status=RecipientStatus.READ, read_at=now)
        )
        await session.commit()
        return result.rowcount or 0


async def get_admin_feed(
    tenant_id: str | UUID,
    *,
    is_system_admin: bool = False,
    type: Optional[NotificationType] = None,
    source: Optional[NotificationSource] = None,
    category: Optional[NotificationCategory] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Tenant-wide (or cross-tenant for SYSTEM_ADMIN) notification feed."""
    if not DATABASE_AVAILABLE:
        return [], 0

    base = select(Notification)
    tenant_uuid = _uuid(tenant_id)
    if not is_system_admin and tenant_uuid is not None:
        base = base.where(Notification.tenant_id == tenant_uuid)
    if type is not None:
        base = base.where(Notification.type == type)
    if source is not None:
        base = base.where(Notification.source == source)
    if category is not None:
        base = base.where(Notification.category == category)

    count_stmt = select(func.count()).select_from(base.subquery())
    async with AsyncSessionLocal() as session:
        total = (await session.execute(count_stmt)).scalar() or 0
        rows = (
            (
                await session.execute(
                    base.order_by(Notification.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
    return [n.to_dict() for n in rows], total


async def get_notification_delivery_detail(
    notification_id: str | UUID,
    tenant_id: str | UUID,
    *,
    is_system_admin: bool = False,
) -> Optional[dict]:
    """Resolve a single notification to its sender + per-recipient delivery state.

    Returns ``None`` if the notification doesn't exist or is outside the
    caller's tenant scope (so the endpoint can 404).
    """
    if not DATABASE_AVAILABLE:
        return None

    notif_uuid = _uuid(notification_id)
    tenant_uuid = _uuid(tenant_id)
    if notif_uuid is None:
        return None

    async with AsyncSessionLocal() as session:
        notif_stmt = select(Notification).where(Notification.id == notif_uuid)
        if not is_system_admin and tenant_uuid is not None:
            notif_stmt = notif_stmt.where(Notification.tenant_id == tenant_uuid)
        notif = (await session.execute(notif_stmt)).scalar_one_or_none()
        if notif is None:
            return None

        # Resolve sender (if any) to an email for display.
        sender = None
        if notif.sender_user_id is not None:
            sender_row = (
                await session.execute(
                    select(UserModel.id, UserModel.email).where(
                        UserModel.id == notif.sender_user_id
                    )
                )
            ).first()
            if sender_row:
                sender = {
                    "id": str(sender_row[0]),
                    "email": sender_row[1],
                }

        # Recipients + their deliveries, with user email resolved.
        recip_rows = (
            await session.execute(
                select(NotificationRecipient, UserModel.email)
                .join(
                    UserModel,
                    UserModel.id == NotificationRecipient.user_id,
                    isouter=True,
                )
                .where(NotificationRecipient.notification_id == notif_uuid)
                .order_by(NotificationRecipient.created_at.asc())
            )
        ).all()

        delivery_rows = (
            (
                await session.execute(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.notification_id == notif_uuid)
                    .order_by(
                        NotificationDelivery.user_id, NotificationDelivery.channel
                    )
                )
            )
            .scalars()
            .all()
        )
        deliveries_by_user: dict[UUID, list[NotificationDelivery]] = {}
        for d in delivery_rows:
            deliveries_by_user.setdefault(d.user_id, []).append(d)

        recipients_out: list[dict] = []
        for recipient, email in recip_rows:
            deliv_list = deliveries_by_user.get(recipient.user_id, [])
            recipients_out.append(
                {
                    "user_id": str(recipient.user_id),
                    "user_email": email,
                    "inbox_status": recipient.status.value,
                    "read_at": recipient.read_at.isoformat()
                    if recipient.read_at
                    else None,
                    "dismissed_at": recipient.dismissed_at.isoformat()
                    if recipient.dismissed_at
                    else None,
                    "recipient_kind": recipient.recipient_kind.value,
                    "deliveries": [
                        {
                            "channel": d.channel.value,
                            "status": d.status.value,
                            "attempted_at": d.attempted_at.isoformat()
                            if d.attempted_at
                            else None,
                            "delivered_at": d.delivered_at.isoformat()
                            if d.delivered_at
                            else None,
                            "error": d.error,
                        }
                        for d in deliv_list
                    ],
                }
            )

        return {
            "notification": notif.to_dict(),
            "sender": sender,
            "recipients": recipients_out,
            "recipient_count": len(recipients_out),
        }


async def get_admin_stats(
    tenant_id: str | UUID,
    *,
    is_system_admin: bool = False,
) -> dict:
    """Aggregated delivery stats for the admin dashboard.

    Returns counts by source, by category, by channel-delivery status, and
    per-recipient inbox totals. SYSTEM_ADMIN sees cross-tenant aggregates;
    everyone else is scoped to their tenant.
    """
    if not DATABASE_AVAILABLE:
        return {
            "by_source": {},
            "by_category": {},
            "delivery": {},
            "recipients": 0,
            "unique_recipients": 0,
            "total": 0,
        }

    tenant_uuid = _uuid(tenant_id)
    # Apply the tenant scope to EACH table's own tenant_id column. Earlier
    # the filter was always on ``Notification.tenant_id`` even for delivery /
    # recipient counts, which produced a cartesian product
    # (``FROM notification_deliveries, notifications WHERE notifications.tenant_id = ...``)
    # and inflated every count by the tenant's notification total.
    notif_filter = None if is_system_admin else Notification.tenant_id == tenant_uuid
    delivery_filter = (
        None if is_system_admin else NotificationDelivery.tenant_id == tenant_uuid
    )
    recipient_filter = (
        None if is_system_admin else NotificationRecipient.tenant_id == tenant_uuid
    )

    async with AsyncSessionLocal() as session:

        def _q(stmt, flt):
            return stmt.where(flt) if flt is not None else stmt

        by_source_rows = (
            await session.execute(
                _q(
                    select(Notification.source, func.count()),
                    notif_filter,
                ).group_by(Notification.source)
            )
        ).all()
        by_category_rows = (
            await session.execute(
                _q(
                    select(Notification.category, func.count()),
                    notif_filter,
                ).group_by(Notification.category)
            )
        ).all()
        delivery_rows = (
            await session.execute(
                _q(
                    select(
                        NotificationDelivery.channel,
                        NotificationDelivery.status,
                        func.count(),
                    ),
                    delivery_filter,
                ).group_by(NotificationDelivery.channel, NotificationDelivery.status)
            )
        ).all()
        recipient_total = (
            await session.execute(
                _q(
                    select(func.count()).select_from(NotificationRecipient),
                    recipient_filter,
                )
            )
        ).scalar() or 0
        unique_recipient_total = (
            await session.execute(
                _q(
                    select(func.count(func.distinct(NotificationRecipient.user_id))),
                    recipient_filter,
                )
            )
        ).scalar() or 0
        notif_total = (
            await session.execute(
                _q(
                    select(func.count()).select_from(Notification),
                    notif_filter,
                )
            )
        ).scalar() or 0

    by_source = {str(k.value): v for k, v in by_source_rows}
    by_category = {str(k.value): v for k, v in by_category_rows}
    delivery: dict[str, dict[str, int]] = {}
    for channel, status_val, count in delivery_rows:
        delivery.setdefault(str(channel.value), {})[str(status_val.value)] = count

    return {
        "by_source": by_source,
        "by_category": by_category,
        "delivery": delivery,
        "recipients": recipient_total,
        "unique_recipients": unique_recipient_total,
        "total": notif_total,
    }


async def _set_recipient_status(
    recipient_id: str | UUID,
    user_id: str | UUID,
    tenant_id: str | UUID,
    status: RecipientStatus,
    timestamp_col: str,
) -> bool:
    if not DATABASE_AVAILABLE:
        return False
    rid = _uuid(recipient_id)
    if rid is None:
        return False
    values: dict[str, Any] = {"status": status, timestamp_col: _now()}
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(NotificationRecipient)
            .where(
                and_(
                    NotificationRecipient.id == rid,
                    NotificationRecipient.user_id == _uuid(user_id),
                    NotificationRecipient.tenant_id == _uuid(tenant_id),
                )
            )
            .values(**values)
        )
        await session.commit()
        return (result.rowcount or 0) > 0


def _inbox_item(recipient: NotificationRecipient, notification: Notification) -> dict:
    return {
        "recipient_id": str(recipient.id),
        "status": recipient.status.value,
        "read_at": recipient.read_at.isoformat() if recipient.read_at else None,
        "dismissed_at": recipient.dismissed_at.isoformat()
        if recipient.dismissed_at
        else None,
        "notification": notification.to_dict(),
    }
