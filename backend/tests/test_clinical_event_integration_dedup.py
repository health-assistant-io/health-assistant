"""Tests for the integration-sourced dedup in ``clinical_event_service.create_event``.

Workstream B.1 of the integrations follow-ups pass. The dedup contract:
when **both** ``source_integration_id`` and ``external_id`` are supplied
to ``create_event``, the service looks up an existing event with that
key for the same patient + tenant and returns it instead of creating a
duplicate. Mirrors the pattern on ``examinations``.

These tests use a minimal fake session — they exercise the dedup decision
tree (lookup-or-skip, return-or-create) without spinning up a real DB.
"""
from uuid import uuid4

import pytest

from app.schemas.user import TokenData
from app.services import clinical_event_service as svc


TENANT = uuid4()
PATIENT = uuid4()
OWNER = uuid4()


def _actor():
    return TokenData(user_id=OWNER, tenant_id=TENANT, role="USER")


def _payload():
    """Minimal ``ClinicalEventCreate`` — every required field, no extras."""
    from app.schemas.clinical_event import ClinicalEventCreate

    return ClinicalEventCreate(
        patient_id=PATIENT,
        title="Hospital admission",
        type_id=None,
    )


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """Captures every ``execute`` call so the test can assert which lookup
    ran (or didn't). ``add`` calls are tracked to confirm a row was/wasn't
    queued for insert. ``flush`` / ``commit`` are no-ops."""

    def __init__(self, find_result=None):
        self._find_result = find_result
        self.executes = []
        self.added = []

    async def execute(self, query):
        self.executes.append(query)
        return _FakeResult(self._find_result)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        # Mimic the server-side id assignment so the service code path
        # which reads ``new_event.id`` after flush doesn't see ``None``.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self):
        return None


# ---------------------------------------------------------------------------
# Dedup hit: both fields set + matching existing row → return as-is
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_event_returns_existing_on_dedup_hit(monkeypatch):
    """When the dedup key matches an existing event, the service must return
    that row verbatim — no new row queued, no commit beyond what
    ``_refetch_with_relations`` does."""
    existing_id = uuid4()
    existing_event = type("E", (), {"id": existing_id})()  # only needs .id

    db = _FakeSession(find_result=existing_event)

    # Stub out the access check (would otherwise hit the DB).
    async def _noop_access(*a, **kw):
        return None
    monkeypatch.setattr(svc, "check_patient_access", _noop_access)

    # Stub out the refetch so we don't need real relationships loaded.
    refetched = {"id": str(existing_id), "dedup_hit": True}
    async def _fake_refetch(*a, **kw):
        return refetched
    monkeypatch.setattr(svc, "_refetch_with_relations", _fake_refetch)

    integration_id = uuid4()
    result = await svc.create_event(
        db,
        _actor(),
        _payload(),
        source_integration_id=integration_id,
        external_id="upstream-encounter-42",
    )

    assert result is refetched
    assert db.added == [], (
        "Dedup hit must not queue a new ClinicalEvent for insert"
    )


@pytest.mark.asyncio
async def test_create_event_dedup_lookup_uses_all_four_fields(monkeypatch):
    """The dedup SELECT must filter on all four fields (tenant_id, patient_id,
    source_integration_id, external_id). A regression that drops any of
    these would either over-match (returning unrelated events) or
    under-match (creating duplicates)."""
    db = _FakeSession(find_result=None)
    async def _noop_access(*a, **kw): return None
    monkeypatch.setattr(svc, "check_patient_access", _noop_access)
    async def _fake_refetch(*a, **kw): return {}
    monkeypatch.setattr(svc, "_refetch_with_relations", _fake_refetch)

    integration_id = uuid4()
    await svc.create_event(
        db,
        _actor(),
        _payload(),
        source_integration_id=integration_id,
        external_id="upstream-encounter-42",
    )

    # The SELECT is the first execute call; compile it to SQL string for
    # assertions. This catches a regression where a filter is dropped.
    assert len(db.executes) >= 1, "dedup lookup should issue a SELECT"
    compiled = db.executes[0].compile(
        compile_kwargs={"literal_binds": True}
    )
    sql = str(compiled).lower()
    assert "clinical_events" in sql
    # All four predicates should be present (column-name fragments).
    assert "tenant_id" in sql
    assert "patient_id" in sql
    assert "source_integration_id" in sql
    assert "external_id" in sql


