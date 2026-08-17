"""Mobile push target registration + delivery helpers.

The registration side (register / unregister / list_devices) is called from
the bridge integration's ``POST /notifications/register-device`` and
``DELETE /notifications/register-device/{device_id}`` paths. Each mobile
install registers exactly once (the ``(user_id, device_id)`` pair is
unique, so re-registering upserts — e.g. when the user picks a new
UnifiedPush distributor).

The delivery side (``dispatch`` + ``send_unifiedpush`` + ``send_fcm``) is
called from the ``dispatch_mobile_push`` Celery task after the existing
``deliver_notification`` task finishes its Web Push fan-out. It reads the
active ``MobilePushTarget`` rows for a recipient user and POSTs the
notification payload to each device's endpoint.

Channel preference is enforced upstream: ``notification_service.emit``
creates a ``NotificationDelivery(channel=PUSH)`` row only when the user
hasn't muted PUSH for the notification's kind. The dispatch task respects
that — it only fires for notifications that already have a PENDING PUSH
delivery row.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import MobilePushTarget

logger = logging.getLogger(__name__)

# Per-device HTTP timeout — push endpoints are usually fast, but a single
# slow distributor shouldn't block the dispatch loop. Keep it short; the
# task retries on the next emission if this attempt times out.
_PUSH_HTTP_TIMEOUT = 8.0


async def register_device(
    db: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    device_id: str,
    platform: str,
    endpoint_url: str,
    encryption_pubkey: str | None = None,
    app_version: str | None = None,
    user_agent: str | None = None,
) -> MobilePushTarget:
    """Upsert a device registration.

    The ``(user_id, device_id)`` pair is unique, so a re-registration (the
    same install id after the user picks a new distributor, an app update,
    etc.) updates the existing row instead of duplicating. Uses Postgres
    ``INSERT ... ON CONFLICT ... DO UPDATE`` so concurrent registrations
    don't race.
    """
    platform_norm = (platform or "").lower().strip()
    if platform_norm not in ("unifiedpush", "fcm"):
        raise ValueError(
            f"Unsupported platform '{platform}'. Must be 'unifiedpush' or 'fcm'."
        )
    if not endpoint_url:
        raise ValueError("endpoint_url is required.")

    now = datetime.datetime.now(datetime.timezone.utc)
    stmt = (
        pg_insert(MobilePushTarget)
        .values(
            user_id=user_id,
            tenant_id=tenant_id,
            device_id=device_id,
            platform=platform_norm,
            endpoint_url=endpoint_url,
            encryption_pubkey=encryption_pubkey,
            app_version=app_version,
            user_agent=user_agent,
            is_active=True,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "device_id"],
            set_={
                "platform": platform_norm,
                "endpoint_url": endpoint_url,
                "encryption_pubkey": encryption_pubkey,
                "app_version": app_version,
                "user_agent": user_agent,
                "is_active": True,
                "last_seen_at": now,
                "updated_at": now,
            },
        )
        .returning(MobilePushTarget)
    )
    res = await db.execute(stmt)
    row = res.scalar_one()
    await db.commit()
    return row


async def unregister_device(
    db: AsyncSession, *, user_id: UUID, device_id: str
) -> bool:
    """Soft-deactivate a device (sign-out / lost-device). Hard-delete is not
    exposed — the row is retained for audit + to detect a re-registration
    of the same device id (which re-activates it via the upsert above).

    Returns True if a row was deactivated, False if no such (user, device)
    pair exists."""
    now = datetime.datetime.now(datetime.timezone.utc)
    res = await db.execute(
        update(MobilePushTarget)
        .where(
            MobilePushTarget.user_id == user_id,
            MobilePushTarget.device_id == device_id,
            MobilePushTarget.is_active.is_(True),
        )
        .values(is_active=False, updated_at=now, last_seen_at=now)
    )
    await db.commit()
    return (res.rowcount or 0) > 0


async def list_devices(
    db: AsyncSession,
    *,
    user_id: UUID,
    include_inactive: bool = False,
) -> list[MobilePushTarget]:
    """The 'Where am I signed in' list. Defaults to active-only."""
    stmt = select(MobilePushTarget).where(MobilePushTarget.user_id == user_id)
    if not include_inactive:
        stmt = stmt.where(MobilePushTarget.is_active.is_(True))
    stmt = stmt.order_by(MobilePushTarget.last_seen_at.desc().nullslast())
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def active_targets_for_user(
    db: AsyncSession, *, user_id: UUID
) -> list[MobilePushTarget]:
    """Read the active push targets for a user — used by the dispatch task."""
    res = await db.execute(
        select(MobilePushTarget).where(
            MobilePushTarget.user_id == user_id,
            MobilePushTarget.is_active.is_(True),
        )
    )
    return list(res.scalars().all())


# ---------------------------------------------------------------------------
# Delivery (called by the Celery dispatch task)
# ---------------------------------------------------------------------------


async def dispatch(
    db: AsyncSession, *, user_id: UUID, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """Push ``payload`` to every active device owned by ``user_id``.

    Returns a list of per-device result dicts (``{device_id, platform,
    status, detail}``) for the delivery log. Never raises — a single
    device failure must not block delivery to the others. The dispatch
    task captures all exceptions and records them in the result.
    """
    targets = await active_targets_for_user(db, user_id=user_id)
    results: list[dict[str, Any]] = []
    for target in targets:
        try:
            if target.platform == "unifiedpush":
                await send_unifiedpush(target, payload)
            elif target.platform == "fcm":
                await send_fcm(target, payload)
            else:
                # Unknown platform — log and skip. Should not happen (the
                # register gate validates) but a future transport might land
                # before its sender is wired.
                results.append(
                    {
                        "device_id": target.device_id,
                        "platform": target.platform,
                        "status": "skipped",
                        "detail": f"Unsupported platform {target.platform}",
                    }
                )
                continue
            results.append(
                {
                    "device_id": target.device_id,
                    "platform": target.platform,
                    "status": "sent",
                    "detail": None,
                }
            )
        except Exception as exc:  # noqa: BLE001 — dispatch must not raise
            logger.warning(
                "Mobile push to device %s (%s) failed: %s",
                target.device_id,
                target.platform,
                exc,
            )
            results.append(
                {
                    "device_id": target.device_id,
                    "platform": target.platform,
                    "status": "failed",
                    "detail": str(exc),
                }
            )
    return results


async def send_unifiedpush(target: MobilePushTarget, payload: dict[str, Any]) -> None:
    """POST the notification payload to the user-chosen UnifiedPush
    distributor endpoint. The endpoint URL IS the auth (per UnifiedPush
    spec) — there is no separate bearer token.

    End-to-end encryption (RFC 9180 Hybrid Encryption) is applied when
    ``target.encryption_pubkey`` is set. The full crypto pipeline
    (HPKE seal against the client's pubkey) is deferred to a follow-up —
    v2 ships plaintext-over-HTTPS, which is acceptable because UnifiedPush
    distributors are typically self-hosted on a TLS endpoint the user
    controls. The ``encryption_pubkey`` column is reserved now so the
    upgrade doesn't need a migration.
    """
    async with httpx.AsyncClient(timeout=_PUSH_HTTP_TIMEOUT) as client:
        # UnifiedPush v1 spec: the body IS the message content; headers are
        # left to the app. We send a small JSON envelope so the receiver
        # (the mobile app's BroadcastReceiver) can decode uniformly across
        # notification kinds.
        resp = await client.post(
            target.endpoint_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            # 404 / 410 typically mean the endpoint was retired — the next
            # register will replace it. Don't auto-deactivate here; let the
            # client re-register.
            raise RuntimeError(
                f"UnifiedPush endpoint returned HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )


async def send_fcm(target: MobilePushTarget, payload: dict[str, Any]) -> None:
    """Send via Firebase Cloud Messaging v1.

    FCM v1 needs an OAuth2 service-account token minted from
    ``GOOGLE_APPLICATION_CREDENTIALS``. The full integration (credentials
    resolution + token refresh + the FCM v1 HTTP v1 projects/messages:send
    call) is gated on FCM being configured at the instance level — v2
    ships the FCM path disabled with a clear log so operators know what
    to enable. The implementation here is the canonical call shape.
    """
    # Deferred: when FCM creds land, this becomes a google-auth token mint
    # + a POST to https://fcm.googleapis.com/v1/projects/<proj>/messages:send
    # with the payload as ``message.token = target.endpoint_url``. Until then
    # the register side still accepts ``platform=fcm`` (so the client can opt
    # in), and the dispatch side surfaces a clear runtime message.
    raise RuntimeError(
        "FCM transport is not configured on this server. Set "
        "GOOGLE_APPLICATION_CREDENTIALS to enable FCM delivery."
    )
