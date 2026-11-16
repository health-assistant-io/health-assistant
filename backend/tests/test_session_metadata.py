"""Session device metadata, revoke-others and sign-in alerts (program 12C1).

The device list is a security control, so the facts behind it are part of
the contract: ``auth_sessions`` now carries ``ip`` / ``user_agent`` (as
observed at issuance) and ``last_seen_at`` (throttled activity stamp), the
list exposes them, ``POST /me/sessions/revoke-others`` signs out the other
devices **without** the ``token_version`` bump that would also kill the
caller, and a sign-in from an unseen device raises a ``SECURITY_EVENT``
notification (in-app + push, never email).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.auth_session_model import AuthSessionModel
from app.models.enums import (
    NotificationCategory,
    NotificationType,
    Role,
)
from app.models.notification import Notification, NotificationRecipient
from app.models.user_model import UserModel
from tests._auth_helpers import auth_headers, create_user

PASSWORD = "correct-password-123"
DEVICE_A = "HealthClient/1.0 (linux; desktop)"
DEVICE_B = "HealthClient/1.0 (android; phone)"


class FakeRedis:
    """Dict-backed stand-in exposing the ``SET NX EX`` throttle surface."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def get(self, key):
        return self.store.get(key)

    async def getdel(self, key):
        return self.store.pop(key, None)

    async def delete(self, *keys):
        return sum(1 for key in keys if self.store.pop(key, None) is not None)

    async def exists(self, *keys):
        return sum(1 for key in keys if key in self.store)

    async def incr(self, key):
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key, ttl):
        return True

    async def scan_iter(self, pattern):
        import fnmatch

        for key in list(self.store):
            if fnmatch.fnmatch(key, pattern):
                yield key


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.core.token_store.redis_client", fake)
    monkeypatch.setattr("app.core.rate_limit.redis_client", fake)
    return fake


@pytest_asyncio.fixture
async def device_user():
    return await create_user(role=Role.USER, password=PASSWORD)


def _client_at(ip: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 4321)), base_url="http://test")


async def _login(client: AsyncClient, user, *, user_agent: str = DEVICE_A):
    return await client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": PASSWORD},
        headers={"User-Agent": user_agent},
    )


async def _last_seen(family_id):
    async with AsyncSessionLocal() as session:
        row = await session.get(AuthSessionModel, family_id)
        return row.last_seen_at


