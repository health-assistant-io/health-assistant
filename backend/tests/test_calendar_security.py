"""The plan-52 §10/§11 P2 security wall (ADR-0052, Rev 3 findings).

Each test pins one review finding at the seam where it is enforced:

* **A (bind-CSRF)** — the OAuth ``state`` is bound to the authenticated
  principal that started the flow, single-use: a state issued to another
  principal is refused (403) and burned; replay is refused (400).
* **B (token leak through error detail)** — a private ICS feed's URL
  carries a bearer token in its query string; that token never appears in
  ``last_error``, an audit row, a log line, an API response — nor in the
  binding row itself (C4 custody: the full URL lives only in the sealed
  slot, the row keeps scheme+host+path).
* **C (consent notice keyed to the chosen auth kind)** — a ``token``
  ICS bind must never see "needs no credential".
* **D (inbound is untrusted)** — pulled events become ``origin='calendar'``
  records marked ``unverified``; foreign events are pulled only through the
  explicit ``acceptCategories``/``acceptAll`` options; dedup rides the
  calendar partial unique index; confirm/dismiss is the Tier-1 surface.
* **E (one writer per remote calendar)** — a second writable binding over
  the same calendar is refused (409 + unique partial index).
* **G (SSRF / polite runtime)** — a user-supplied internal address is
  refused by the guard before any connection; a redirect into a private
  address fails per-hop.
* **H (refresh-failure posture)** — ``invalid_grant`` ⇒ ``needs-auth``
  (stop, notify once); ``403 insufficient_scope`` ⇒ ``needs-secret``;
  transient ⇒ ``degraded`` → ``failing`` with bounded backoff.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.extensions.cloud.cloud_calendar import CalendarError, CalendarEvent
from app.extensions.errors import ExtensionError
from app.extensions.sdk.auth import OAuthStateStore
from app.main import app
from app.models.calendar_binding import CalendarBinding
from app.models.clinical_event import ClinicalEvent
from app.models.enums import Role
from app.services.calendar import credentials as creds
from app.services.calendar import sync as calendar_sync
from app.services.calendar.sanitize import sanitize_error_detail, sanitize_url
from tests._auth_helpers import auth_headers, create_user
from tests.test_calendar_sync import FakeTransport, _make_world, _seed_oauth_slot

SECRET_FEED_TOKEN = "feedtoken-SUPERSECRET"


@pytest.fixture
def data_key(monkeypatch):
    settings = get_settings()
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "HA_DATA_KEY", key)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
    return key


@pytest.fixture
def fake_state_store(monkeypatch):
    """Swap the endpoint's OAuthStateStore for an in-memory fake."""

    class _FakeRedis:
        def __init__(self) -> None:
            self._store: dict[str, str] = {}

        async def set(self, key, value, ex=None, nx=False):
            self._store[key] = value
            return True

        async def execute_command(self, command, key):
            # GETDEL — the one-shot consume.
            return self._store.pop(key, None)

        async def get(self, key):
            return self._store.get(key)

        async def delete(self, key):
            return self._store.pop(key, None)

    fake = _FakeRedis()
    monkeypatch.setattr(
        "app.api.v1.endpoints.calendar.OAuthStateStore",
        lambda: OAuthStateStore(redis_client=fake),
    )
    return fake


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def owner_env():
    user = await create_user(role=Role.USER, full_name="Cal Owner")
    return user, await auth_headers(user)


@pytest_asyncio.fixture
async def attacker_env():
    user = await create_user(role=Role.USER, full_name="Cal Attacker")
    return user, await auth_headers(user)


async def _api_binding(client, headers, patient_id, **overrides) -> dict:
    payload = {
        "provider": "google",
        "patient_id": str(patient_id),
        "direction": "push",
        "redaction": "neutral",
    }
    payload.update(overrides)
    response = await client.post("/api/v1/calendar/bindings", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _patient_id_for(user) -> uuid.UUID:
    from app.models.fhir.patient import Patient

    pid = uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=pid,
                tenant_id=user.tenant_id,
                name={"family": "Test", "given": ["Sec"]},
                gender="UNKNOWN",
            )
        )
        await db.commit()
    return pid


