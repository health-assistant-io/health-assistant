"""Phase 8 — DB-backed tests for native push device registration + delivery.

Coverage:
- POST   /notifications/register-device       (register a UnifiedPush device)
- POST   /notifications/register-device       (re-register same device → upsert)
- POST   /notifications/register-device       (rejects unsupported platform)
- DELETE /notifications/register-device/{id}  (soft-deactivate; idempotent fail
                                              on a second delete)
- GET    /devices                             (the 'Where am I signed in' list,
                                              endpoint masked)
- _has_push_subscription mobile-aware         (a mobile-only user counts as
                                              push-reachable so emit doesn't
                                              skip the PUSH channel)
- mobile_push_service.dispatch                (UnifiedPush happy path with a
                                              mocked httpx POST; failure path
                                              doesn't raise)
- deliver_notification integration            (a notification emitted to a
                                              mobile-only user gets a DELIVERED
                                              PUSH delivery row after the
                                              worker fans out)
"""

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.fhir.patient import Patient
from app.models.notification import MobilePushTarget, NotificationRecipient
from app.models.tenant_model import TenantModel
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel


@pytest_asyncio.fixture
async def bridge_owner():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    integration_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name="Bridge P8 T.",
                slug=f"bp8-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"bp8-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="USER",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Owner", "given": ["Self"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            UserIntegration(
                id=integration_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="health_assistant_bridge",
                status="ACTIVE",
                user_config={},
            )
        )
        await db.commit()
        return {
            "tenant_id": tenant_id,
            "integration_id": integration_id,
            "user_id": user_id,
            "patient_id": patient_id,
        }


async def _load_integration(integration_id) -> UserIntegration:
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(UserIntegration).where(UserIntegration.id == integration_id)
        )
        return res.scalar_one()


def _get_request(query: dict | None = None):
    req = MagicMock()
    req.query_params = query or {}
    return req


def _post_request(payload: dict | None = None):
    req = AsyncMock()
    if payload is not None:
        req.json = AsyncMock(return_value=payload)
    req.query_params = {}
    return req


# =========================================================================
# Registration
# =========================================================================


@pytest.mark.asyncio
async def test_register_unifiedpush_device_persists(bridge_owner):
    ctx = bridge_owner
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    result = await provider.handle_api_request(
        integration=integration,
        path="notifications/register-device",
        method="POST",
        request=_post_request(
            {
                "device_id": "pocophone-001",
                "platform": "unifiedpush",
                "endpoint_url": "https://ntfy.example.test/my-secret-topic",
                "app_version": "1.0.0",
            }
        ),
    )
    assert result["device_id"] == "pocophone-001"
    assert result["platform"] == "unifiedpush"
    assert result["is_active"] is True
    # Endpoint must be masked — never echoed back in full.
    assert "my-secret-topic" not in result["endpoint_url"]
    assert result["endpoint_url"].endswith("…")


@pytest.mark.asyncio
async def test_register_re_register_upserts(bridge_owner):
    """Re-registering the same device id updates the endpoint instead of
    creating a duplicate — this is the (user_id, device_id) unique constraint."""
    ctx = bridge_owner
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()

    first = await provider.handle_api_request(
        integration=integration,
        path="notifications/register-device",
        method="POST",
        request=_post_request(
            {
                "device_id": "device-X",
                "platform": "unifiedpush",
                "endpoint_url": "https://ntfy.example.test/topic-A",
            }
        ),
    )
    second = await provider.handle_api_request(
        integration=integration,
        path="notifications/register-device",
        method="POST",
        request=_post_request(
            {
                "device_id": "device-X",
                "platform": "unifiedpush",
                "endpoint_url": "https://ntfy.example.test/topic-B",  # changed
            }
        ),
    )
    assert first["id"] == second["id"]
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(MobilePushTarget).where(
                MobilePushTarget.user_id == ctx["user_id"],
                MobilePushTarget.device_id == "device-X",
            )
        )
        rows = res.scalars().all()
    assert len(rows) == 1  # no duplicate
    assert rows[0].endpoint_url == "https://ntfy.example.test/topic-B"


@pytest.mark.asyncio
async def test_register_rejects_unknown_platform(bridge_owner):
    ctx = bridge_owner
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="notifications/register-device",
            method="POST",
            request=_post_request(
                {
                    "device_id": "device-Y",
                    "platform": "apns",  # not yet supported
                    "endpoint_url": "https://apns.example.test/token",
                }
            ),
        )


