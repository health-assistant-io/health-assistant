"""Calendar sync: reconcile/apply idempotency + typed failure posture.

Runs the real pipeline against a **fake transport** (the seam the family
contract injects auth through) and a live test DB:

* a push run projects local records through the binding's redaction
  profile, applies ``create-remote`` actions and pins the link rows;
* a second run with nothing changed is idempotent — no duplicate remote
  writes, zero new links;
* a local change produces ``update-remote`` on the next run;
* typed failures map to the ADR posture: ``auth`` ⇒ ``needs-auth``,
  transient ⇒ ``degraded``; every run writes one audit row with counts +
  typed codes only (no titles);
* the cursor round-trips opaquely; a disabled binding is skipped.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.extensions.cloud.cloud_calendar import CalendarError, CalendarEvent, CalendarPage
from app.models.calendar_binding import CalendarBinding, CalendarEventLink, CalendarSyncLog
from app.models.clinical_event import ClinicalEvent
from app.models.enums import Role
from app.models.examination_model import ExaminationModel
from app.services.calendar import credentials as creds
from app.services.calendar import sync as calendar_sync

FUTURE_DAY = (datetime.now(UTC) + timedelta(days=30)).date()


@pytest.fixture
def data_key(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "HA_DATA_KEY", key)
    monkeypatch.setattr(settings, "HA_DATA_KEY_PREVIOUS", "")
    return key


class FakeTransport:
    """The transport seam — records writes, replays a fixed remote world."""

    writable = True

    def __init__(
        self, events: list[CalendarEvent] | None = None, *, fail: CalendarError | None = None
    ):
        self.events = list(events or [])
        self.put: list[CalendarEvent] = []
        self.deleted: list[CalendarEvent] = []
        self.fail = fail
        self.sync_token = "cursor-1"

    async def list_events(self, start, end, *, page_token=None, sync_token=None) -> CalendarPage:
        if self.fail is not None:
            raise self.fail
        return CalendarPage(events=list(self.events), next_sync_token=self.sync_token)

    async def put_event(self, event: CalendarEvent) -> CalendarEvent:
        self.put.append(event)
        self.events = [e for e in self.events if e.uid != event.uid]
        self.events.append(event)
        return replace(event, remote_id=f"remote-{event.uid}", etag="etag-1")

    async def delete_event(self, event: CalendarEvent) -> None:
        self.deleted.append(event)
        self.events = [e for e in self.events if e.uid != event.uid]

    async def aclose(self) -> None:
        return None


async def _make_world(
    *, provider: str = "google", auth_kind: str = "oauth2", **binding_overrides
) -> Any:
    """One tenant + user + patient + binding (+ a future examination)."""
    from app.models.fhir.patient import Patient
    from app.models.tenant_model import TenantModel
    from app.models.user_model import UserModel

    tenant_id, user_id, patient_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    binding_id = uuid.uuid4()
    exam_id = uuid.uuid4()
    direction = binding_overrides.pop("direction", "push")
    options = binding_overrides.pop("options", {})

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Sync T.", slug=f"sync-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"sync-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role=Role.USER.value,
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["Sync"]},
                gender="UNKNOWN",
            )
        )
        await db.flush()
        db.add(
            ExaminationModel(
                id=exam_id,
                tenant_id=tenant_id,
                patient_id=patient_id,
                examination_date=FUTURE_DAY,
                notes="internal only",
            )
        )
        await db.flush()
        db.add(
            CalendarBinding(
                id=binding_id,
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                provider=provider,
                service="calendar",
                auth_kind=auth_kind,
                label="Test calendar",
                secret_ref=creds.secret_ref_for(binding_id, auth_kind),
                direction=direction,
                redaction="neutral",
                status="unbound",
                options=options,
                **binding_overrides,
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        binding = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding_id))
        ).scalar_one()
        exam = (
            await db.execute(select(ExaminationModel).where(ExaminationModel.id == exam_id))
        ).scalar_one()
        return binding, exam


async def _reload(binding_id) -> CalendarBinding:
    async with AsyncSessionLocal() as db:
        binding = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding_id))
        ).scalar_one()
        _ = binding.id  # load attributes before the session closes
        return binding


def _seed_oauth_slot(binding, *, token: str = "at-test", expires_in_seconds: int = 3600):
    async def _seed():
        async with AsyncSessionLocal() as db:
            live = (
                await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
            ).scalar_one()
            await creds.set_secret(
                db,
                live,
                {
                    "access_token": token,
                    "refresh_token": "rt-test",
                    "expires_at": (
                        datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
                    ).isoformat(),
                    "scope": "https://www.googleapis.com/auth/calendar.events",
                    "token_endpoint": "https://oauth2.example.com/token",
                    "client_id": "client-123",
                },
                scopes=["https://www.googleapis.com/auth/calendar.events"],
            )
            await db.commit()

    return _seed


@pytest.mark.asyncio
async def test_push_projects_and_pins_links(data_key):
    binding, exam = await _make_world()
    await _seed_oauth_slot(binding)()
    transport = FakeTransport()

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)

    assert outcome.ok is True
    assert outcome.status == "ok"
    assert outcome.counts.get("create-remote") == 1
    # the remote event is the *neutral* projection — never the record data
    assert len(transport.put) == 1
    pushed = transport.put[0]
    assert pushed.uid == f"health:examination:{exam.id}"
    assert pushed.summary == "Medical appointment"
    assert pushed.all_day is True
    assert "internal only" not in repr(pushed.summary + pushed.description + pushed.location)
    # link row pinned with remote identity + the SEPARATE per-side hashes (E3)
    async with AsyncSessionLocal() as db:
        link = (
            await db.execute(
                select(CalendarEventLink).where(CalendarEventLink.binding_id == binding.id)
            )
        ).scalar_one()
        assert link.uid == pushed.uid
        assert link.remote_id == f"remote-{pushed.uid}"
        assert link.local_hash and link.remote_hash
        assert link.local_kind == "examination"
        live = await _reload(binding.id)
        assert live.status == "ok"
        assert live.sync_cursor == "cursor-1"
        assert live.last_synced_at is not None


@pytest.mark.asyncio
async def test_second_run_is_idempotent(data_key):
    binding, _exam_row = await _make_world()
    await _seed_oauth_slot(binding)()
    transport = FakeTransport()

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        first = await calendar_sync.run_sync(db, live, transport=transport)
        second = await calendar_sync.run_sync(db, live, transport=transport)

    assert first.ok and second.ok
    assert first.counts.get("create-remote") == 1
    assert not second.counts.get("create-remote")
    assert not second.counts.get("update-remote")
    assert len(transport.put) == 1, "a no-change rerun must not write to the provider"
    async with AsyncSessionLocal() as db:
        links = (
            await db.execute(
                select(CalendarEventLink).where(CalendarEventLink.binding_id == binding.id)
            )
        ).scalars().all()
        assert len(links) == 1


@pytest.mark.asyncio
async def test_local_change_updates_the_remote(data_key):
    binding, exam = await _make_world()
    await _seed_oauth_slot(binding)()
    transport = FakeTransport()

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        await calendar_sync.run_sync(db, live, transport=transport)

    async with AsyncSessionLocal() as db:
        exam_row = (
            await db.execute(
                select(ExaminationModel).where(ExaminationModel.id == exam.id)
            )
        ).scalar_one()
        exam_row.examination_date = FUTURE_DAY + timedelta(days=1)
        await db.commit()

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)

    assert outcome.counts.get("update-remote") == 1
    assert len(transport.put) == 2
    assert str(transport.put[-1].start) == str(FUTURE_DAY + timedelta(days=1))


@pytest.mark.asyncio
async def test_auth_failure_maps_to_needs_auth_and_audits(data_key):
    binding, _exam_row = await _make_world()
    await _seed_oauth_slot(binding)()
    transport = FakeTransport(fail=CalendarError("auth", "GET events -> 401"))

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)

    assert outcome.ok is False
    assert outcome.status == "needs-auth"
    assert outcome.error_code == "auth"
    live = await _reload(binding.id)
    assert live.status == "needs-auth"
    # a typed stop never counts toward the transient failure cap (ADR §3)
    assert live.consecutive_failures == 0
    async with AsyncSessionLocal() as db:
        log = (
            await db.execute(
                select(CalendarSyncLog).where(CalendarSyncLog.binding_id == binding.id)
            )
        ).scalar_one()
        assert log.status == "failed"
        assert log.error_code == "auth"
        # the audit row carries counts + typed codes only — never titles
        assert "internal only" not in repr(log.counts) + repr(log.error_detail)


@pytest.mark.asyncio
async def test_transient_failure_degrades(data_key):
    binding, _exam_row = await _make_world()
    await _seed_oauth_slot(binding)()
    transport = FakeTransport(fail=CalendarError("unreachable", "boom", retryable=True))

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)

    assert outcome.status == "degraded"
    live = await _reload(binding.id)
    assert live.status == "degraded"
    assert live.consecutive_failures == 1


@pytest.mark.asyncio
async def test_missing_secret_is_needs_secret_without_any_call(data_key):
    binding, _exam_row = await _make_world()
    transport = FakeTransport()

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)

    assert outcome.ok is False
    assert outcome.status == "needs-secret"
    assert transport.put == [], "the credential gate must fire before any network call"


@pytest.mark.asyncio
async def test_disabled_binding_is_skipped(data_key):
    binding, _exam_row = await _make_world(enabled=False)
    await _seed_oauth_slot(binding)()
    transport = FakeTransport()

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)

    assert outcome.status == "skipped"
    assert transport.put == []


@pytest.mark.asyncio
async def test_pull_of_accepted_event_creates_a_clinical_record(data_key):
    # two-way binding on nextcloud (caldav is writable; a caldav base needs
    # no oauth slot — auth_kind 'none' passes the gate with {})
    remote_uid = f"health:clinical-event:{uuid4()}"
    remote_event = CalendarEvent(
        uid=remote_uid,
        start=datetime.now(UTC) + timedelta(days=3),
        end=datetime.now(UTC) + timedelta(days=3, hours=1),
        summary="Follow-up (self-added)",
    )
    binding, _exam_row = await _make_world(
        provider="nextcloud", auth_kind="none", direction="two-way"
    )
    transport = FakeTransport(events=[remote_event])

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        outcome = await calendar_sync.run_sync(db, live, transport=transport)

    assert outcome.ok is True
    assert outcome.counts.get("create-remote") == 1  # the examination
    assert outcome.counts.get("create-local") == 1  # the accepted remote event
    async with AsyncSessionLocal() as db:
        pulled = (
            await db.execute(
                select(ClinicalEvent).where(ClinicalEvent.external_id == remote_uid)
            )
        ).scalar_one()
        assert pulled is not None
        assert pulled.tenant_id == binding.tenant_id
        assert pulled.patient_id == binding.patient_id


async def test_delta_is_a_probe_never_the_remote_view(data_key):
    """M4 safety: a delta returns CHANGED events only.

    Using it as the reconcile's remote view would read every unchanged event
    as "vanished" and unlink it. The cursor is a fast path — no changes means
    the reconcile is skipped entirely; any change falls through to the window.
    """

    class _DeltaTransport(FakeTransport):
        def __init__(self, events):
            super().__init__(events)
            self.delta_calls = 0
            self.window_calls = 0

        async def list_events(self, start, end, *, page_token=None, sync_token=None):
            if sync_token:
                self.delta_calls += 1
                # a real delta: nothing changed since the cursor
                return CalendarPage(events=[], next_sync_token="cursor-2")
            self.window_calls += 1
            return CalendarPage(events=list(self.events), next_sync_token="cursor-1")

    binding, _exam_row = await _make_world()
    await _seed_oauth_slot(binding)()
    transport = _DeltaTransport([])

    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        first = await calendar_sync.run_sync(db, live, transport=transport)
    assert first.ok
    assert transport.window_calls == 1, "the first run lists the full window"
    assert live.sync_cursor, "the run stores a cursor"

    # second run: the delta reports no changes -> the reconcile never runs
    transport.window_calls = 0
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        second = await calendar_sync.run_sync(db, live, transport=transport)
    assert second.ok
    assert transport.delta_calls >= 1, "the cursor must be used as a probe"
    assert transport.window_calls == 0, "a no-change delta must skip the reconcile"


@pytest.mark.asyncio
async def test_stale_cursor_is_dropped_not_fatal(data_key):
    """A dead cursor triggers a full resync — it is never a failure (E13)."""
    binding, _exam_row = await _make_world()
    await _seed_oauth_slot(binding)()

    class _Stale(FakeTransport):
        async def list_events(self, start, end, *, page_token=None, sync_token=None):
            if sync_token:
                raise CalendarError("not-ready", "sync token expired")
            return CalendarPage(events=list(self.events), next_sync_token="cursor-1")

    transport = _Stale([])
    async with AsyncSessionLocal() as db:
        live = (
            await db.execute(select(CalendarBinding).where(CalendarBinding.id == binding.id))
        ).scalar_one()
        live.sync_cursor = "dead"
        first = await calendar_sync.run_sync(db, live, transport=transport)
    assert first.ok, f"a stale cursor must not fail the run: {first}"


def test_failure_status_transitions():
    assert calendar_sync._failure_status("auth", 0) == "needs-auth"
    assert calendar_sync._failure_status("needs-secret", 0) == "needs-secret"
    assert calendar_sync._failure_status("unreachable", 1) == "degraded"
    assert calendar_sync._failure_status("unreachable", 99) == "failing"