async def _seed_patient_world() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(tenant_id, user_id, patient_id) — real rows, for direct-ORM tests."""
    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.models.user_model import UserModel

    tenant_id, user_id, patient_id = uuid4(), uuid4(), uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Sec T.", slug=f"sec-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"sec-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role=Role.USER.value,
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["Sec"]},
                gender="UNKNOWN",
            )
        )
        await db.commit()
    return tenant_id, user_id, patient_id


# ---------------------------------------------------------------------------
# A — bind-CSRF: the state is bound to the principal + single-use
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_callback_refuses_a_foreign_principal(
    client, owner_env, attacker_env, fake_state_store, monkeypatch
):
    """The attack: the attacker completes THEIR authorization code against
    the victim's binding (pointing the victim's clinical calendar at the
    attacker's account). The state belongs to the victim → refused, burned."""
    owner, owner_headers = owner_env
    _attacker, attacker_headers = attacker_env
    monkeypatch.setenv("CALENDAR_OAUTH_CLIENT_IDS", '{"google": "client-123"}')
    patient_id = await _patient_id_for(owner)
    binding = await _api_binding(client, owner_headers, patient_id)

    started = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/oauth/start", headers=owner_headers
    )
    assert started.status_code == 200, started.text
    state = started.json()["state"]

    # The attacker's browser (their session) presents the stolen state+code.
    refused = await client.get(
        f"/api/v1/calendar/oauth/callback?state={state}&code=attacker-code",
        headers=attacker_headers,
    )
    assert refused.status_code == 403
    # The state is consumed (one-shot): even the rightful principal cannot
    # complete the burned flow — the attacker's attempt destroyed it, it
    # can never attach the attacker's code to the victim's binding.
    replay = await client.get(
        f"/api/v1/calendar/oauth/callback?state={state}&code=attacker-code",
        headers=owner_headers,
    )
    assert replay.status_code == 400


@pytest.mark.asyncio
async def test_oauth_callback_completes_only_for_the_starting_principal(
    client, owner_env, fake_state_store, monkeypatch
):
    """The honest flow: the same signed-in principal completes, the code is
    exchanged, the token set is sealed — and the state cannot be replayed."""
    owner, owner_headers = owner_env
    monkeypatch.setenv("CALENDAR_OAUTH_CLIENT_IDS", '{"google": "client-123"}')
    patient_id = await _patient_id_for(owner)
    binding = await _api_binding(client, owner_headers, patient_id)

    started = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/oauth/start", headers=owner_headers
    )
    state = started.json()["state"]

    async def fake_exchange(
        token_url, code, verifier, redirect_uri, client_id, *, client_secret=None, http
    ):
        assert code == "good-code"
        return {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600}

    monkeypatch.setattr(
        "app.api.v1.endpoints.calendar.exchange_code", fake_exchange
    )
    completed = await client.get(
        f"/api/v1/calendar/oauth/callback?state={state}&code=good-code",
        headers=owner_headers,
    )
    assert completed.status_code == 302
    assert "status=connected" in completed.headers["location"]

    # one-shot: a replayed callback is dead
    replay = await client.get(
        f"/api/v1/calendar/oauth/callback?state={state}&code=good-code",
        headers=owner_headers,
    )
    assert replay.status_code == 400

    async with AsyncSessionLocal() as db:
        cred = (
            await db.execute(
                text(
                    "SELECT kind FROM calendar_binding_credentials "
                    "WHERE binding_id = CAST(:bid AS uuid)"
                )
                .bindparams(bid=binding["id"])
            )
        ).scalar_one()
        assert cred == "oauth"


@pytest.mark.asyncio
async def test_oauth_callback_sends_the_deployment_client_secret(
    client, owner_env, fake_state_store, monkeypatch
):
    """A confidential *web application* client (Google's type, which a
    server-side callback redirect forces) needs its secret at the token
    endpoint. It is deployment config — resolved from the environment at
    exchange time, never written into the binding slot."""
    owner, owner_headers = owner_env
    monkeypatch.setenv("CALENDAR_OAUTH_CLIENT_IDS", '{"google": "client-123"}')
    monkeypatch.setenv(
        "CALENDAR_OAUTH_CLIENT_SECRETS", '{"google": "web-app-secret-1"}'
    )
    patient_id = await _patient_id_for(owner)
    binding = await _api_binding(client, owner_headers, patient_id)
    started = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/oauth/start", headers=owner_headers
    )
    state = started.json()["state"]

    captured: dict[str, str | None] = {}

    async def fake_exchange(
        token_url, code, verifier, redirect_uri, client_id, *, client_secret=None, http
    ):
        captured["client_secret"] = client_secret
        return {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600}

    monkeypatch.setattr("app.api.v1.endpoints.calendar.exchange_code", fake_exchange)
    completed = await client.get(
        f"/api/v1/calendar/oauth/callback?state={state}&code=good-code",
        headers=owner_headers,
    )
    assert completed.status_code == 302
    assert captured["client_secret"] == "web-app-secret-1"

    # custody: the secret belongs to the deployment, so it is not in the slot
    async with AsyncSessionLocal() as db:
        sealed = (
            await db.execute(
                text(
                    "SELECT sealed_blob FROM calendar_binding_credentials "
                    "WHERE binding_id = CAST(:bid AS uuid)"
                ).bindparams(bid=binding["id"])
            )
        ).scalar_one()
        assert "web-app-secret-1" not in str(sealed)


