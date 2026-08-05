"""Tests for the telemetry hard-block on STATE biomarkers (plan Step 11).

The Celery task ``migrate_biomarker_data`` is the defensive backstop. The
endpoint guard (Step 6) + the DB CHECK constraint (Step 1) already prevent
the toggle from being queued, but the task shouldn't trust its input. This
file invokes the underlying coroutine directly.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import (
    BiomarkerAllowedState,
    BiomarkerDefinition,
    BiomarkerState,
)
from app.models.enums import (
    BiomarkerValueType,
    CatalogScope,
    CodingSystem,
)


V3 = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


@pytest.fixture(autouse=True)
async def _scrub():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "DELETE FROM biomarker_allowed_states WHERE biomarker_id IN "
                "(SELECT id FROM biomarker_definitions WHERE slug LIKE 'state-tg-%')"
            )
        )
        await session.execute(
            text("DELETE FROM biomarker_definitions WHERE slug LIKE 'state-tg-%'")
        )
        await session.execute(
            text("DELETE FROM biomarker_states WHERE slug LIKE 'state-tg-%'")
        )
        await session.commit()


async def _seed_state_biomarker():
    """Create a STATE biomarker with POS/NEG allowed states.

    POS/NEG are looked up first (canonical seed catalog may already be
    loaded); falls back to insert under the state-tg-* slug namespace.
    """
    from sqlalchemy import select as sa_select

    async with AsyncSessionLocal() as session:
        async def _get_or_create_state(code, display):
            existing = (
                await session.execute(
                    sa_select(BiomarkerState).where(
                        BiomarkerState.code == code, BiomarkerState.system == V3
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            state = BiomarkerState(
                slug=f"state-tg-{code.lower()}-{uuid.uuid4().hex[:6]}",
                code=code,
                system=V3,
                display=display,
            )
            session.add(state)
            await session.flush()
            return state

        pos = await _get_or_create_state("POS", "Positive")
        neg = await _get_or_create_state("NEG", "Negative")
        bio = BiomarkerDefinition(
            slug=f"state-tg-bio-{uuid.uuid4().hex[:8]}",
            coding_system=CodingSystem.LOINC,
            name="PCR",
            aliases=[],
            is_telemetry=False,
            value_type=BiomarkerValueType.STATE,
            scope=CatalogScope.SYSTEM,
        )
        session.add(bio)
        await session.flush()
        session.add_all(
            [
                BiomarkerAllowedState(
                    biomarker_id=bio.id, state_id=pos.id, is_normal=False
                ),
                BiomarkerAllowedState(
                    biomarker_id=bio.id, state_id=neg.id, is_normal=True
                ),
            ]
        )
        await session.commit()
        return bio.id


@pytest.mark.asyncio
async def test_migrate_state_biomarker_to_telemetry_is_rejected():
    """Calling migrate_biomarker_data on a STATE biomarker with
    to_telemetry=True returns 'failed' rather than attempting the (impossible)
    numeric migration."""
    bio_id = await _seed_state_biomarker()
    tenant_id = uuid.uuid4()

    # Invoke the underlying async coroutine directly, bypassing the
    # @celery_app.task and @async_task wrappers (double __wrapped__ unwrap).
    from app.workers import tasks as worker_tasks

    raw_fn = worker_tasks.migrate_biomarker_data.__wrapped__.__wrapped__
    result = await raw_fn(None, str(bio_id), str(tenant_id), True)

    assert result["status"] == "failed"
    assert "STATE" in result["error"]

    # The biomarker's meta_data reflects the failure (visible in the UI).
    async with AsyncSessionLocal() as session:
        meta = (
            await session.execute(
                text(
                    "SELECT meta_data FROM biomarker_definitions WHERE id = :id"
                ),
                {"id": str(bio_id)},
            )
        ).scalar_one()
        assert meta["migration_status"] == "failed"
        assert "STATE" in meta["migration_error"]
        # And the biomarker itself is unchanged (still STATE, still not telemetry).
        row = (
            await session.execute(
                text(
                    "SELECT value_type, is_telemetry FROM biomarker_definitions WHERE id = :id"
                ),
                {"id": str(bio_id)},
            )
        ).one()
        assert row[0] == "state"
        assert row[1] is False
