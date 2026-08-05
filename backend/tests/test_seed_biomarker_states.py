"""Seed test for the canonical ``biomarker_states`` catalog
(plan Step 3). Confirms the seed loads all expected states, is idempotent,
and reconciles mutable fields on re-run.
"""
import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerState
from app.services.seed_service import seed_service

# The minimum expected canonical states (subset of the full catalog). The
# seed file is the source of truth — this guards against accidental deletion
# of core clinical codes.
EXPECTED_CORE_STATES = {
    # Microbiology / serology
    ("POS", "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"),
    ("NEG", "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"),
    ("IND", "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"),
    # SNOMED
    ("260373001", "http://snomed.info/sct"),  # Detected
    ("260415000", "http://snomed.info/sct"),  # Not detected
    # AST
    ("S", "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"),
    ("R", "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"),
    # DataAbsentReason
    ("unknown", "http://terminology.hl7.org/CodeSystem/data-absent-reason"),
    ("not-performed", "http://terminology.hl7.org/CodeSystem/data-absent-reason"),
}


@pytest.mark.asyncio
async def test_seed_biomarker_states_loads_catalog():
    """Seeding populates the canonical state set."""
    stats = await seed_service.seed_biomarker_states()
    assert stats["errors"] == 0, f"seed errors: {stats}"
    # Either added (fresh DB) or updated (idempotent re-run). Total touched
    # should be ≥ the seed file size.
    assert stats["added"] + stats["updated"] >= 22

    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(BiomarkerState.code, BiomarkerState.system)
                )
            )
            .all()
        )
        present = {(r[0], r[1]) for r in rows}
        missing = EXPECTED_CORE_STATES - present
        assert not missing, f"Missing canonical states: {missing}"


@pytest.mark.asyncio
async def test_seed_biomarker_states_is_idempotent():
    """Running twice doesn't add duplicates; all rows are 'updated' on the
    second pass."""
    await seed_service.seed_biomarker_states()  # ensure populated
    second = await seed_service.seed_biomarker_states()
    assert second["added"] == 0, f"second run should not add rows: {second}"
    assert second["errors"] == 0
    assert second["updated"] >= 22

    async with AsyncSessionLocal() as session:
        total = (
            await session.execute(text("SELECT COUNT(*) FROM biomarker_states"))
        ).scalar()
        # Each (code, system) appears exactly once.
        assert total == second["updated"], (
            f"row count {total} != updated count {second['updated']} — duplicate inserts?"
        )


@pytest.mark.asyncio
async def test_seed_reconciles_mutable_fields():
    """Re-running the seed reconciles display/description/sort_order to the
    JSON — editing the seed and re-seeding is the normal evolution path."""
    async with AsyncSessionLocal() as session:
        # Mutate a row away from the seed value
        row = (
            await session.execute(
                select(BiomarkerState).where(
                    BiomarkerState.slug == "positive"
                )
            )
        ).scalar_one()
        original_display = row.display
        row.display = "__mutated_for_test__"
        await session.commit()

    # Re-seed → the row should be reconciled back to the seed value
    await seed_service.seed_biomarker_states()

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(BiomarkerState).where(
                    BiomarkerState.slug == "positive"
                )
            )
        ).scalar_one()
        assert row.display == original_display, (
            f"seed did not reconcile display; got {row.display!r}, "
            f"expected {original_display!r}"
        )