@pytest.mark.asyncio
async def test_oauth_callback_omits_the_secret_for_a_public_client(
    client, owner_env, fake_state_store, monkeypatch
):
    """A public client (Microsoft "Mobile and desktop") takes pure PKCE: no
    secret is configured and none may be invented."""
    owner, owner_headers = owner_env
    monkeypatch.setenv("CALENDAR_OAUTH_CLIENT_IDS", '{"google": "client-123"}')
    monkeypatch.delenv("CALENDAR_OAUTH_CLIENT_SECRETS", raising=False)
    patient_id = await _patient_id_for(owner)
    binding = await _api_binding(client, owner_headers, patient_id)
    started = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/oauth/start", headers=owner_headers
    )
    state = started.json()["state"]

    captured: dict[str, str | None] = {}

    async def fake_exchange(
        token_url, code, verifier, redirect_uri, client_id, *, client_secret=None, http
    ):
        captured["client_secret"] = client_secret
        return {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600}

    monkeypatch.setattr("app.api.v1.endpoints.calendar.exchange_code", fake_exchange)
    completed = await client.get(
        f"/api/v1/calendar/oauth/callback?state={state}&code=good-code",
        headers=owner_headers,
    )
    assert completed.status_code == 302
    assert captured["client_secret"] is None


@pytest.mark.asyncio
async def test_oauth_callback_requires_a_session(client, fake_state_store, monkeypatch):
    """No session at all → the callback cannot complete anything."""
    monkeypatch.setenv("CALENDAR_OAUTH_CLIENT_IDS", '{"google": "client-123"}')
    response = await client.get(
        "/api/v1/calendar/oauth/callback?state=whatever&code=x",
    )
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# B — token leak through error detail (+ C4 row custody)
# ---------------------------------------------------------------------------


def test_sanitize_strips_query_and_userinfo():
    dirty = (
        "GET https://feeds.example.com/cal.ics?token=abc123&user=bob#a-frag "
        "failed for https://user:pass@internal.example.com/dav/"
    )
    clean = sanitize_error_detail(dirty)
    assert "abc123" not in clean
    assert "user=bob" not in clean
    assert "a-frag" not in clean
    assert "user:pass@" not in clean
    assert "https://feeds.example.com/cal.ics" in clean
    assert "https://internal.example.com/dav/" in clean
    assert sanitize_url("https://h/p?token=x") == "https://h/p"
    assert sanitize_url("not a url") == "<url-redacted>"
    assert sanitize_error_detail(None) is None


class _ExplodingClient:
    """An httpx-shaped client whose ``send`` fails with the URL (and its
    token query) embedded in the exception message — exactly what httpx
    does for connection errors."""

    def __init__(self, url: str) -> None:
        self._url = url
        self.calls = 0

    def build_request(self, method, url, **kwargs):
        kwargs.pop("timeout", None)  # httpx.Request has no timeout kwarg
        return httpx.Request(method, url, **kwargs)

    async def send(self, request, stream=False):
        self.calls += 1
        raise httpx.ConnectError(
            f"[Errno -2] Name or service not known: GET {self._url}"
        )

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_provider_error_never_carries_the_feed_token(data_key, caplog):
    """The vendored transport puts ``str(exc)`` into ``CalendarError.detail``;
    the host must scrub before storing/surfacing. The token appears
    nowhere: not in ``last_error``, not in the audit row, not in the
    outcome, not in the logs."""
    binding, _exam = await _make_world(
        provider="ics", auth_kind="token", direction="pull"
    )
    full_url = f"https://feeds.example.com/calendar.ics?token={SECRET_FEED_TOKEN}"

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(
                select(CalendarBinding).where(CalendarBinding.id == binding.id)
            )
        ).scalar_one()
        await creds.set_secret(db, live, {"url": full_url})
        await db.commit()

    exploding = _ExplodingClient(full_url)

    def fake_check_url(url, *, config=None, resolver=None):
        return ["93.184.216.34"]  # the guard passes; the client explodes

    import app.extensions.polite_http as polite_http

    original = polite_http.check_url
    polite_http.check_url = fake_check_url
    try:
        async with AsyncSessionLocal() as db:
            live = (
                await db.execute(
                    select(CalendarBinding).where(CalendarBinding.id == binding.id)
                )
            ).scalar_one()
            with caplog.at_level("ERROR"):
                outcome = await calendar_sync.run_sync(db, live, http=exploding)
    finally:
        polite_http.check_url = original

    assert outcome.ok is False
    assert exploding.calls >= 1, "the guarded client must have reached the transport"
    assert SECRET_FEED_TOKEN not in (outcome.error_detail or "")
    assert SECRET_FEED_TOKEN not in repr(outcome.to_dict())
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(
                select(CalendarBinding).where(CalendarBinding.id == binding.id)
            )
        ).scalar_one()
        assert SECRET_FEED_TOKEN not in (live.last_error or "")
        row = (
            await db.execute(
                text(
                    "SELECT error_detail FROM calendar_sync_logs "
                    "WHERE binding_id = CAST(:bid AS uuid) ORDER BY started_at DESC LIMIT 1"
                ).bindparams(bid=live.id)
            )
        ).scalar()
        assert row is not None
        assert SECRET_FEED_TOKEN not in row
    for record in caplog.records:
        assert SECRET_FEED_TOKEN not in record.getMessage()


