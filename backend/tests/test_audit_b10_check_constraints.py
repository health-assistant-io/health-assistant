"""Regression tests for audit B10 — obvious CHECK constraints.

Three layers of coverage:
1. The ORM metadata on each model declares the expected ``CheckConstraint``
   (keeps ``alembic autogenerate`` in sync with the migration).
2. The constraints actually exist in the live DB (the migration ran).
3. Behavioural spot-checks on the two FK-free tables (``units`` and
   ``biomarker_definitions``) prove enforcement rejects bad rows and accepts
   good ones. The remaining tables (documents/examinations/ai_models/jobs)
   carry heavy FK scaffolding; the metadata + DB-existence checks cover them.
"""
import uuid

import pytest
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.base import Base

EXPECTED = {
    "ck_units_positive_conversion_multiplier",
    "ck_biomarker_definitions_ref_range_order",
    "ck_examinations_extraction_progress_bounds",
    "ck_documents_progress_bounds",
    "ck_export_jobs_progress_bounds",
    "ck_import_jobs_progress_bounds",
    "ck_ai_models_positive_max_tokens",
    "ck_ai_models_temperature_bounds",
}


def test_models_declare_check_constraints():
    """Every B10 constraint is declared on a model's ``__table_args__``."""
    found = set()
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if (
                isinstance(constraint, CheckConstraint)
                and constraint.name in EXPECTED
            ):
                found.add(constraint.name)
    missing = EXPECTED - found
    assert not missing, f"Model metadata missing constraints: {missing}"


@pytest.mark.asyncio
async def test_constraints_exist_in_db():
    """The migration created every B10 constraint in the live DB."""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text("SELECT conname FROM pg_constraint WHERE conname LIKE 'ck\\_%'")
            )
        ).scalars().all()
    missing = EXPECTED - set(rows)
    assert not missing, f"DB missing constraints: {missing}"


@pytest.mark.asyncio
async def test_units_conversion_multiplier_must_be_positive():
    """A non-positive conversion_multiplier is rejected; a positive one is not."""
    good_symbol = f"b10-pos-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        # Violating insert must fail (inside a savepoint so the session survives).
        with pytest.raises(IntegrityError) as exc_info:
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO units (id, symbol, name, quantity_type, conversion_multiplier) "
                        "VALUES (gen_random_uuid(), :sym, 'neg', 'OTHER', 0)"
                    ),
                    {"sym": f"b10-neg-{uuid.uuid4().hex[:8]}"},
                )
        assert "ck_units_positive_conversion_multiplier" in str(exc_info.value)

        # Valid insert must succeed and be cleanable.
        await session.execute(
            text(
                "INSERT INTO units (id, symbol, name, quantity_type, conversion_multiplier) "
                "VALUES (gen_random_uuid(), :sym, 'pos', 'OTHER', 2.5)"
            ),
            {"sym": good_symbol},
        )
        await session.commit()
        # Clean up so the unique symbol doesn't leak across tests.
        await session.execute(text("DELETE FROM units WHERE symbol = :sym"), {"sym": good_symbol})
        await session.commit()


@pytest.mark.asyncio
async def test_biomarker_reference_range_order_enforced():
    """An inverted reference range (min > max) is rejected; ordered/NULL are not."""
    good_slug = f"b10-good-{uuid.uuid4().hex[:8]}"
    # Raw SQL doesn't apply ORM Python defaults, so supply every NOT NULL
    # column explicitly (id/slug/name/coding_system/aliases/scope). The ONLY
    # reason the inverted insert may fail is the ref-range check constraint.
    inv_sql = (
        "INSERT INTO biomarker_definitions "
        "(id, slug, name, coding_system, aliases, is_telemetry, scope, "
        " reference_range_min, reference_range_max) "
        "VALUES (gen_random_uuid(), :slug, 'inv', 'CUSTOM', '[]', false, 'system', 10, 5)"
    )
    good_sql = (
        "INSERT INTO biomarker_definitions "
        "(id, slug, name, coding_system, aliases, is_telemetry, scope, "
        " reference_range_min, reference_range_max) "
        "VALUES (gen_random_uuid(), :slug, 'good', 'CUSTOM', '[]', false, 'system', 5, 10)"
    )
    async with AsyncSessionLocal() as session:
        with pytest.raises(IntegrityError) as exc_info:
            async with session.begin_nested():
                await session.execute(
                    text(inv_sql),
                    {"slug": f"b10-inv-{uuid.uuid4().hex[:8]}"},
                )
        # The failure must be the check constraint, not some other integrity issue.
        assert "ck_biomarker_definitions_ref_range_order" in str(exc_info.value)

        # Ordered range is fine.
        await session.execute(text(good_sql), {"slug": good_slug})
        await session.commit()
        await session.execute(
            text("DELETE FROM biomarker_definitions WHERE slug = :slug"), {"slug": good_slug}
        )
        await session.commit()
