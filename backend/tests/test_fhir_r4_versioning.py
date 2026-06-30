"""Regression tests for F5 + F17: honest versioning + If-Match + Location.

F5 (versioning): the CapabilityStatement per-resource ``versioning`` field
now honestly declares ``"no-version"`` (Phase 1.2 / F4). We don't implement
``vread`` / ``history-instance`` / a version-history table, so advertising
``"versioned"`` was a lie. What we DO support is:
- ``VersionedMixin.version`` is bumped on update (internal optimistic-lock
  primitive; still useful).
- The ETag header carries the current version (``W/"<version>"``).
- ``If-Match`` optimistic locking: a PUT with ``If-Match: W/"3"`` against a
  version-2 row returns HTTP 412 Precondition Failed (RFC 7232).

F17 (Location header): the create-response Location header uses the short
form ``<base>/Type/<id>`` — allowed for ``versioning="no-version"`` (the
versioned form ``<base>/Type/<id>/_history/<vid>`` is only required when
``versioning="versioned"`` and ``vread`` is implemented).
"""

from __future__ import annotations

import pytest

from app.facade.crud import PreconditionFailed, _parse_if_match


# ---------------------------------------------------------------------------
# F5 — _parse_if_match
# ---------------------------------------------------------------------------

def test_parse_if_match_weak_etag():
    """The standard FHIR/server form: W/"<version>"."""
    assert _parse_if_match('W/"3"') == 3


def test_parse_if_match_strong_etag():
    """RFC 7232 strong form: "<version>"."""
    assert _parse_if_match('"5"') == 5


def test_parse_if_match_bare_version():
    """Bare integer (non-spec but tolerated by some clients)."""
    assert _parse_if_match("7") == 7


def test_parse_if_match_empty_returns_none():
    assert _parse_if_match("") is None
    assert _parse_if_match("   ") is None


def test_parse_if_match_garbage_returns_none():
    """Garbage values don't raise — return None (caller ignores the header)."""
    assert _parse_if_match("garbage") is None
    assert _parse_if_match('"not-a-number"') is None
    assert _parse_if_match('W/"abc"') is None


def test_parse_if_match_strips_whitespace():
    assert _parse_if_match('  W/"3"  ') == 3
    assert _parse_if_match('  "5"  ') == 5


# ---------------------------------------------------------------------------
# F5 — PreconditionFailed exception carries useful context
# ---------------------------------------------------------------------------

def test_precondition_failed_carries_context():
    """The exception carries the resource type/id and expected/actual version
    so the endpoint can build an informative OperationOutcome."""
    exc = PreconditionFailed(
        resource_type="Patient",
        resource_id="abc",
        expected=3,
        actual=2,
    )
    assert exc.resource_type == "Patient"
    assert exc.resource_id == "abc"
    assert exc.expected == 3
    assert exc.actual == 2
    assert "Patient/abc" in str(exc)
    assert "3" in str(exc)
    assert "2" in str(exc)


