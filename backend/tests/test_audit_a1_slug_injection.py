"""Tests for audit item A1 — second-order SQL injection via biomarker slug.

The biomarker ``slug`` is interpolated into raw SQL in the telemetry analytics
path (``analytics_service.get_biomarker_trends``). These tests verify the three
layers of defence:
1. Pydantic schema rejects unsafe slugs on the API write path.
2. ``sanitize_slug`` coerces arbitrary AI/import input into a safe slug.
3. ``analytics_service`` skips (never interpolates) any slug that is not a
   strict identifier, regardless of how it entered the DB.
"""
import uuid

import pytest
from pydantic import ValidationError

from app.schemas.biomarker import (
    BiomarkerBase,
    BiomarkerCreate,
    is_safe_slug,
    sanitize_slug,
)


class TestSafeSlugPredicate:
    def test_accepts_alphanumeric(self):
        assert is_safe_slug("glucose")
        assert is_safe_slug("8867-4")
        assert is_safe_slug("heart-rate")
        assert is_safe_slug("LDL_C")

    def test_accepts_underscore(self):
        assert is_safe_slug("blood_pressure")

    def test_rejects_empty(self):
        assert not is_safe_slug("")

    @pytest.mark.parametrize(
        "bad",
        [
            "x') UNION SELECT 1--",
            "x'; DROP TABLE users;--",
            "with space",
            "dot.dot",
            'quote"injected',
            "semi;colon",
            "back`tick",
            "unição",
            "x" * 81,
            "leading/slash",
        ],
    )
    def test_rejects_unsafe(self, bad):
        assert not is_safe_slug(bad), f"{bad!r} should be rejected"


class TestSanitizeSlug:
    def test_passes_through_clean_slug(self):
        assert sanitize_slug("glucose") == "glucose"
        assert sanitize_slug("8867-4") == "8867-4"

    def test_lowercases(self):
        assert sanitize_slug("LDL") == "ldl"

    def test_replaces_unsafe_run_with_single_hyphen(self):
        assert sanitize_slug("x') UNION SELECT 1--") == "x-union-select-1"

    def test_replaces_spaces(self):
        assert sanitize_slug("heart rate") == "heart-rate"

    def test_strips_leading_trailing_hyphens(self):
        assert sanitize_slug("---glucose---") == "glucose"

    def test_truncates_to_80(self):
        assert len(sanitize_slug("a" * 200)) == 80

    def test_empty_falls_back(self):
        assert sanitize_slug("") == "biomarker"
        assert sanitize_slug(",,,") == "biomarker"


class TestBiomarkerSchemaValidation:
    def test_clean_slug_accepted(self):
        b = BiomarkerCreate(slug="glucose", name="Glucose")
        assert b.slug == "glucose"

    def test_unsafe_slug_rejected(self):
        with pytest.raises(ValidationError):
            BiomarkerCreate(slug="x') UNION SELECT 1--", name="evil")

    def test_space_slug_rejected(self):
        with pytest.raises(ValidationError):
            BiomarkerCreate(slug="bad slug", name="x")

    def test_overlong_slug_rejected(self):
        with pytest.raises(ValidationError):
            BiomarkerCreate(slug="a" * 81, name="x")

    def test_loinc_code_slug_accepted(self):
        b = BiomarkerBase(slug="8867-4", name="Heart rate")
        assert b.slug == "8867-4"


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return _FakeScalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def keys(self):
        return []

    def fetchall(self):
        return []

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Captures every executed SQL string so we can assert no injection
    payload ever reaches the database."""

    def __init__(self, biomarker_rows):
        self._bio_rows = biomarker_rows
        self.executed_sql: list[str] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.executed_sql.append(sql)
        # The biomarker-definitions query joins BiomarkerDefinition + Unit and
        # returns (bio, symbol) rows. Everything else returns empty results.
        if "BiomarkerDefinition" in sql or "biomarker_definition" in sql.lower():
            return _FakeResult(self._bio_rows)
        return _FakeResult([])


class TestAnalyticsTelemetryGuard:
    """End-to-end: a telemetry biomarker with a malicious slug must never have
    its slug interpolated into the executed SQL."""

    @pytest.mark.asyncio
    async def test_evil_slug_not_interpolated_into_sql(self):
        from app.services.analytics_service import get_biomarker_trends

        tenant_id = str(uuid.uuid4())
        evil_payload = "x') UNION SELECT hashed_password FROM users--"

        # Build a fake biomarker-definition row flagged is_telemetry so the
        # telemetry branch is taken. The slug is an injection payload.
        class FakeBio:
            def __init__(self):
                self.id = uuid.uuid4()
                self.slug = evil_payload
                self.is_telemetry = True
                self.name = "evil"
                self.coding_system = "CUSTOM"
                self.code = None
                self.aliases: list = []
                self.reference_range_min = None
                self.reference_range_max = None
                self.preferred_unit_id = None
                self.class_concept_id = None
                self.info = None

        fake_bio = FakeBio()
        db = _FakeDB(biomarker_rows=[(fake_bio, None)])

        # Call the real function; it will reach the telemetry loop because the
        # biomarker is telemetry-flagged.
        result = await get_biomarker_trends(
            tenant_id=tenant_id,
            period="last-30-days",
            db=db,
        )

        # The injection payload must NEVER appear in any executed SQL.
        for sql in db.executed_sql:
            assert "UNION SELECT" not in sql, (
                f"SQL injection payload leaked into executed SQL: {sql!r}"
            )
            assert "hashed_password" not in sql
            assert evil_payload not in sql

        # Result is still well-formed (no crash).
        assert "biomarkers" in result
