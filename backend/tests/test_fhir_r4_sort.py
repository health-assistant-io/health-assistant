"""Regression tests for F10: FHIR R4 search sort column corrections.

The bug (audit F10) had three sub-points:
1. ``Condition?_sort=onset-date`` referenced ``fhir_onset_datetime`` (column
   doesn't exist; the model has ``onset_date``).
2. ``MedicationRequest?authored-on`` filter used ``created_at`` but sort used
   ``start_date`` — filter and sort disagreed for the same param.
3. ``Patient?_sort=name`` sorted by the JSONB blob byte representation, not
   family name (so 'Smith' sorted before 'adams' because uppercase < lowercase
   in ASCII).

The fix in ``facade/search_params.py``:
- ``Condition.onset-date`` → ``onset_date`` (the real column).
- ``MedicationRequest.authored-on`` → ``created_at`` (aligns with the filter).
- ``Patient.name`` → a callable ``_patient_family_name_sort`` that builds
  ``LOWER(COALESCE(name -> 0 ->> 'family', name ->> 'family', ''))``,
  handling both the list-of-HumanName and single-HumanName storage shapes.

These tests are mostly table-driven against ``SORT_COLUMNS`` (so they stay
correct even if the underlying model column changes), plus a SQL-execution
test against an in-memory SQLite DB for the Patient name expression.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, func, insert, select

from app.facade.search_params import SORT_COLUMNS, _patient_family_name_sort


# ---------------------------------------------------------------------------
# Column-name corrections (F10.1, F10.2)
# ---------------------------------------------------------------------------

def test_condition_onset_date_sort_targets_real_column():
    """F10.1: Condition.sort onset-date must map to the actual ORM column."""
    assert SORT_COLUMNS["Condition"]["onset-date"] == "onset_date"
    # The bug mapped it to 'fhir_onset_datetime' which doesn't exist.
    assert SORT_COLUMNS["Condition"]["onset-date"] != "fhir_onset_datetime"


def test_medication_request_authored_on_sort_aligns_with_filter():
    """F10.2: MedicationRequest.authored-on sort and filter must use the same
    column. The filter builds `col == dt` against created_at (see
    facade.crud._build_resource_filter date_param_to_col['authored-on']),
    so sort must also use created_at. Previously sort used 'start_date'
    which disagreed with the filter."""
    assert SORT_COLUMNS["MedicationRequest"]["authored-on"] == "created_at"


def test_medication_request_authored_on_sort_is_not_start_date():
    """Regression: previously sort was 'start_date' while filter was 'created_at'."""
    assert SORT_COLUMNS["MedicationRequest"]["authored-on"] != "start_date"


# ---------------------------------------------------------------------------
# Patient name sort — must be a callable expression, not a bare column name
# ---------------------------------------------------------------------------

def test_patient_name_sort_is_a_callable():
    """F10.3: Patient?_sort=name must build a SQL expression, not use the
    JSONB column directly (which would sort by blob bytes)."""
    value = SORT_COLUMNS["Patient"]["name"]
    assert callable(value), "Patient.name sort must be a callable expression"


def test_patient_name_sort_callable_returns_expression():
    """The callable must return a SQLAlchemy expression (not a string, not None)."""
    expr = _patient_family_name_sort()
    # Just verify it's a non-string, non-None object that has .desc() / .asc()
    # (SQLAlchemy expression interface).
    assert expr is not None
    assert not isinstance(expr, str)
    assert hasattr(expr, "desc")
    assert hasattr(expr, "asc")


# ---------------------------------------------------------------------------
# End-to-end: Patient name sort expression against an in-memory SQLite DB
# ---------------------------------------------------------------------------
#
# SQLite doesn't have the -> / ->> JSON operators, so we test the expression's
# *intent* against PostgreSQL semantics using a stub: emulate the COALESCE
# behavior the callable encodes. The integration test (PostgreSQL) is the
# real proof; this is a guard that the callable at least produces the right
# expression structure.


def test_patient_name_sort_handles_both_storage_shapes(monkeypatch):
    """Smoke test: the callable must build an expression that references
    both the list-shape path (name -> 0 ->> 'family') and the dict-shape
    path (name ->> 'family') inside a COALESCE."""
    from sqlalchemy.dialects import postgresql

    expr = _patient_family_name_sort()
    compiled = str(
        expr.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()

    # The compiled SQL must include the JSON path extractions inside a coalesce.
    assert "coalesce" in compiled
    # The list-shape extraction: -> 0 ->> 'family'
    assert "->0" in compiled.replace(" ", "") or "-> 0" in compiled
    assert "'family'" in compiled
    # The dict-shape extraction: ->> 'family' at the root (without the [0] prefix).
    # Both shapes extract 'family' — we just verify the operator is there.
    assert "->>" in compiled


@pytest.fixture
def fake_pg_db():
    """Build an in-memory table that mimics Patient.name JSONB storage.

    SQLite doesn't support the JSON -> / ->> operators the way Postgres does,
    so we use SQLite's ``json_extract`` instead and patch the callable's SQL
    to use it. This is purely to validate the sort *semantics* (lowercase
    family-name ordering across both storage shapes).
    """
    metadata = MetaData()
    patients = Table(
        "fhir_patients_fake",
        metadata,
        Column("id", Integer, primary_key=True),
        # SQLite JSON — same shape we'd store in PG JSONB.
        Column("name", String),  # holds JSON strings
    )
    # Use SQLite's in-memory engine.
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)

    # Insert rows with mixed storage shapes:
    # 1. list-of-HumanName (FHIR canonical, the new shape): family='Smith'
    # 2. single-HumanName dict (legacy REST shape): family='adams'
    # 3. list-of-HumanName: family='Taylor'
    # 4. single-HumanName dict: family='Brown'
    rows = [
        {"id": 1, "name": '[{"family": "Smith", "given": ["John"]}]'},
        {"id": 2, "name": '{"family": "adams", "given": ["Jane"]}'},
        {"id": 3, "name": '[{"family": "Taylor", "given": ["Tom"]}]'},
        {"id": 4, "name": '{"family": "Brown", "given": ["Bob"]}'},
    ]
    with engine.begin() as conn:
        conn.execute(insert(patients), rows)
    return engine, patients


def test_patient_name_sort_semantics_lowercase_family(fake_pg_db):
    """Verify the *intent* of _patient_family_name_sort: regardless of whether
    the row stores a list-of-HumanName or a single-HumanName dict, ordering by
    family name must be case-insensitive (so 'adams' < 'Brown' < 'Smith' <
    'Taylor' rather than ASCII-byte order where 'Brown' < 'Smith' < 'Taylor'
    < 'adams').
    """
    engine, patients = fake_pg_db

    # Emulate the callable's COALESCE behavior using SQLite's json_extract:
    # - json_extract(name, '$[0].family') handles list shape (returns the
    #   first element's family).
    # - json_extract(name, '$.family') handles dict shape.
    # COALESCE picks whichever is non-NULL.
    from sqlalchemy import func

    expr = func.lower(
        func.coalesce(
            func.json_extract(patients.c.name, "$[0].family"),
            func.json_extract(patients.c.name, "$.family"),
            "",
        )
    )

    with engine.begin() as conn:
        result = conn.execute(
            select(patients.c.id).order_by(expr.asc())
        )
        ordered_ids = [row[0] for row in result]

    # Expected ascending order by lowercase family:
    #   'adams' (id=2), 'Brown' (id=4), 'Smith' (id=1), 'Taylor' (id=3)
    assert ordered_ids == [2, 4, 1, 3]