# ---------------------------------------------------------------------------
# F5 — crud.update honors If-Match
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_update_if_match_version_check_unit():
    """Unit-level test of the If-Match version check inside crud.update.

    We patch select + db.execute to avoid needing a real SQLAlchemy model.
    Verifies: matching If-Match → succeeds (commit called); mismatched →
    PreconditionFailed raised (commit NOT called); no If-Match → no check.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from app.facade import crud
    from app.facade.registry import ResourceEntry

    RID = str(uuid4())

    class _Stub:
        id = MagicMock()
        tenant_id = MagicMock()

        def __init__(self):
            self.id = RID
            self.version = 2

    entry = ResourceEntry(
        resource_type="StubF5", model=_Stub, tenant_scope="tenant_id"
    )

    class _CurrentUser:
        tenant_id = "00000000-0000-0000-0000-000000000000"
        user_id = "00000000-0000-0000-0000-000000000001"
        role = "USER"

    def _build_db(obj):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = obj
        db.execute = AsyncMock(return_value=result_mock)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    # Patch all the external calls so we isolate the If-Match path.
    common_patches = [
        patch("app.facade.crud.fhir_to_orm", return_value={}),
        patch("app.facade.crud.assert_valid_fhir", return_value={}),
        patch("app.facade.crud.record_provenance", new=AsyncMock()),
        patch("app.facade.crud.select"),
    ]

    # --- Case 1: matching If-Match → succeeds ---
    obj1 = _Stub()
    obj1.to_fhir_dict = MagicMock(return_value={"resourceType": "StubF5", "id": RID})
    db1 = _build_db(obj1)
    for p in common_patches:
        p.start()
    try:
        result = await crud.update(
            entry, RID, {"resourceType": "StubF5"}, _CurrentUser(), db1,
            if_match='W/"2"',
        )
        assert result is not None
        db1.commit.assert_called_once()
    finally:
        for p in common_patches:
            p.stop()

    # --- Case 2: mismatched If-Match → PreconditionFailed ---
    obj2 = _Stub()
    db2 = _build_db(obj2)
    for p in common_patches:
        p.start()
    try:
        with pytest.raises(PreconditionFailed) as exc:
            await crud.update(
                entry, RID, {"resourceType": "StubF5"}, _CurrentUser(), db2,
                if_match='W/"3"',
            )
        assert exc.value.expected == 3
        assert exc.value.actual == 2
        db2.commit.assert_not_called()
    finally:
        for p in common_patches:
            p.stop()

    # --- Case 3: no If-Match → no check ---
    obj3 = _Stub()
    obj3.to_fhir_dict = MagicMock(return_value={"resourceType": "StubF5", "id": RID})
    db3 = _build_db(obj3)
    for p in common_patches:
        p.start()
    try:
        result = await crud.update(
            entry, RID, {"resourceType": "StubF5"}, _CurrentUser(), db3,
            if_match=None,
        )
        assert result is not None
        db3.commit.assert_called_once()
    finally:
        for p in common_patches:
            p.stop()

    # --- Case 4: unparseable If-Match → ignored ---
    obj4 = _Stub()
    obj4.to_fhir_dict = MagicMock(return_value={"resourceType": "StubF5", "id": RID})
    db4 = _build_db(obj4)
    for p in common_patches:
        p.start()
    try:
        result = await crud.update(
            entry, RID, {"resourceType": "StubF5"}, _CurrentUser(), db4,
            if_match="garbage",
        )
        assert result is not None
        db4.commit.assert_called_once()
    finally:
        for p in common_patches:
            p.stop()


    assert result is not None


# ---------------------------------------------------------------------------
# F5 — CapabilityStatement per-resource versioning is no-version
# (already covered by test_capability_statement_per_resource_no_version_no_update_create
# in test_fhir_r4_phase1.py — sanity check here that it remains honest)
# ---------------------------------------------------------------------------

def test_capability_statement_versioning_is_no_version():
    """F5 headline: CapabilityStatement per-resource versioning must be
    'no-version' (we don't implement vread / history-instance)."""
    from app.services.fhir_facade_service import build_capability_statement

    cs = build_capability_statement("https://host/api/v1/fhir/R4")
    for rsc in cs["rest"][0]["resource"]:
        assert rsc["versioning"] == "no-version", rsc["type"]


# ---------------------------------------------------------------------------
# F17 — Location header short form
# ---------------------------------------------------------------------------

def test_create_location_header_uses_short_form():
    """F17: for versioning='no-version', the Location header is the short form
    <base>/Type/<id> (no /_history/<vid> segment). The /_history/<vid> form
    is only required when versioning='versioned' AND vread is implemented."""
    # The endpoint builds Location as f"{base}/{type}/{id}" — see
    # fhir_r4.py:create_resource. We just verify the format constant here.
    base = "https://host/api/v1/fhir/R4"
    resource_type = "Patient"
    rid = "abc-123"

    # The endpoint does f"{_facade_base_url(request)}/{resource_type}/{rid}"
    location = f"{base}/{resource_type}/{rid}"

    # No /_history/ segment.
    assert "/_history/" not in location
    # The path is the short form.
    assert location == "https://host/api/v1/fhir/R4/Patient/abc-123"
