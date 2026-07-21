"""Tests for the notification digest collapsing (item 4 of the
integrations-sdk-improvements plan).

Pin the ``emit(dedup_key=..., dedup_ttl_seconds=...)`` contract:

* Two emits with the same key inside the TTL window → one row.
* After TTL expires → second emit creates a new row.
* Race window ``IntegrityError`` → rollback + re-fetch.
* No ``dedup_key`` → behaviour unchanged.
* TTL is clamped to a sane floor/ceiling.
* NotificationSpec carries ``digest_key`` and the builder exposes it.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.converters import utcnow
from app.core.database import AsyncSessionLocal
from app.models.enums import (
    NotificationCategory,
    NotificationSeverity,
    NotificationSource,
    NotificationType,
    RecipientKind,
)
from app.models.notification import Notification
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.notification_service import (
    _compute_dedup_expires_at,
    _DIGEST_TTL_CEILING_SECONDS,
    _DIGEST_TTL_FLOOR_SECONDS,
    _resolve_digest_ttl,
    emit,
)


@pytest_asyncio.fixture
async def tenant_and_user():
    """Isolated tenant + user for emit() tests.

    Returns ``(tenant_id, user_id)``.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name="Notif Dedup T.",
                slug=f"notifdedup-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"notifdedup-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.commit()

    return tenant_id, user_id


async def _emit_one(
    tenant_id,
    user_id,
    *,
    dedup_key=None,
    dedup_ttl_seconds=None,
    title="Test notif",
):
    """Convenience wrapper that calls ``emit`` with a default shape."""
    return await emit(
        source=NotificationSource.INTEGRATION,
        type=NotificationType.INTEGRATION_EVENT,
        category=NotificationCategory.ALERT,
        severity=NotificationSeverity.WARNING,
        title=title,
        body="body",
        tenant_id=tenant_id,
        targets=[{"kind": RecipientKind.USER.value, "id": str(user_id)}],
        sender_user_id=user_id,
        dedup_key=dedup_key,
        dedup_ttl_seconds=dedup_ttl_seconds,
    )


# ---------------------------------------------------------------------------
# TTL helper contract
# ---------------------------------------------------------------------------


def test_resolve_digest_ttl_uses_default_when_none():
    """``None`` falls back to the platform default (6h)."""
    assert _resolve_digest_ttl(None) == 21600


def test_resolve_digest_ttl_applies_floor():
    """Tiny TTLs are bumped to the 60s floor."""
    assert _resolve_digest_ttl(5) == _DIGEST_TTL_FLOOR_SECONDS
    assert _resolve_digest_ttl(1) == _DIGEST_TTL_FLOOR_SECONDS


def test_resolve_digest_ttl_applies_ceiling():
    """Absurd TTLs are capped at 7 days."""
    assert _resolve_digest_ttl(86400 * 30) == _DIGEST_TTL_CEILING_SECONDS


def test_resolve_digest_ttl_zero_or_negative_returns_none():
    """``0`` / negative values signal 'no digestion'."""
    assert _resolve_digest_ttl(0) is None
    assert _resolve_digest_ttl(-10) is None


def test_resolve_digest_ttl_passes_through_in_range():
    assert _resolve_digest_ttl(120) == 120
    assert _resolve_digest_ttl(3600) == 3600


def test_compute_dedup_expires_at_none_returns_none():
    assert _compute_dedup_expires_at(None) is None


def test_compute_dedup_expires_at_returns_future():
    expires = _compute_dedup_expires_at(60)
    assert expires is not None
    assert expires > utcnow()