def test_the_vendored_transport_leaks_by_contract_and_the_scrub_contains_it():
    """Documents the E12 defect this wall defends against: the contract's
    ``_HttpTransport`` embeds ``str(exc)`` (full URL, token query and all)
    in ``CalendarError.detail`` — the host scrub is the boundary."""
    from app.extensions.cloud.cloud_calendar import IcsCalendarTransport

    full_url = f"https://feeds.example.com/calendar.ics?token={SECRET_FEED_TOKEN}"

    class _LeakClient:
        def request(self, method, url, **kwargs):
            # httpx-shaped call — the transport's `_request` awaits this.
            raise httpx.ConnectError(f"[Errno -2] Name or service not known: GET {url}")

        async def aclose(self):
            return None

    transport = IcsCalendarTransport(_LeakClient(), full_url)
    import asyncio

    with pytest.raises(CalendarError) as excinfo:
        asyncio.run(
            transport.list_events(
                datetime.now(UTC) - timedelta(days=1), datetime.now(UTC) + timedelta(days=1)
            )
        )
    assert excinfo.value.code == "unreachable"
    # the contract leaks (this is why the host boundary must scrub):
    assert SECRET_FEED_TOKEN in excinfo.value.detail
    # ...and the host scrub removes exactly that:
    assert SECRET_FEED_TOKEN not in (sanitize_error_detail(excinfo.value.detail) or "")


@pytest.mark.asyncio
async def test_private_feed_url_is_custodied_in_the_row_and_response(client, owner_env):
    """C4: a feed URL with a query string is a credential. The create/update
    response and the stored row carry scheme+host+path only; the API names
    the keys whose full URL must be sealed."""
    owner, headers = owner_env
    patient_id = await _patient_id_for(owner)
    full_url = f"https://feeds.example.com/private.ics?token={SECRET_FEED_TOKEN}"
    created = await _api_binding(
        client,
        headers,
        patient_id,
        provider="ics",
        auth_kind="token",
        direction="pull",
        options={"url": full_url},
    )
    assert created["options"]["url"] == "https://feeds.example.com/private.ics"
    assert "url_custody_required" in created and created["url_custody_required"] == ["url"]
    assert SECRET_FEED_TOKEN not in repr(created)

    listed = await client.get("/api/v1/calendar/bindings", headers=headers)
    assert SECRET_FEED_TOKEN not in listed.text

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(CalendarBinding).where(CalendarBinding.id == uuid.UUID(created["id"]))
            )
        ).scalar_one()
        assert row.options["url"] == "https://feeds.example.com/private.ics"

    # A query URL with a slot-less auth kind is refused outright.
    denied = await client.post(
        "/api/v1/calendar/bindings",
        json={
            "provider": "ics",
            "auth_kind": "none",
            "patient_id": str(patient_id),
            "direction": "pull",
            "options": {"url": full_url},
        },
        headers=headers,
    )
    assert denied.status_code == 422


# ---------------------------------------------------------------------------
# C — consent notice keyed to the CHOSEN auth kind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consent_notice_is_keyed_to_the_chosen_auth_kind(client, owner_env):
    """E12: ``consent_notice()`` defaulting to ``auth_kinds[0]`` made a
    token ICS bind read "needs no credential". The catalog surface now
    carries a verbatim notice per kind; the token notice names the token."""
    owner, headers = owner_env
    _ = await _patient_id_for(owner)
    response = await client.get("/api/v1/calendar/providers", headers=headers)
    assert response.status_code == 200
    providers = {row["id"]: row for row in response.json()["providers"]}
    ics = providers["ics"]
    assert set(ics["authKinds"]) == {"none", "token"}
    assert "token" in ics["consentNotices"] and "none" in ics["consentNotices"]
    assert "token" in ics["consentNotices"]["token"].lower()
    assert "no credential" in ics["consentNotices"]["none"]
    assert (
        ics["consentNotices"]["token"] != ics["consentNotices"]["none"]
    ), "a token bind must never be told it needs no credential"
    caldav = providers["caldav"]
    assert "app-password" in caldav["consentNotices"]


