"""Regression tests for F9: FHIR token / JSONB search semantics.

The audit identified three sub-bugs:

1. ``code`` filter inspected only ``coding[0]`` — multi-coding resources
   (e.g. an Observation with a LOINC + a SNOMED coding) silently missed.
2. ``category`` filter compared ``model.category.astext == value`` but
   ``Observation.category`` is a **list** of CodeableConcepts (astext of an
   array ≠ scalar). No Observation ever matched a category filter.
3. Token modifiers (``:not``, ``:above``, ``:below``, ``:in``, ``:text``)
   not implemented.

The fix uses the PostgreSQL JSONB ``@>`` containment operator so a
CodeableConcept column matches if the supplied fragment appears anywhere
in its structure. ``is_list=True`` wraps the fragment for list-of-
CodeableConcept columns (``Observation.category``,
``Communication.category``, etc.).

These tests validate the predicate construction against the PostgreSQL
dialect (compile with ``literal_binds=True``); integration tests against
a live PG instance cover actual execution semantics.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import Column
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from app.facade.crud import _build_resource_filter, _jsonb_codeable_concept_match


def _literal_sql(predicate) -> str:
    """Compile a SQLAlchemy predicate with literal binds (PostgreSQL dialect)."""
    return str(
        predicate.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


# ---------------------------------------------------------------------------
# _jsonb_codeable_concept_match — predicate construction
# ---------------------------------------------------------------------------

@pytest.fixture
def cc_column():
    """A single CodeableConcept JSONB column (Observation.code shape)."""
    return Column("code", JSONB)


@pytest.fixture
def cc_list_column():
    """A list-of-CodeableConcept JSONB column (Observation.category shape)."""
    return Column("category", JSONB)


def test_bare_code_match_uses_containment(cc_column):
    """The predicate must use @> (containment), not coding[0] path equality."""
    pred = _jsonb_codeable_concept_match(cc_column, "1234-5", is_list=False)
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    # The fragment must be a coding-block, not a bare string.
    assert '"coding"' in compiled
    assert '"1234-5"' in compiled


def test_system_pipe_code_match(cc_column):
    """``code=http://loinc.org|1234-5`` must include both system and code."""
    pred = _jsonb_codeable_concept_match(
        cc_column, "http://loinc.org|1234-5", is_list=False
    )
    compiled = _literal_sql(pred)
    assert '"http://loinc.org"' in compiled
    assert '"1234-5"' in compiled


def test_list_match_wraps_fragment_in_list(cc_list_column):
    """``is_list=True`` must emit a list-wrapped fragment so the @> matches
    any element of the stored list."""
    pred = _jsonb_codeable_concept_match(cc_list_column, "vital-signs", is_list=True)
    compiled = _literal_sql(pred)
    # The fragment must be a JSON array ([{...}]), not a bare object ({...}).
    assert "'[{\"coding\"" in compiled.replace("\\", "") or '[{"coding"' in compiled


def test_non_jsonb_column_returns_none():
    """If the column isn't JSONB (e.g. a scalar enum column), the helper
    returns None so the caller can fall back to scalar equality."""
    from sqlalchemy import String

    scalar_col = Column("status", String)
    pred = _jsonb_codeable_concept_match(scalar_col, "active", is_list=False)
    assert pred is None


# ---------------------------------------------------------------------------
# _build_resource_filter — code branch (multi-coding support)
# ---------------------------------------------------------------------------

class _FakeObservation:
    """Stub ORM model with a JSONB code column for testing the dispatcher."""
    code = Column("code", JSONB)


def test_build_resource_filter_code_bare_token():
    """``code=1234-5`` builds an @> containment predicate against model.code."""
    pred = _build_resource_filter(_FakeObservation, "code", "1234-5")
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    assert '"1234-5"' in compiled
    # The bug previously emitted a path-equality on coding[0]:
    assert "coding" not in compiled.split("@>")[0]  # left side is the column, not a path


def test_build_resource_filter_code_system_pipe():
    """``code=http://loinc.org|1234-5`` builds a system+code containment."""
    pred = _build_resource_filter(_FakeObservation, "code", "http://loinc.org|1234-5")
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    assert '"http://loinc.org"' in compiled
    assert '"1234-5"' in compiled


def test_build_resource_filter_code_not_modifier():
    """``code:not=1234-5`` negates the match."""
    pred = _build_resource_filter(_FakeObservation, "code:not", "1234-5")
    compiled = _literal_sql(pred)
    # Negation produces NOT (...). The literal SQL wraps the @> in NOT (...).
    assert "NOT" in compiled.upper()
    assert "@>" in compiled


