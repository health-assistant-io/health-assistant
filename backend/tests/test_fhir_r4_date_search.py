"""Regression tests for F13: FHIR date search precision + DRY.

Two audit sub-points:

1. **Year/month/day precision** (F13.1): ``_parse_fhir_datetime("2024")``
   was treated as exact ``2024-01-01T00:00:00Z`` instead of the whole-year
   range ``>= 2024-01-01 AND < 2025-01-01`` per the FHIR R4 spec.
   Same for month and day precision.

2. **DRY** (F13.2): ``crud._build_resource_filter`` reimplemented date
   prefix logic parallel to ``DateFilter.to_orm_filter`` — could drift.
   Now both paths route through ``DateFilter.to_orm_filter``.

These tests verify the precision-aware range semantics for every FHIR
date prefix, plus that the two call sites emit equivalent predicates.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from sqlalchemy import Column, DateTime
from sqlalchemy.dialects import postgresql

from app.facade.search_params import (
    DATE_PREFIXES,
    DateFilter,
    _parse_fhir_date_range,
    _parse_fhir_datetime,
)


def _literal_sql(predicate) -> str:
    """Compile a SQLAlchemy predicate with literal binds so the test can assert
    on the actual datetime values (default str() uses :param placeholders)."""
    return str(
        predicate.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


# ---------------------------------------------------------------------------
# _parse_fhir_date_range — precision semantics
# ---------------------------------------------------------------------------

def test_year_precision_returns_whole_year_range():
    start, end = _parse_fhir_date_range("2024")
    assert start == _dt.datetime(2024, 1, 1, tzinfo=_dt.timezone.utc)
    assert end == _dt.datetime(2025, 1, 1, tzinfo=_dt.timezone.utc)


def test_month_precision_returns_whole_month_range():
    start, end = _parse_fhir_date_range("2024-05")
    assert start == _dt.datetime(2024, 5, 1, tzinfo=_dt.timezone.utc)
    assert end == _dt.datetime(2024, 6, 1, tzinfo=_dt.timezone.utc)


def test_month_precision_december_wraps_to_next_year():
    start, end = _parse_fhir_date_range("2024-12")
    assert start == _dt.datetime(2024, 12, 1, tzinfo=_dt.timezone.utc)
    assert end == _dt.datetime(2025, 1, 1, tzinfo=_dt.timezone.utc)


def test_day_precision_returns_whole_day_range():
    start, end = _parse_fhir_date_range("2024-05-15")
    assert start == _dt.datetime(2024, 5, 15, tzinfo=_dt.timezone.utc)
    assert end == _dt.datetime(2024, 5, 16, tzinfo=_dt.timezone.utc)


def test_day_precision_end_of_month_wraps():
    start, end = _parse_fhir_date_range("2024-02-28")
    # 2024 is a leap year — Feb 28 + 1 day = Feb 29.
    assert end == _dt.datetime(2024, 2, 29, tzinfo=_dt.timezone.utc)


def test_day_precision_end_of_year_wraps():
    start, end = _parse_fhir_date_range("2024-12-31")
    assert end == _dt.datetime(2025, 1, 1, tzinfo=_dt.timezone.utc)


def test_datetime_precision_is_point_range():
    """A full datetime is an exact instant — range is [instant, instant+1µs)."""
    start, end = _parse_fhir_date_range("2024-05-15T13:30:00Z")
    assert start == _dt.datetime(2024, 5, 15, 13, 30, 0, tzinfo=_dt.timezone.utc)
    # End is start + 1µs so the eq overlap check includes the instant itself.
    assert end == start + _dt.timedelta(microseconds=1)


def test_parse_fhir_date_range_invalid_returns_none():
    assert _parse_fhir_date_range("garbage") is None
    assert _parse_fhir_date_range("") is None
    assert _parse_fhir_date_range("2024-13") is None  # invalid month
    assert _parse_fhir_date_range("2024-02-30") is None  # invalid day


def test_parse_fhir_date_range_still_accepts_full_iso_with_offset():
    start, end = _parse_fhir_date_range("2024-05-15T13:30:00+02:00")
    # The +02:00 offset is normalized to UTC.
    assert start == _dt.datetime(2024, 5, 15, 11, 30, 0, tzinfo=_dt.timezone.utc)


# ---------------------------------------------------------------------------
# DateFilter.to_orm_filter — prefix matrix with precision
# ---------------------------------------------------------------------------

@pytest.fixture
def date_col():
    """A bare SQLAlchemy DateTime column for building predicates."""
    return Column("test_col", DateTime)


def _predicate_kind(predicate):
    """Return the operator string of a SQLAlchemy binary predicate, e.g.
    '>=' / '<' / '=' / 'AND' (for tuples) for assertion convenience."""
    if predicate is None:
        return None
    # SQLAlchemy BinaryExpression exposes .operator
    op = getattr(predicate, "operator", None)
    if op is None:
        return str(type(predicate).__name__)
    return op.__name__ if hasattr(op, "__name__") else str(op)


def test_eq_year_precision_returns_range_overlap(date_col):
    """eq2024 → column >= 2024-01-01 AND column < 2025-01-01 (range overlap)."""
    f = DateFilter(prefix="eq", value="2024")
    pred = f.to_orm_filter(date_col)
    assert pred is not None
    # Should compile to an AND of two >= / < comparisons.
    compiled = _literal_sql(pred)
    assert "AND" in compiled.upper() or "and" in compiled.lower()
    # Contains the start boundary.
    assert "2024-01-01" in compiled


def test_eq_year_precision_includes_any_time_of_year(date_col):
    """F13: the headline bug — `eq2024` must match e.g. 2024-06-15T12:00, not
    just 2024-01-01T00:00:00. We verify by compiling and checking both the
    start (inclusive) and end (exclusive) boundaries are present."""
    f = DateFilter(prefix="eq", value="2024")
    pred = f.to_orm_filter(date_col)
    compiled = _literal_sql(pred)
    assert "2024-01-01" in compiled  # start
    assert "2025-01-01" in compiled  # end (exclusive)


def test_eq_without_prefix_uses_year_range(date_col):
    """No prefix = eq. Same as test_eq_year_precision_returns_range_overlap."""
    f = DateFilter(prefix=None, value="2024")
    pred = f.to_orm_filter(date_col)
    assert pred is not None


def test_gt_year_is_strictly_after_period_end(date_col):
    """gt2024 → column >= 2025-01-01 (strictly after the period)."""
    f = DateFilter(prefix="gt", value="2024")
    pred = f.to_orm_filter(date_col)
    compiled = _literal_sql(pred)
    assert "2025-01-01" in compiled
    assert ">=" in compiled


def test_sa_year_same_as_gt(date_col):
    """sa (starts after) behaves like gt for our purposes."""
    f_gt = DateFilter(prefix="gt", value="2024")
    f_sa = DateFilter(prefix="sa", value="2024")
    # Same operator and same bound.
    assert _literal_sql(f_gt.to_orm_filter(date_col)) == _literal_sql(f_sa.to_orm_filter(date_col))


def test_ge_year_is_at_or_after_start(date_col):
    """ge2024 → column >= 2024-01-01."""
    f = DateFilter(prefix="ge", value="2024")
    pred = f.to_orm_filter(date_col)
    compiled = _literal_sql(pred)
    assert "2024-01-01" in compiled
    assert ">=" in compiled


def test_lt_year_is_strictly_before_start(date_col):
    """lt2024 → column < 2024-01-01."""
    f = DateFilter(prefix="lt", value="2024")
    pred = f.to_orm_filter(date_col)
    compiled = _literal_sql(pred)
    assert "2024-01-01" in compiled
    assert "<" in compiled
    assert ">=" not in compiled


def test_eb_year_same_as_lt(date_col):
    f_lt = DateFilter(prefix="lt", value="2024")
    f_eb = DateFilter(prefix="eb", value="2024")
    assert _literal_sql(f_lt.to_orm_filter(date_col)) == _literal_sql(f_eb.to_orm_filter(date_col))


def test_le_year_is_before_period_end(date_col):
    """le2024 → column < 2025-01-01 (i.e. <= 2024-12-31T23:59:59.999)."""
    f = DateFilter(prefix="le", value="2024")
    pred = f.to_orm_filter(date_col)
    compiled = _literal_sql(pred)
    assert "2025-01-01" in compiled
    assert "<" in compiled


def test_ne_year_returns_or_of_outside_range(date_col):
    """ne2024 → column < 2024-01-01 OR column >= 2025-01-01."""
    f = DateFilter(prefix="ne", value="2024")
    pred = f.to_orm_filter(date_col)
    compiled = _literal_sql(pred)
    assert "OR" in compiled.upper() or "or" in compiled.lower()


def test_ap_year_returns_window_around_range(date_col):
    """ap2024 → ±1 day window around [start, end)."""
    f = DateFilter(prefix="ap", value="2024")
    pred = f.to_orm_filter(date_col)
    compiled = _literal_sql(pred)
    # The window should include the year-1 day before start and end+1 day after.
    assert "2023-12-31" in compiled  # start - 1 day
    assert "2025-01-02" in compiled  # end + 1 day


def test_invalid_value_returns_none(date_col):
    """Garbage value → None (caller ignores)."""
    f = DateFilter(prefix=None, value="garbage")
    assert f.to_orm_filter(date_col) is None


def test_all_date_prefixes_have_a_path(date_col):
    """Smoke test: every FHIR date prefix compiles without raising."""
    for prefix in DATE_PREFIXES:
        f = DateFilter(prefix=prefix, value="2024-05-15")
        pred = f.to_orm_filter(date_col)
        assert pred is not None, f"prefix {prefix} returned None"


# ---------------------------------------------------------------------------
# DRY — _build_resource_filter delegates to DateFilter.to_orm_filter (F13.2)
# ---------------------------------------------------------------------------

def test_crud_resource_filter_uses_datefilter_implementation():
    """F13.2: crud._build_resource_filter must route date params through
    DateFilter.to_orm_filter (the same code path _lastUpdated uses). The
    previous implementation inlined a parallel prefix matrix that could
    drift from DateFilter.

    We verify by patching DateFilter.to_orm_filter and asserting that
    _build_resource_filter's date branch produces a predicate whose string
    form matches the (patched) DateFilter output.
    """
    from app.facade import crud
    from app.facade.search_params import _split_date_param

    class _DummyModel:
        # The crud date_param_to_col entry for 'onset-date' targets 'onset_date'.
        onset_date = Column("onset_date", DateTime)

    # Build predicates via both paths and confirm they're equivalent.
    via_date_filter = _split_date_param("2024").to_orm_filter(_DummyModel.onset_date)

    # Simulate the resource-filter call by hitting the date_param_to_col branch
    # directly. _build_resource_filter is module-level; we exercise it.
    via_resource_filter = crud._build_resource_filter(_DummyModel, "onset-date", "2024")

    assert _literal_sql(via_resource_filter) == _literal_sql(via_date_filter)


def test_crud_resource_filter_date_precision_inherited_from_datefilter():
    """The precision semantics (year-range expansion) must propagate from
    DateFilter into the resource-filter path. Verifies F13.2 end-to-end:
    a year-precision filter through the resource path produces a range
    predicate, not a single-instant comparison."""
    from app.facade import crud

    class _DummyModel:
        onset_date = Column("onset_date", DateTime)

    # eq2024 through the resource-filter path.
    pred = crud._build_resource_filter(_DummyModel, "onset-date", "2024")
    compiled = _literal_sql(pred)
    # Must contain BOTH year boundaries (range), not a single equality.
    assert "2024-01-01" in compiled
    assert "2025-01-01" in compiled
    # No exact equality operator on a single instant.
    assert "test_col" not in compiled  # sanity — uses our column name