# ---------------------------------------------------------------------------
# D — inbound is untrusted (C3)
# ---------------------------------------------------------------------------


def _remote_event(uid: str, *, categories: tuple[str, ...] = (), **kwargs) -> CalendarEvent:
    start = datetime.now(UTC) + timedelta(days=3)
    return CalendarEvent(
        uid=uid,
        start=start,
        end=start + timedelta(hours=1),
        summary="Untrusted calendar text",
        categories=categories,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_pulled_event_is_flagged_unverified_and_never_completed(data_key):
    remote_uid = f"health:clinical-event:{uuid4()}"
    binding, _exam = await _make_world(
        provider="nextcloud", auth_kind="none", direction="two-way"
    )
    transport = FakeTransport(events=[_remote_event(remote_uid)])
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)
    assert outcome.ok is True
    assert outcome.counts.get("create-local") == 1
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(ClinicalEvent).where(ClinicalEvent.external_id == remote_uid)
            )
        ).scalar_one()
        metadata = dict(row.event_metadata or {})
        assert metadata.get("origin") == "calendar"
        assert metadata.get("unverified") is True, "inbound is untrusted until confirmed"
        assert row.status.value == "ACTIVE", "never auto-completed"
        assert row.source_integration_id is None
        assert metadata.get("calendar_binding_id") == str(binding.id)


@pytest.mark.asyncio
async def test_second_pull_does_not_duplicate_the_record(data_key):
    remote_uid = f"health:clinical-event:{uuid4()}"
    binding, _exam = await _make_world(
        provider="nextcloud", auth_kind="none", direction="two-way"
    )
    transport = FakeTransport(events=[_remote_event(remote_uid)])
    for _ in range(2):
        async with AsyncSessionLocal() as db:
            live = (
                await db.execute(
                    select(CalendarBinding).where(CalendarBinding.id == binding.id)
                )
            ).scalar_one()
            outcome = await calendar_sync.run_sync(db, live, transport=transport)
    assert outcome.ok is True
    assert outcome.counts.get("create-local") in (None, 0), (
        "dedup on (tenant, patient, external_id)"
    )
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ClinicalEvent).where(ClinicalEvent.external_id == remote_uid)
            )
        ).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_calendar_dedup_index_refuses_a_duplicate():
    """The partial unique index is the race backstop for the pre-check."""
    tenant_id, _user_id, patient_id = await _seed_patient_world()
    external = f"remote-uid-{uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        await db.flush()
        db.add(
            ClinicalEvent(
                tenant_id=tenant_id,
                patient_id=patient_id,
                title="first",
                external_id=external,
                event_metadata={"origin": "calendar", "unverified": True},
            )
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        db.add(
            ClinicalEvent(
                tenant_id=tenant_id,
                patient_id=patient_id,
                title="second — same remote uid",
                external_id=external,
                event_metadata={"origin": "calendar", "unverified": True},
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


@pytest.mark.asyncio
async def test_foreign_event_is_not_pulled_by_default(data_key):
    """The default acceptance predicate admits only ``health:`` round-trip
    uids — never the whole personal calendar."""
    foreign_uid = f"family-reunion-{uuid4().hex[:8]}"
    binding, _exam = await _make_world(
        provider="nextcloud", auth_kind="none", direction="two-way"
    )
    transport = FakeTransport(events=[_remote_event(foreign_uid)])
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)
    assert outcome.ok is True
    assert outcome.counts.get("create-local") is None
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ClinicalEvent).where(ClinicalEvent.external_id == foreign_uid)
            )
        ).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_accept_categories_option_admits_matching_events(data_key):
    remote_uid = f"club-{uuid4().hex[:8]}"
    binding, _exam = await _make_world(
        provider="nextcloud",
        auth_kind="none",
        direction="two-way",
        options={"accept_categories": ["THERAPY"]},
    )
    transport = FakeTransport(events=[_remote_event(remote_uid, categories=("Therapy",))])
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)
    assert outcome.counts.get("create-local") == 1, "categories match case-insensitively"


@pytest.mark.asyncio
async def test_accept_all_is_an_explicit_opt_in(data_key):
    foreign_uid = f"whatever-{uuid4().hex[:8]}"
    binding, _exam = await _make_world(
        provider="nextcloud",
        auth_kind="none",
        direction="two-way",
        options={"accept_all": True},
    )
    transport = FakeTransport(events=[_remote_event(foreign_uid)])
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)
    assert outcome.counts.get("create-local") == 1