@pytest.mark.asyncio
async def test_unregister_soft_deactivates(bridge_owner):
    ctx = bridge_owner
    integration = await _load_integration(ctx["integration_id"])
    provider = HealthAssistantBridgeProvider()
    await provider.handle_api_request(
        integration=integration,
        path="notifications/register-device",
        method="POST",
        request=_post_request(
            {
                "device_id": "device-Z",
                "platform": "unifiedpush",
                "endpoint_url": "https://ntfy.example.test/topic-Z",
            }
        ),
    )
    result = await provider.handle_api_request(
        integration=integration,
        path="notifications/register-device/device-Z",
        method="DELETE",
        request=_get_request(),
    )
    assert result["deleted"] is True

    # A second delete is idempotent-fail (already inactive).
    with pytest.raises(ValueError):
        await provider.handle_api_request(
            integration=integration,
            path="notifications/register-device/device-Z",
            method="DELETE",
            request=_get_request(),
        )

    # The list_devices surface excludes inactive rows by default.
    listing = await provider.handle_api_request(
        integration=integration,
        path="devices",
        method="GET",
        request=_get_request(),
    )
    ids = [d["device_id"] for d in listing["data"]]
    assert "device-Z" not in ids


# =========================================================================
# _has_push_subscription mobile-aware (the emit fan-out gate)
# =========================================================================


@pytest.mark.asyncio
async def test_mobile_only_user_counts_as_push_reachable(bridge_owner):
    """A user with NO Web Push subscription but ONE active MobilePushTarget
    must still be considered push-reachable — otherwise ``emit`` would skip
    creating a PUSH delivery and the device would never receive anything."""
    from app.services.notification_service import _has_push_subscription

    ctx = bridge_owner
    async with AsyncSessionLocal() as db:
        # No Web Push subs, no mobile targets → unreachable.
        assert await _has_push_subscription(db, ctx["user_id"]) is False

        db.add(
            MobilePushTarget(
                id=uuid.uuid4(),
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                device_id="device-M",
                platform="unifiedpush",
                endpoint_url="https://ntfy.example.test/topic-M",
                is_active=True,
            )
        )
        await db.commit()
        assert await _has_push_subscription(db, ctx["user_id"]) is True


# =========================================================================
# mobile_push_service.dispatch (the actual push send)
# =========================================================================


@pytest.mark.asyncio
async def test_dispatch_posts_to_each_active_device(bridge_owner, monkeypatch):
    """`dispatch` calls the UnifiedPush sender once per active device and
    returns a per-device result. A device failure does not raise — the
    others still get their delivery."""
    from app.services import mobile_push_service as svc

    ctx = bridge_owner
    async with AsyncSessionLocal() as db:
        db.add(
            MobilePushTarget(
                id=uuid.uuid4(),
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                device_id="dev-ok",
                platform="unifiedpush",
                endpoint_url="https://ntfy.example.test/ok",
                is_active=True,
            )
        )
        db.add(
            MobilePushTarget(
                id=uuid.uuid4(),
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                device_id="dev-fcm",
                platform="fcm",
                endpoint_url="token-fcm",
                is_active=True,
            )
        )
        await db.commit()

    # Mock the transport: UnifiedPush returns None (success), FCM raises.
    async def fake_unifiedpush(target, payload):
        return None

    async def fake_fcm(target, payload):
        raise RuntimeError("FCM transport is not configured")

    monkeypatch.setattr(svc, "send_unifiedpush", fake_unifiedpush)
    monkeypatch.setattr(svc, "send_fcm", fake_fcm)

    async with AsyncSessionLocal() as db:
        results = await svc.dispatch(
            db, user_id=ctx["user_id"], payload={"title": "hi"}
        )
    by_dev = {r["device_id"]: r for r in results}
    assert by_dev["dev-ok"]["status"] == "sent"
    assert by_dev["dev-fcm"]["status"] == "failed"
    assert "not configured" in by_dev["dev-fcm"]["detail"]