def test_build_resource_filter_code_missing_column_returns_none():
    """If the model has no `code` column, the dispatcher returns None."""

    class _NoCode:
        pass

    assert _build_resource_filter(_NoCode, "code", "1234-5") is None


# ---------------------------------------------------------------------------
# _build_resource_filter — category branch (list-of-CodeableConcept)
# ---------------------------------------------------------------------------

class _FakeObservationCategory:
    """Observation-style model: category is a JSONB list of CodeableConcept."""
    category = Column("category", JSONB)


class _FakeAllergyCategory:
    """AllergyIntolerance-style model: category is a scalar enum column."""
    from sqlalchemy import Enum as SqlEnum

    category = Column("category", SqlEnum)


def test_build_resource_filter_category_jsonb_list_uses_containment():
    """``category=vital-signs`` against a JSONB list column must use @>
    containment (F9 fix — previously ``model.category.astext == value``
    compared the whole list as a scalar string and matched nothing)."""
    pred = _build_resource_filter(_FakeObservationCategory, "category", "vital-signs")
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    # The fragment is list-wrapped ([{"coding":[{"code":"vital-signs"}]}]).
    assert '"vital-signs"' in compiled


def test_build_resource_filter_category_jsonb_list_system_pipe():
    pred = _build_resource_filter(
        _FakeObservationCategory,
        "category",
        "http://hl7.org/fhir/observation-category|vital-signs",
    )
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    assert '"vital-signs"' in compiled
    assert '"http://hl7.org/fhir/observation-category"' in compiled


def test_build_resource_filter_category_scalar_enum_uses_equality():
    """AllergyIntolerance.category is a PG enum (scalar). The dispatcher must
    use direct equality (case-insensitive), not @> containment."""
    pred = _build_resource_filter(_FakeAllergyCategory, "category", "food")
    compiled = _literal_sql(pred)
    # The bug would have emitted category.astext == 'food' (wrong for lists);
    # the new code emits a scalar equality with upper-case fallback.
    assert "food" in compiled.lower()
    # No @> containment on the scalar enum path.
    assert "@>" not in compiled


def test_build_resource_filter_category_not_modifier():
    """``category:not=vital-signs`` negates the match."""
    pred = _build_resource_filter(
        _FakeObservationCategory, "category:not", "vital-signs"
    )
    compiled = _literal_sql(pred)
    assert "NOT" in compiled.upper()
    assert "@>" in compiled


# ---------------------------------------------------------------------------
# type branch (DocumentReference.type / DiagnosticReport.type)
# ---------------------------------------------------------------------------

class _FakeDocRef:
    type = Column("type", JSONB)


def test_build_resource_filter_type_uses_containment():
    """``type=loinc-type`` (DocumentReference/DiagnosticReport) uses @>
    containment on the type column."""
    pred = _build_resource_filter(_FakeDocRef, "type", "loinc-code")
    compiled = _literal_sql(pred)
    assert "@>" in compiled
    assert '"loinc-code"' in compiled


def test_build_resource_filter_type_not_modifier():
    pred = _build_resource_filter(_FakeDocRef, "type:not", "loinc-code")
    compiled = _literal_sql(pred)
    assert "NOT" in compiled.upper()


# ---------------------------------------------------------------------------
# Fragment shape sanity (multi-coding capability)
# ---------------------------------------------------------------------------

def test_fragment_does_not_pin_coding_index():
    """The headline F9 bug: the previous predicate pinned coding[0]. The new
    fragment is a free-floating coding block under @> containment, so the
    match works against any element of the coding list (coding[0], coding[1],
    coding[2], ...). Verify the compiled SQL doesn't reference a specific
    array index on the column side."""
    pred = _build_resource_filter(_FakeObservation, "code", "1234-5")
    compiled = _literal_sql(pred)
    # The left side of @> should be just the column name `code`, not
    # `code -> 'coding' -> 0`. (The path-extraction form pins coding[0].)
    left_of_at = compiled.split("@>")[0]
    # Strip whitespace and check there's no path traversal on the column.
    left_normalized = " ".join(left_of_at.split())
    # The left side should end with the bare column reference `"code"` —
    # no `->` JSON-path operator on the column side.
    assert "->" not in left_normalized, (
        f"Predicate left-side uses path extraction (pins a coding index): {left_normalized}"
    )