@pytest.mark.asyncio
async def test_confirm_and_dismiss_are_the_tier1_surface(client, owner_env, data_key):
    owner, headers = owner_env
    patient_id = await _patient_id_for(owner)
    binding = await _api_binding(
        client,
        headers,
        patient_id,
        provider="ics",
        auth_kind="none",
        direction="pull",
    )
    external = f"confirm-{uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        db.add(
            ClinicalEvent(
                tenant_id=uuid.UUID(binding["tenant_id"]),
                patient_id=uuid.UUID(binding["patient_id"]),
                title="Pulled text",
                external_id=external,
                event_metadata={"origin": "calendar", "unverified": True},
            )
        )
        await db.commit()
        row_id = (
            await db.execute(
                select(ClinicalEvent).where(ClinicalEvent.external_id == external)
            )
        ).scalar_one().id

    confirmed = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/inbound/{row_id}/confirm",
        headers=headers,
    )
    assert confirmed.status_code == 200
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(ClinicalEvent).where(ClinicalEvent.id == row_id))
        ).scalar_one()
        assert row.event_metadata["unverified"] is False

    dismissed = await client.post(
        f"/api/v1/calendar/bindings/{binding['id']}/inbound/{row_id}/dismiss",
        headers=headers,
    )
    assert dismissed.status_code == 200
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(ClinicalEvent).where(ClinicalEvent.id == row_id))
        ).scalar_one()
        assert row.deleted_at is not None

    # a stranger cannot confirm or dismiss someone else's inbound record
    other = await create_user(role=Role.USER, full_name="Cal Other")
    other_headers = await auth_headers(other)
    for action in ("confirm", "dismiss"):
        response = await client.post(
            f"/api/v1/calendar/bindings/{binding['id']}/inbound/{row_id}/{action}",
            headers=other_headers,
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# E — one writer per remote calendar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_redaction_patch_is_consent_gated_both_ways(client, owner_env):
    """Redaction granularity (E11/F): the profile lives on the binding (the
    calendar link), defaults to ``neutral``, and ``full`` — the profile that
    publishes real titles/notes/locations — requires the explicit consent
    flag on EVERY request that sets it."""
    owner, headers = owner_env
    patient_id = await _patient_id_for(owner)
    binding = await _api_binding(client, headers, patient_id)
    assert binding["redaction"] == "neutral"

    denied = await client.patch(
        f"/api/v1/calendar/bindings/{binding['id']}",
        json={"redaction": "full"},
        headers=headers,
    )
    assert denied.status_code == 422
    allowed = await client.patch(
        f"/api/v1/calendar/bindings/{binding['id']}",
        json={"redaction": "full", "consent_full_details": True},
        headers=headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["redaction"] == "full"
    # and back down is always allowed (narrowing needs no consent)
    narrowed = await client.patch(
        f"/api/v1/calendar/bindings/{binding['id']}",
        json={"redaction": "neutral"},
        headers=headers,
    )
    assert narrowed.status_code == 200
    assert narrowed.json()["redaction"] == "neutral"


@pytest.mark.asyncio
async def test_second_writer_over_the_same_calendar_is_refused(client, owner_env):
    owner, headers = owner_env
    patient_id = await _patient_id_for(owner)
    first = await _api_binding(
        client, headers, patient_id, options={"calendar_id": "primary"}
    )
    assert first["remote_calendar_id"] == "primary"
    second = await client.post(
        "/api/v1/calendar/bindings",
        json={
            "provider": "google",
            "patient_id": str(patient_id),
            "direction": "push",
            "options": {"calendar_id": "primary"},
        },
        headers=headers,
    )
    assert second.status_code == 409, second.text

    # a read-only subscription may share the calendar
    reader = await client.post(
        "/api/v1/calendar/bindings",
        json={
            "provider": "google",
            "patient_id": str(patient_id),
            "direction": "pull",
            "options": {"calendar_id": "primary"},
        },
        headers=headers,
    )
    assert reader.status_code == 200

    # a different calendar is a different writer slot
    other = await client.post(
        "/api/v1/calendar/bindings",
        json={
            "provider": "google",
            "patient_id": str(patient_id),
            "direction": "push",
            "options": {"calendar_id": "work"},
        },
        headers=headers,
    )
    assert other.status_code == 200

    # flipping the reader to writable hits the same law
    flipped = await client.patch(
        f"/api/v1/calendar/bindings/{reader.json()['id']}",
        json={"direction": "two-way"},
        headers=headers,
    )
    assert flipped.status_code == 409


@pytest.mark.asyncio
async def test_writer_uniqueness_is_enforced_by_the_index():
    """The partial unique index is the DB-level backstop (direct rows)."""
    tenant_id, user_id, patient_id = await _seed_patient_world()
    async with AsyncSessionLocal() as db:
        db.add(
            CalendarBinding(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="google",
                service="calendar",
                auth_kind="oauth2",
                label="first writer",
                direction="push",
                redaction="neutral",
                remote_calendar_id="primary",
                options={},
            )
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        db.add(
            CalendarBinding(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider="google",
                service="calendar",
                auth_kind="oauth2",
                label="second writer",
                direction="two-way",
                redaction="neutral",
                remote_calendar_id="primary",
                options={},
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


@pytest.mark.asyncio
async def test_pull_bindings_may_share_the_calendar_at_the_index_level():
    tenant_id, user_id, patient_id = await _seed_patient_world()
    async with AsyncSessionLocal() as db:
        for label in ("reader one", "reader two"):
            db.add(
                CalendarBinding(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    patient_id=patient_id,
                    provider="google",
                    service="calendar",
                    auth_kind="oauth2",
                    label=label,
                    direction="pull",
                    redaction="neutral",
                    remote_calendar_id="shared",
                    options={},
                )
            )
        await db.commit()


# ---------------------------------------------------------------------------
# G — SSRF: user-supplied URLs go through the guarded runtime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internal_address_is_refused_before_any_connection(data_key):
    """``http://10.0.0.4/…`` is a reserved-range target: the guard refuses
    it (typed ``net-denied`` → ``failing``) and the transport never dials."""
    binding, _exam = await _make_world(
        provider="ics",
        auth_kind="none",
        direction="pull",
        options={"url": "http://10.0.0.4/feed.ics"},
    )
    dialed: list[str] = []

    class _SpyClient:
        async def request(self, method, url, **kwargs):
            dialed.append(str(url))
            raise AssertionError("the guard must refuse before any connection")

        async def aclose(self):
            return None

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, http=_SpyClient())
    assert outcome.ok is False
    assert outcome.error_code == "net-denied"
    assert outcome.status == "failing"
    assert dialed == []
    live = await _reload_binding(binding.id)
    # the guard names the host (config diagnostics) but never the path/query
    assert "feed.ics" not in (live.last_error or ""), "no URL path in the surface"


@pytest.mark.asyncio
async def test_redirect_into_a_private_address_is_refused(data_key):
    """A public feed 302-ing into the internal network dies at the hop."""
    from unittest.mock import patch

    import app.extensions.polite_http as polite_http

    public_url = "https://feeds.example.com/cal.ics"

    def fake_check_url(url, *, config=None, resolver=None):
        if str(url).startswith("https://feeds.example.com"):
            return ["93.184.216.34"]  # the public hop passes
        raise polite_http.PoliteHttpError(
            "net-denied", f"host is a private/loopback address ({url})"
        )  # any other target goes through the REAL guard's refusal

    class _RedirectingClient:
        def __init__(self) -> None:
            self.calls = 0

        def build_request(self, method, url, **kwargs):
            kwargs.pop("timeout", None)  # httpx.Request has no timeout kwarg
            return httpx.Request(method, url, **kwargs)

        async def send(self, request, stream=False):
            self.calls += 1
            # the pin dials the resolved IP but keeps the original Host —
            # and a private hop must never reach send at all
            assert (
                request.headers.get("host") == "feeds.example.com"
            ), "the private hop must never dial"
            return httpx.Response(
                302,
                headers={"location": "http://10.0.0.9/cal.ics"},
                request=request,
            )

        async def aclose(self):
            return None

    binding, _exam = await _make_world(
        provider="ics", auth_kind="none", direction="pull", options={"url": public_url}
    )
    spy = _RedirectingClient()
    with patch.object(polite_http, "check_url", fake_check_url):
        async with AsyncSessionLocal() as db:
            live = (
                await db.execute(
                    select(CalendarBinding).where(CalendarBinding.id == binding.id)
                )
            ).scalar_one()
            outcome = await calendar_sync.run_sync(db, live, http=spy)
    assert outcome.ok is False
    print("DEBUG outcome:", outcome.error_code, "|", outcome.error_detail)
    assert outcome.error_code == "net-denied"
    assert spy.calls == 1, "exactly the public hop dialed; the private hop was refused"


async def _reload_binding(binding_id):
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding_id))
        ).scalar_one()
        _ = row.id  # materialize before the session closes
        return row


# ---------------------------------------------------------------------------
# H — refresh-failure posture (ADR-0052 §3)
# ---------------------------------------------------------------------------


def _expired_slot():
    return {
        "access_token": "at-expired",
        "refresh_token": "rt-1",
        "expires_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        "scope": "s",
        "token_endpoint": "https://oauth2.example.com/token",
        "client_id": "client-123",
    }


@pytest.mark.asyncio
async def test_invalid_grant_stops_as_needs_auth_and_notifies_once(data_key, monkeypatch):
    binding, _exam = await _make_world()
    await _seed_oauth_slot(binding, expires_in_seconds=-3600)()

    async def rejecting_refresh(token_url, refresh_value, client_id, *, http, **kwargs):
        raise ExtensionError(
            "auth", "OAuth error 'invalid_grant'", status=400
        )

    monkeypatch.setattr("app.extensions.sdk.auth.refresh_token", rejecting_refresh)

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        first = await calendar_sync.run_sync(db, live, transport=FakeTransport())
    assert first.ok is False
    assert first.status == "needs-auth"
    assert first.error_code == "auth"

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        second = await calendar_sync.run_sync(db, live, transport=FakeTransport())
    assert second.status == "needs-auth"

    live = await _reload_binding(binding.id)
    assert live.status == "needs-auth"
    assert live.consecutive_failures == 0, "a typed stop never counts as a transient streak"
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT error_code FROM calendar_sync_logs "
                    "WHERE binding_id = CAST(:bid AS uuid)"
                )
                .bindparams(bid=binding.id)
            )
        ).scalars().all()
        assert rows == ["auth"], "notify ONCE — the repeat run must not re-audit"