async def _families(user_id):
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AuthSessionModel)
                    .where(AuthSessionModel.user_id == user_id)
                    .where(AuthSessionModel.revoked_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            session.expunge(row)
        return rows


# --- device facts -----------------------------------------------------------


@pytest.mark.asyncio
async def test_login_stores_ip_user_agent_and_last_seen(async_client: AsyncClient, device_user):
    response = await _login(async_client, device_user)
    assert response.status_code == 200, response.text

    families = await _families(device_user.id)
    assert len(families) == 1
    row = families[0]
    assert row.ip == "127.0.0.1"
    assert row.user_agent == DEVICE_A
    assert row.client_label == DEVICE_A
    assert row.last_seen_at is not None


@pytest.mark.asyncio
async def test_an_overlong_user_agent_is_truncated_not_fatal(
    async_client: AsyncClient, device_user
):
    """B9 — ``user_agent`` is ``String(512)``: a long header must not fail the
    insert (the sign-in is legitimate; the stored fact is bounded)."""
    long_agent = "HealthClient/9.9 " + ("x" * 2000)
    response = await _login(async_client, device_user, user_agent=long_agent)
    assert response.status_code == 200, response.text

    row = (await _families(device_user.id))[0]
    assert row.user_agent is not None
    assert len(row.user_agent) == 512
    assert row.user_agent == long_agent[:512]


@pytest.mark.asyncio
async def test_device_list_exposes_the_device_facts(async_client: AsyncClient, device_user):
    assert (await _login(async_client, device_user, user_agent=DEVICE_A)).status_code == 200
    assert (await _login(async_client, device_user, user_agent=DEVICE_B)).status_code == 200
    headers = await auth_headers(device_user)
    async_client.cookies.clear()

    response = await async_client.get("/api/v1/me/sessions", headers=headers)

    assert response.status_code == 200, response.text
    rows = response.json()
    assert {row["user_agent"] for row in rows} == {DEVICE_A, DEVICE_B}
    assert all(row["ip"] == "127.0.0.1" for row in rows)
    assert all(row["last_seen_at"] for row in rows)


@pytest.mark.asyncio
async def test_last_seen_is_throttled_per_family(async_client: AsyncClient, device_user):
    login = await _login(async_client, device_user)
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    async_client.cookies.clear()
    family_id = uuid.UUID(str((await _families(device_user.id))[0].id))
    old = datetime.now(UTC) - timedelta(hours=5)
    async with AsyncSessionLocal() as session:
        await session.execute(
            AuthSessionModel.__table__.update()
            .where(AuthSessionModel.id == family_id)
            .values(last_seen_at=old)
        )
        await session.commit()

    assert (await async_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    first = await _last_seen(family_id)
    assert first is not None and first > old

    assert (await async_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    second = await _last_seen(family_id)
    assert second == first


# --- revoke-others ----------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_others_keeps_the_caller_and_kills_the_rest(
    async_client: AsyncClient, device_user
):
    laptop = await _login(async_client, device_user, user_agent=DEVICE_A)
    phone = await _login(async_client, device_user, user_agent=DEVICE_B)
    laptop_headers = {"Authorization": f"Bearer {laptop.json()['access_token']}"}
    phone_headers = {"Authorization": f"Bearer {phone.json()['access_token']}"}
    async_client.cookies.clear()
    version_before = (await _refresh_user(device_user.id)).token_version

    response = await async_client.post("/api/v1/me/sessions/revoke-others", headers=laptop_headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"revoked": 1}
    assert (await async_client.get("/api/v1/auth/me", headers=laptop_headers)).status_code == 200
    assert (await async_client.get("/api/v1/auth/me", headers=phone_headers)).status_code == 401
    leftovers = await _families(device_user.id)
    assert [row.user_agent for row in leftovers] == [DEVICE_A]
    # No token_version bump: the caller's own bearer (and any other
    # credential of this user outside a family) is untouched.
    assert (await _refresh_user(device_user.id)).token_version == version_before


@pytest.mark.asyncio
async def test_revoke_others_is_audited(async_client: AsyncClient, device_user):
    laptop = await _login(async_client, device_user, user_agent=DEVICE_A)
    await _login(async_client, device_user, user_agent=DEVICE_B)
    headers = {"Authorization": f"Bearer {laptop.json()['access_token']}"}
    async_client.cookies.clear()

    await async_client.post("/api/v1/me/sessions/revoke-others", headers=headers)

    from app.models.audit_model import AuditEvent

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(AuditEvent.action).where(AuditEvent.user_id == device_user.id)
        )
        assert "session.revoke_others" in set(rows.scalars().all())


# --- sign-in alerts ---------------------------------------------------------


async def _refresh_user(user_id) -> UserModel:
    async with AsyncSessionLocal() as session:
        user = await session.get(UserModel, user_id)
        session.expunge(user)
        return user


async def _security_events(user_id) -> list[Notification]:
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Notification)
                    .join(
                        NotificationRecipient,
                        NotificationRecipient.notification_id == Notification.id,
                    )
                    .where(NotificationRecipient.user_id == user_id)
                    .where(Notification.type == NotificationType.SECURITY_EVENT)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            session.expunge(row)
        return rows


@pytest.mark.asyncio
async def test_login_from_a_new_device_emits_a_security_alert(
    async_client: AsyncClient, device_user
):
    assert (await _login(async_client, device_user)).status_code == 200

    events = await _security_events(device_user.id)
    assert len(events) == 1
    event = events[0]
    assert event.category == NotificationCategory.SECURITY
    assert event.tenant_id == device_user.tenant_id
    assert "New sign-in" in event.title
    assert event.source_ref["ip"] == "127.0.0.1"
    assert "password" in event.body


@pytest.mark.asyncio
async def test_a_known_device_does_not_alert_again(async_client: AsyncClient, device_user):
    assert (await _login(async_client, device_user)).status_code == 200
    assert (await _login(async_client, device_user)).status_code == 200

    assert len(await _security_events(device_user.id)) == 1


@pytest.mark.asyncio
async def test_a_new_ip_alerts_even_on_a_known_device_agent(async_client: AsyncClient, device_user):
    assert (await _login(async_client, device_user)).status_code == 200
    other = _client_at("10.9.8.7")
    async with other:
        response = await _login(other, device_user)
    assert response.status_code == 200, response.text

    assert len(await _security_events(device_user.id)) == 2


@pytest.mark.asyncio
async def test_the_source_mute_never_silences_security_alerts(
    async_client: AsyncClient, device_user
):
    """Safety-critical types bypass the source-level mute (channel prefs stand)."""
    async with AsyncSessionLocal() as session:
        user = await session.get(UserModel, device_user.id)
        settings = dict(user.settings or {})
        settings["notifications.sources.SYSTEM"] = False
        user.settings = settings
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(user, "settings")
        await session.commit()

    assert (await _login(async_client, device_user)).status_code == 200

    assert len(await _security_events(device_user.id)) == 1


@pytest.mark.asyncio
async def test_alert_timestamp_is_recent(async_client: AsyncClient, device_user):
    started = int(time.time())
    assert (await _login(async_client, device_user)).status_code == 200
    events = await _security_events(device_user.id)
    assert events[0].created_at.timestamp() >= started - 5