# ---------------------------------------------------------------------------
# Dedup miss: both fields set, no match → create with provenance populated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_event_creates_with_provenance_on_dedup_miss(monkeypatch):
    """When the dedup key doesn't match an existing event, the service must
    create a new row with ``source_integration_id`` and ``external_id``
    populated on the ORM instance — not just on the wire payload."""
    db = _FakeSession(find_result=None)
    async def _noop_access(*a, **kw): return None
    monkeypatch.setattr(svc, "check_patient_access", _noop_access)
    async def _fake_refetch(*a, **kw): return {}
    monkeypatch.setattr(svc, "_refetch_with_relations", _fake_refetch)

    integration_id = uuid4()
    await svc.create_event(
        db,
        _actor(),
        _payload(),
        source_integration_id=integration_id,
        external_id="upstream-encounter-42",
    )

    assert len(db.added) == 1, "exactly one ClinicalEvent should be queued"
    new_event = db.added[0]
    assert new_event.source_integration_id == integration_id
    assert new_event.external_id == "upstream-encounter-42"
    assert new_event.tenant_id == TENANT
    assert new_event.created_by == OWNER


# ---------------------------------------------------------------------------
# No dedup: either field absent → skip the lookup entirely
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_event_skips_dedup_when_source_integration_id_absent(monkeypatch):
    """A UI caller (no integration context) passes neither field — the
    dedup lookup must not run, preserving the original create-always
    behavior. Catches a regression where the lookup runs with NULLs and
    accidentally matches an unrelated NULL-keyed row."""
    db = _FakeSession(find_result=None)
    async def _noop_access(*a, **kw): return None
    monkeypatch.setattr(svc, "check_patient_access", _noop_access)
    async def _fake_refetch(*a, **kw): return {}
    monkeypatch.setattr(svc, "_refetch_with_relations", _fake_refetch)

    # Only external_id set — should NOT trigger dedup.
    await svc.create_event(
        db, _actor(), _payload(), external_id="orphan-id-no-source"
    )

    # No execute() calls — the dedup SELECT didn't run.
    assert db.executes == [], (
        "dedup lookup must NOT run when source_integration_id is absent"
    )
    assert len(db.added) == 1
    assert db.added[0].source_integration_id is None
    assert db.added[0].external_id == "orphan-id-no-source"


@pytest.mark.asyncio
async def test_create_event_skips_dedup_when_external_id_absent(monkeypatch):
    """Symmetric to the above: integration context but no external_id
    (e.g. an upstream system that doesn't expose stable ids) must also
    skip the dedup lookup."""
    db = _FakeSession(find_result=None)
    async def _noop_access(*a, **kw): return None
    monkeypatch.setattr(svc, "check_patient_access", _noop_access)
    async def _fake_refetch(*a, **kw): return {}
    monkeypatch.setattr(svc, "_refetch_with_relations", _fake_refetch)

    await svc.create_event(
        db,
        _actor(),
        _payload(),
        source_integration_id=uuid4(),  # set
        # external_id omitted
    )

    assert db.executes == [], (
        "dedup lookup must NOT run when external_id is absent"
    )


# ---------------------------------------------------------------------------
# Migration guard (source-level): the new columns exist on the model
# ---------------------------------------------------------------------------


def test_clinical_event_model_has_integration_provenance_columns():
    """The ORM model must declare the two new columns — catches an
    accidental revert that drops them from the model while leaving the
    migration in place (or vice versa)."""
    from app.models.clinical_event import ClinicalEvent

    column_names = {c.name for c in ClinicalEvent.__table__.columns}
    assert "source_integration_id" in column_names
    assert "external_id" in column_names