@pytest.mark.asyncio
async def test_insufficient_scope_needs_a_fresh_consent(data_key, monkeypatch):
    binding, _exam = await _make_world()
    await _seed_oauth_slot(binding, expires_in_seconds=-3600)()

    async def scope_rejected(token_url, refresh_value, client_id, *, http, **kwargs):
        raise ExtensionError(
            "auth", "OAuth error 'insufficient_scope'", status=403
        )

    monkeypatch.setattr("app.extensions.sdk.auth.refresh_token", scope_rejected)
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=FakeTransport())
    assert outcome.status == "needs-secret"
    assert outcome.error_code == "needs-secret"


@pytest.mark.asyncio
async def test_refresh_sends_the_deployment_client_secret(data_key, monkeypatch):
    """Refresh-on-use also carries the confidential client's secret — still
    read from deployment config, never from the binding slot."""
    binding, _exam = await _make_world()
    await _seed_oauth_slot(binding, expires_in_seconds=-3600)()
    monkeypatch.setenv("CALENDAR_OAUTH_CLIENT_SECRETS", '{"google": "web-app-secret-1"}')
    seen: dict[str, str | None] = {}

    async def capture(
        token_url, refresh_value, client_id, *, client_secret=None, http, **kwargs
    ):
        seen["client_secret"] = client_secret
        return {
            "access_token": "at-new",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "scope": "https://www.googleapis.com/auth/calendar.events",
        }

    monkeypatch.setattr("app.extensions.sdk.auth.refresh_token", capture)
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=FakeTransport())
    assert outcome.ok is True
    assert seen["client_secret"] == "web-app-secret-1"


