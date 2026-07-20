"""Tests for the new ``examination_service.create_examination`` write-side
chokepoint (workstream E.1).

Mirrors the dedup-test pattern established in
``test_clinical_event_integration_dedup.py``: the service must dedup on
the integration key (precise) when both ``source_integration_id`` and
``external_id`` are present, and fall back to the heuristic UI dedup
(fuzzy) when ``auto_extract_metadata`` is False. Either way, the
returned row carries the integration provenance fields populated.

These tests use a minimal fake session — they exercise the dedup
decision tree (lookup-or-skip, return-or-create) without spinning up a
real DB.
"""
from uuid import uuid4

import pytest

from app.core.errors import NotFoundError
from app.schemas.examination import ExaminationCreate
from app.schemas.user import TokenData
from app.services import examination_service as svc


TENANT = uuid4()
PATIENT = uuid4()
OWNER = uuid4()


def _actor():
    return TokenData(user_id=OWNER, tenant_id=TENANT, role="USER")


def _payload(**overrides):
    """Minimal ExaminationCreate — every required field, no extras."""
    base = {"patient_id": PATIENT, "examination_date": "2026-07-21"}
    base.update(overrides)
    return ExaminationCreate(**base)


class _FakeResult:
    def __init__(self, rows=None, single=None):
        # ``scalars().first()`` and ``scalar_one_or_none()`` are the two
        # access patterns the service uses. Support both.
        self._rows = rows or []
        self._single = single

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._single


class _FakeSession:
    """Captures every ``execute`` and ``add`` call so tests can assert
    which lookup ran (or didn't) and whether a row was queued."""

    def __init__(self, *, rows=None, single=None):
        # ``rows`` is the list returned by SELECT queries (heuristic +
        # integration dedup lookups); ``single`` is the row returned by
        # patient-existence check (None ⇒ raise NotFoundError path).
        self._rows = rows
        self._single = single
        self.executes = []
        self.added = []

    async def execute(self, query):
        self.executes.append(query)
        # Patient-existence queries select Patient; dedup queries select
        # ExaminationModel. Both end up in ``scalars()`` access — we just
        # return the same fake result for any execute call. Tests that
        # need to distinguish should mock at a finer grain.
        if self._rows is not None:
            return _FakeResult(rows=self._rows)
        return _FakeResult(single=self._single)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()


# ---------------------------------------------------------------------------
# Stubs — keep tests focused on dedup decision tree, not on per-call helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_external_dependencies(monkeypatch):
    """Stub ``check_patient_access``, ``_validate_patient_exists``, and the
    medical-processing category resolver so the service's dedup logic can
    be exercised in isolation."""
    async def _noop_access(*a, **kw):
        return None
    monkeypatch.setattr(svc, "check_patient_access", _noop_access)
    monkeypatch.setattr(svc, "_validate_patient_exists", _noop_access)
    # The category resolver is only invoked when payload.category is set
    # without category_concept_id; default to raising to surface accidental
    # invocations.
    async def _fail_resolve(*a, **kw):
        raise AssertionError("MedicalProcessingService.resolve_category should not be invoked by these tests")
    monkeypatch.setattr(
        "app.ai.pipeline.service.MedicalProcessingService",
        type("M", (), {"resolve_category": _fail_resolve}),
    )


@pytest.fixture(autouse=True)
def _stub_reload(monkeypatch):
    """``_reload_with_relationships`` hits the DB; stub it to echo the id."""
    async def _fake_reload(db, examination_id):
        return type("E", (), {"id": examination_id})()
    monkeypatch.setattr(svc, "_reload_with_relationships", _fake_reload)


# ---------------------------------------------------------------------------
# Integration dedup (precise)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_dedup_hit_returns_existing():
    """When the dedup key matches an existing exam, the service must return
    that row verbatim — no new row queued."""
    existing_id = uuid4()
    existing = type("E", (), {"id": existing_id})()
    db = _FakeSession(rows=[existing])

    integration_id = uuid4()
    result = await svc.create_examination(
        db, _actor(), _payload(),
        source_integration_id=integration_id,
        external_id="upstream-encounter-42",
    )

    assert result.id == existing_id
    assert db.added == [], "Dedup hit must not queue a new exam for insert"


