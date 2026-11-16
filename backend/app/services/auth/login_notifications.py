"""Sign-in alert: a successful login from a device the account has not used.

The control self-hosted multi-device users actually need: if someone signs
in with a stolen password from a machine you do not recognise, the alert
lands in your inbox (in-app + push — never email; the instance may have no
mail transport) at the same moment the session opens.

"New" means **no live ``auth_sessions`` family of this user carries the
same (ip, user agent) pair** — the authoritative device record, not a
heuristic. A family stored before migration ``0019_session_metadata`` has
NULL device facts and therefore never matches (re-alerting is safer than
missing a compromise).

Delivery goes through :func:`app.services.notifications.notification_service.emit`,
so channel preferences apply (a user who turned push off still gets the
in-app row); the event type is safety-critical — the source-level mute does
not silence it (see ``notification_kind_registry._IMMUTABLE_TYPES``).

No PHI: the payload carries the device label, the source address and the
authentication method only.
"""

from __future__ import annotations

import hashlib
import logging

from app.models.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationSeverity,
    NotificationSource,
    NotificationType,
    RecipientKind,
)
from app.models.notification import Notification
from app.services.auth.auth_session_service import device_hint, known_device
from app.services.notifications.notification_service import emit

logger = logging.getLogger(__name__)

#: One alert per device per day: a crash-looped client must not flood the
#: inbox with the same sign-in.
_ALERT_DEDUP_SECONDS = 24 * 60 * 60


def _fingerprint(ip: str | None, user_agent: str | None) -> str:
    digest = hashlib.sha256(f"{ip or ''}\x00{user_agent or ''}".encode()).hexdigest()[:16]
    return digest


def _event_dedup_key(event: str, user_id, dedup: str) -> str:
    """One inbox row per event subject per user, inside ``dedup_key``'s 64 chars.

    The raw ``f"{event}:{user_id}:{dedup}"`` shape overflows the
    ``notifications.dedup_key`` column (uuid pairs alone are 73 characters),
    which made every factor-lifecycle notice fail to insert silently. The
    subject is hashed instead; the event name stays readable for operators.
    """
    digest = hashlib.sha256(f"{user_id}\x00{dedup}".encode()).hexdigest()[:16]
    return f"{event}:{digest}"[:64]


async def notify_new_device(
    user,
    *,
    ip: str | None,
    user_agent: str | None,
    method: str,
    family_id: str | None = None,
) -> Notification | None:
    """Emit a security notification when this sign-in is from a new device.

    Returns the notification (or ``None`` when the device is known, the
    record is unusable, or the emission failed — the sign-in itself must
    never fail because an alert could not be written).
    """
    try:
        if await known_device(user.id, ip=ip, user_agent=user_agent, exclude_family_id=family_id):
            return None
        label = device_hint(user_agent, max_length=120) or "Unknown device"
        where = ip or "unknown address"
        title = "New sign-in to your account"
        body = f"{label} from {where} ({method}). If this was not you, sign out that device."
        return await emit(
            source=NotificationSource.SYSTEM,
            type=NotificationType.SECURITY_EVENT,
            category=NotificationCategory.SECURITY,
            severity=NotificationSeverity.WARNING,
            title=title,
            body=body,
            tenant_id=getattr(user, "tenant_id", None),
            targets=[{"kind": RecipientKind.USER.value, "id": str(user.id)}],
            channels=(NotificationChannel.IN_APP, NotificationChannel.PUSH),
            source_ref={
                "event": "auth.login_new_device",
                "method": method,
                "ip": ip,
                "device": label,
            },
            dedup_key=f"signin:{user.id}:{_fingerprint(ip, user_agent)}",
            dedup_ttl_seconds=_ALERT_DEDUP_SECONDS,
        )
    except Exception:
        logger.exception("Sign-in alert could not be emitted (sign-in proceeds)")
        return None


async def notify_security_event(
    user,
    *,
    event: str,
    title: str,
    body: str,
    dedup: str,
    severity: NotificationSeverity = NotificationSeverity.WARNING,
) -> Notification | None:
    """Emit one safety-critical security notification (in-app + push).

    Used by the passkey lifecycle (credential added/deleted, clone alarm).
    Failures are swallowed — the security action itself must not fail
    because an alert could not be written — and the payload carries no PHI.
    """
    try:
        return await emit(
            source=NotificationSource.SYSTEM,
            type=NotificationType.SECURITY_EVENT,
            category=NotificationCategory.SECURITY,
            severity=severity,
            title=title,
            body=body,
            tenant_id=getattr(user, "tenant_id", None),
            targets=[{"kind": RecipientKind.USER.value, "id": str(user.id)}],
            channels=(NotificationChannel.IN_APP, NotificationChannel.PUSH),
            source_ref={"event": event},
            dedup_key=_event_dedup_key(event, user.id, dedup),
            dedup_ttl_seconds=_ALERT_DEDUP_SECONDS,
        )
    except Exception:
        logger.exception("Security notification could not be emitted (%s)", event)
        return None


__all__ = ["notify_new_device", "notify_security_event"]