@pytest.mark.asyncio
async def test_transient_refresh_failures_degrade_then_fail(data_key, monkeypatch):
    binding, _exam = await _make_world()
    await _seed_oauth_slot(binding, expires_in_seconds=-3600)()

    async def flapping(token_url, refresh_value, client_id, *, http, **kwargs):
        raise ExtensionError("unreachable", "token endpoint unreachable", status=None)

    monkeypatch.setattr("app.extensions.sdk.auth.refresh_token", flapping)
    statuses = []
    for _ in range(calendar_sync.FAILURE_CAP + 1):
        async with AsyncSessionLocal() as db:
            live = (
                await db.execute(
                    select(CalendarBinding).where(CalendarBinding.id == binding.id)
                )
            ).scalar_one()
            outcome = await calendar_sync.run_sync(db, live, transport=FakeTransport())
        statuses.append(outcome.status)
    assert statuses[0] == "degraded"
    assert statuses[calendar_sync.FAILURE_CAP - 2] == "degraded"
    assert statuses[calendar_sync.FAILURE_CAP - 1] == "failing", "bounded backoff, then stop"


# ---------------------------------------------------------------------------
# I — migration shape (the round-trip itself is verified by conftest +
# a manual downgrade/upgrade run; here the schema the law needs is pinned)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_carries_the_laws_shape():
    async with AsyncSessionLocal() as db:
        indexes = (
            await db.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE indexname IN ("
                    "'uq_calendar_bindings_remote_calendar', "
                    "'uq_clinical_events_calendar_dedup')"
                )
            )
        ).scalars().all()
        assert set(indexes) == {
            "uq_calendar_bindings_remote_calendar",
            "uq_clinical_events_calendar_dedup",
        }
        columns = (
            await db.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_name IN ('calendar_bindings','calendar_event_links') "
                    "AND column_name IN "
                    "('remote_calendar_id','local_hash','remote_hash','content_hash')"
                )
            )
        ).all()
        found = {(table, column) for table, column in columns}
        assert ("calendar_bindings", "remote_calendar_id") in found
        assert ("calendar_event_links", "local_hash") in found
        assert ("calendar_event_links", "remote_hash") in found
        assert ("calendar_event_links", "content_hash") not in found, (
            "E3: one shared hash is the defect — the pins are separate"
        )