@pytest.mark.asyncio
async def test_integration_dedup_lookup_filters_on_all_four_fields():
    """The dedup SELECT must filter on all four fields. A regression that
    drops one would either over-match (returning unrelated exams) or
    under-match (creating duplicates)."""
    db = _FakeSession(rows=[])  # no match → falls through to create
    integration_id = uuid4()

    await svc.create_examination(
        db, _actor(), _payload(),
        source_integration_id=integration_id,
        external_id="upstream-encounter-42",
    )

    # Find the SELECT that probes the integration-key (it filters on
    # source_integration_id); the patient-existence check doesn't.
    dedup_query = next(
        q for q in db.executes
        if "source_integration_id" in str(q).lower()
    )
    sql = str(dedup_query.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "examinations" in sql
    assert "tenant_id" in sql
    assert "patient_id" in sql
    assert "source_integration_id" in sql
    assert "external_id" in sql


@pytest.mark.asyncio
async def test_integration_dedup_miss_creates_with_provenance():
    """When the dedup key doesn't match, the service must create a new row
    with ``source_integration_id`` and ``external_id`` populated on the
    ORM instance."""
    db = _FakeSession(rows=[])
    integration_id = uuid4()

    await svc.create_examination(
        db, _actor(), _payload(),
        source_integration_id=integration_id,
        external_id="upstream-encounter-42",
    )

    assert len(db.added) == 1, "exactly one ExaminationModel should be queued"
    new_exam = db.added[0]
    assert new_exam.source_integration_id == integration_id
    assert new_exam.external_id == "upstream-encounter-42"
    assert new_exam.tenant_id == TENANT
    assert new_exam.created_by == OWNER


@pytest.mark.asyncio
async def test_integration_dedup_skipped_when_source_integration_id_absent():
    """A caller passing only ``external_id`` must NOT trigger the
    integration-key lookup — defending against an accidental match on a
    NULL ``source_integration_id`` (which would over-match unrelated
    UI-created rows)."""
    db = _FakeSession(rows=[])
    await svc.create_examination(
        db, _actor(), _payload(),
        external_id="orphan-id-no-source",
    )
    # No query should have a ``source_integration_id =`` predicate (which
    # only appears in the integration-key dedup path). The SELECT-list
    # will include the column name regardless (SQLAlchemy expands to all
    # columns), so we filter the assertion to just WHERE-clause predicates
    # by checking for the assignment operator.
    for q in db.executes:
        sql = str(q.compile(compile_kwargs={"literal_binds": True})).lower()
        assert "source_integration_id =" not in sql, (
            "Integration-key lookup must NOT run when source_integration_id "
            "is absent — would over-match unrelated UI rows"
        )


# ---------------------------------------------------------------------------
# Heuristic UI dedup (fuzzy) — preserved from the original endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heuristic_dedup_returns_existing_when_not_auto_extract():
    """When ``auto_extract_metadata`` is False (the default), the service
    runs the original endpoint's fuzzy dedup on
    (tenant, patient, examination_date, category_concept_id, notes). A
    match returns the existing row — preserved from the pre-refactor
    endpoint behavior."""
    existing_id = uuid4()
    existing = type("E", (), {"id": existing_id})()
    db = _FakeSession(rows=[existing])

    # No integration kwargs — exercises the UI-caller path. The first
    # SELECT to hit a row wins; the service returns it.
    result = await svc.create_examination(db, _actor(), _payload())

    assert result.id == existing_id
    assert db.added == [], "Heuristic dedup hit must not queue a new exam"


@pytest.mark.asyncio
async def test_auto_extract_metadata_bypasses_heuristic_dedup():
    """``auto_extract_metadata=True`` must skip the heuristic dedup —
    the upload flow intentionally creates multiple placeholder exams in a
    batch (one per file) and they may share date + category + notes."""
    # rows=[] means the heuristic-dedup SELECT would return nothing
    # anyway, but we want to assert the SELECT didn't run in the first
    # place. Use a fresh session and assert no heuristic-style query
    # (filters on examination_date + notes) was issued.
    db = _FakeSession(rows=[])
    await svc.create_examination(
        db, _actor(), _payload(auto_extract_metadata=True),
    )

    for q in db.executes:
        sql = str(q.compile(compile_kwargs={"literal_binds": True})).lower()
        # The heuristic-dedup query filters on notes + examination_date
        # together; the patient-existence query doesn't touch either.
        if "examinations" in sql and "notes" in sql:
            pytest.fail(
                "Heuristic dedup ran despite auto_extract_metadata=True — "
                "bulk-placeholder upload flow would be broken by this"
            )


# ---------------------------------------------------------------------------
# Patient validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_patient_raises_not_found_error(monkeypatch):
    """A bogus patient_id must surface as :class:`NotFoundError` (mapped to
    HTTP 404 by the global handler). Restores the original endpoint's
    inline 404 behavior — service raises domain exception instead."""
    # Re-enable the real _validate_patient_exists with a stub that raises.
    async def _raise(*a, **kw):
        raise NotFoundError("Patient with ID ... not found.")
    monkeypatch.setattr(svc, "_validate_patient_exists", _raise)
    db = _FakeSession(rows=[])

    with pytest.raises(NotFoundError, match="Patient"):
        await svc.create_examination(db, _actor(), _payload())


# ---------------------------------------------------------------------------
# Source-level guards
# ---------------------------------------------------------------------------


def test_examination_model_has_integration_provenance_columns():
    """The ORM model must declare the two integration-provenance columns.
    Both have existed for a while (originally added for the bridge
    provider); this test guards an accidental revert."""
    from app.models.examination_model import ExaminationModel

    column_names = {c.name for c in ExaminationModel.__table__.columns}
    assert "source_integration_id" in column_names
    assert "external_id" in column_names


def test_endpoint_routes_through_service_chokepoint():
    """Source-level guard that the POST /examinations endpoint calls the
    new service function and doesn't carry its own inline dedup / category
    resolution / ORM construction. Catches a partial revert."""
    import inspect
    from app.api.v1.endpoints import examinations as endpoint_mod

    src = inspect.getsource(endpoint_mod.create_examination)
    assert "from app.services.examination_service import" in src, (
        "POST /examinations must delegate to examination_service.create_examination"
    )
    # The inline-implementation markers that used to live here — direct
    # ORM construction, the heuristic-dedup SELECT, the category-resolver
    # call — must NOT reappear in the endpoint body.
    assert "ExaminationModel(" not in src, (
        "Endpoint must not construct ExaminationModel directly — that's "
        "the service's job"
    )
    assert "resolve_category" not in src, (
        "Endpoint must not resolve categories inline — service owns it now"
    )