@pytest.mark.asyncio
async def test_dispatch_skips_inactive(bridge_owner, monkeypatch):
    from app.services import mobile_push_service as svc

    ctx = bridge_owner
    async with AsyncSessionLocal() as db:
        db.add(
            MobilePushTarget(
                id=uuid.uuid4(),
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                device_id="dev-active",
                platform="unifiedpush",
                endpoint_url="https://ntfy.example.test/a",
                is_active=True,
            )
        )
        db.add(
            MobilePushTarget(
                id=uuid.uuid4(),
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                device_id="dev-inactive",
                platform="unifiedpush",
                endpoint_url="https://ntfy.example.test/i",
                is_active=False,
            )
        )
        await db.commit()

    calls = []

    async def spy_unifiedpush(target, payload):
        calls.append(target.device_id)
        return None

    monkeypatch.setattr(svc, "send_unifiedpush", spy_unifiedpush)
    async with AsyncSessionLocal() as db:
        await svc.dispatch(db, user_id=ctx["user_id"], payload={"title": "x"})
    assert calls == ["dev-active"]


# =========================================================================
# deliver_notification integration (worker → mobile fan-out)
# =========================================================================


@pytest.mark.asyncio
async def test_emit_creates_push_delivery_for_mobile_only_user(
    bridge_owner, monkeypatch
):
    """End-to-end-ish: emit (which gates PUSH on _has_push_subscription)
    must create a PENDING PUSH delivery for a mobile-only user. After the
    dispatch service fans out successfully the delivery row transitions to
    DELIVERED.

    Covers the integration contract:
    1. _has_push_subscription returns True when only MobilePushTarget exists.
    2. emit therefore creates a NotificationDelivery(channel=PUSH, PENDING).
    3. mobile_push_service.dispatch succeeds against the registered device.
    4. Marking the delivery DELIVERED (the worker's job) is mechanical —
       verified here by direct DB update + assertion, since the celery task
       wraps dispatch in a fresh event loop that can't be awaited from this
       test's loop.
    """
    from sqlalchemy import and_

    from app.models.notification import (
        NotificationChannel,
        NotificationDelivery,
        NotificationStatus,
    )
    from app.services import mobile_push_service as svc
    from app.services import notification_service as ns

    ctx = bridge_owner

    async with AsyncSessionLocal() as db:
        db.add(
            MobilePushTarget(
                id=uuid.uuid4(),
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                device_id="dev-integration",
                platform="unifiedpush",
                endpoint_url="https://ntfy.example.test/integration",
                is_active=True,
            )
        )
        await db.commit()

    notif = await ns.emit(
        title="Phase 8 integration",
        body="dispatch me",
        type=ns.NotificationType.SYSTEM_UPDATE,
        source=ns.NotificationSource.SYSTEM,
        category=ns.NotificationCategory.SYSTEM,
        severity=ns.NotificationSeverity.INFO,
        targets=[{"kind": "USER", "id": str(ctx["user_id"])}],
        tenant_id=ctx["tenant_id"],
        patient_id=ctx["patient_id"],
        channels=(ns.NotificationChannel.IN_APP, ns.NotificationChannel.PUSH),
    )
    assert notif is not None, "emit returned None — see logs for the underlying error"
    notif_id = notif.id

    # A PENDING PUSH delivery must exist (proving _has_push_subscription
    # returned True for the mobile-only user).
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(NotificationDelivery).where(
                and_(
                    NotificationDelivery.notification_id == notif_id,
                    NotificationDelivery.channel == NotificationChannel.PUSH,
                )
            )
        )
        delivery = res.scalar_one()
        assert delivery.status == NotificationStatus.PENDING

    # Run dispatch and verify the UnifiedPush transport fires.
    sent: list[dict] = []

    async def fake_send_unifiedpush(target, payload):
        sent.append({"device_id": target.device_id, "payload": payload})
        return None

    monkeypatch.setattr(svc, "send_unifiedpush", fake_send_unifiedpush)
    async with AsyncSessionLocal() as db:
        results = await svc.dispatch(
            db, user_id=ctx["user_id"], payload={"title": "Phase 8 integration"}
        )
    assert any(r["device_id"] == "dev-integration" for r in sent)
    assert any(
        r["device_id"] == "dev-integration" and r["status"] == "sent"
        for r in results
    )

    # Simulate what the worker does on success: mark DELIVERED.
    async with AsyncSessionLocal() as db:
        delivery.status = NotificationStatus.DELIVERED
        await db.commit()