# ---------------------------------------------------------------------------
# emit() dedup behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_collapses_duplicate_within_ttl_window(tenant_and_user):
    """Two emits with the same dedup_key inside the TTL window return
    the SAME row id (no new Notification inserted)."""
    tenant_id, user_id = tenant_and_user
    key = f"test:hr:{uuid.uuid4()}"

    first = await _emit_one(tenant_id, user_id, dedup_key=key, dedup_ttl_seconds=300)
    second = await _emit_one(tenant_id, user_id, dedup_key=key, dedup_ttl_seconds=300)

    assert first is not None and second is not None
    assert second.id == first.id, "dedup must return the same row id"

    # Only one row exists for this key.
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Notification).where(Notification.dedup_key == key)
            )
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_emit_creates_new_row_after_ttl_expires(tenant_and_user, monkeypatch):
    """When the dedup_expires_at is in the past, a new emit creates a
    fresh row."""
    # Patch the logger so the test fails LOUDLY instead of silently
    # returning None — emit() catches Exception and logs.exception it.
    # Without this patch, a regression would surface as "second is None".
    import logging

    class _RaisingHandler(logging.Handler):
        def emit(self, record):
            if record.exc_info:
                raise record.exc_info[1]

    handler = _RaisingHandler(level=logging.ERROR)
    logger = logging.getLogger("app.services.notification_service")
    logger.addHandler(handler)
    try:
        tenant_id, user_id = tenant_and_user
        key = f"test:expired:{uuid.uuid4()}"

        first = await _emit_one(
            tenant_id, user_id, dedup_key=key, dedup_ttl_seconds=300
        )
        assert first is not None

        # Manually expire the first row.
        async with AsyncSessionLocal() as db:
            first_row = await db.get(Notification, first.id)
            first_row.dedup_expires_at = utcnow() - timedelta(seconds=1)
            await db.commit()

        second = await _emit_one(
            tenant_id, user_id, dedup_key=key, dedup_ttl_seconds=300
        )
        assert second is not None
        assert second.id != first.id, "expired dedup must create a new row"
    finally:
        logger.removeHandler(handler)


@pytest.mark.asyncio
async def test_emit_without_dedup_key_unaffected(tenant_and_user):
    """No ``dedup_key`` → two emits create two distinct rows (the
    pre-item-4 behaviour)."""
    tenant_id, user_id = tenant_and_user

    first = await _emit_one(tenant_id, user_id, title="A")
    second = await _emit_one(tenant_id, user_id, title="B")

    assert first is not None and second is not None
    assert first.id != second.id


@pytest.mark.asyncio
async def test_emit_dedup_key_is_stored_on_row(tenant_and_user):
    """The row's dedup_key + dedup_expires_at columns are populated."""
    tenant_id, user_id = tenant_and_user
    key = f"test:store:{uuid.uuid4()}"

    notif = await _emit_one(
        tenant_id, user_id, dedup_key=key, dedup_ttl_seconds=120
    )
    assert notif is not None

    async with AsyncSessionLocal() as db:
        row = await db.get(Notification, notif.id)
        assert row.dedup_key == key
        assert row.dedup_expires_at is not None
        assert row.dedup_expires_at > utcnow()


@pytest.mark.asyncio
async def test_emit_dedup_is_best_effort_no_unique_constraint():
    """Sanity guard: there must be NO ``uq_notification_dedup`` unique
    index on the notifications table. Unlike the document / examination
    dedup paths, the notification digest is best-effort (the TTL
    semantics require multiple rows with the same key over time, which
    a unique index can't express — Postgres partial indexes can't
    reference now() in the predicate). The lookup-then-insert in
    emit() handles the common case; the race window is benign."""
    from sqlalchemy import create_engine, text as sa_text

    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = list(
                conn.execute(
                    sa_text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename='notifications' "
                        "AND indexname='uq_notification_dedup'"
                    )
                )
            )
    finally:
        engine.dispose()
    assert rows == [], (
        "uq_notification_dedup must NOT exist — TTL semantics require "
        "multiple rows with the same key over time. Use a non-unique "
        "lookup index instead."
    )


# ---------------------------------------------------------------------------
# NotificationSpec (SDK) digest_key
# ---------------------------------------------------------------------------


def test_notification_spec_supports_digest_key():
    """``NotificationSpec`` carries the optional digest_key field."""
    from integrations.sdk import NotificationSpec

    spec = NotificationSpec(title="x")
    assert spec.digest_key is None  # default

    spec2 = NotificationSpec(title="x", digest_key="domain:type:scope")
    assert spec2.digest_key == "domain:type:scope"


def test_notification_spec_builder_digest_key():
    """The fluent builder exposes ``.digest_key(key)``."""
    from integrations.sdk import NotificationSpec

    spec = (
        NotificationSpec.builder(title="x")
        .digest_key("dev_dummy:hr:patient/abc")
        .build()
    )
    assert spec.digest_key == "dev_dummy:hr:patient/abc"


def test_notification_to_payload_does_not_leak_digest_key():
    """``digest_key`` is platform routing metadata, not user-visible
    payload content. ``to_payload()`` must not include it (the engine
    forwards it as a kwarg to ``emit``, not via the payload dict)."""
    from integrations.sdk import NotificationSpec

    spec = NotificationSpec(title="x", digest_key="some:key")
    payload = spec.to_payload()
    assert "digest_key" not in payload
