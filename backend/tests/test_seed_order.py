"""Seed-order regression test.

`seed_all` must seed ``concepts`` (the ``anatomy_class`` / ``biomarker_class``
taxonomy) BEFORE the stages that resolve a ``class_concept_slug`` to a
``class_concept_id``. An earlier ordering ran ``body_parts`` before ``concepts``,
so on a fresh DB every anatomy row got ``class_concept_id = NULL`` (the slug
could not resolve). This test guards the dependency order end-to-end.
"""

import pytest
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.anatomy_model import AnatomyStructure
from app.services.seed_service import SeedService


@pytest.mark.asyncio
async def test_seed_all_resolves_anatomy_class(tmp_path, monkeypatch):
    """Every seeded anatomy structure must resolve its anatomy_class concept."""
    # seed_all() includes seed_anatomy_figures, which writes images under
    # settings.UPLOAD_DIR. The production default (/var/healthassistant/uploads)
    # is not writable in CI, so point it at a tmp dir for the test.
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    # Reset anatomy_structures so the test is deterministic regardless of
    # accumulated state from prior tests (the shared test DB is not cleaned
    # between runs). seed_all re-inserts every row and resolves its
    # class_concept_id against the (idempotent) concept catalog.
    from sqlalchemy import delete

    async with AsyncSessionLocal() as db:
        await db.execute(delete(AnatomyStructure))
        await db.commit()

    await SeedService().seed_all()
    async with AsyncSessionLocal() as db:
        total = (
            await db.execute(select(func.count()).select_from(AnatomyStructure))
        ).scalar()
        with_class = (
            await db.execute(
                select(func.count())
                .select_from(AnatomyStructure)
                .where(AnatomyStructure.class_concept_id.isnot(None))
            )
        ).scalar()
        thyroid = (
            await db.execute(
                select(AnatomyStructure).where(AnatomyStructure.slug == "thyroid")
            )
        ).scalar_one()

    assert total > 0
    assert with_class == total, (
        f"only {with_class}/{total} anatomy rows resolved a class_concept_id "
        "— concepts must seed before body_parts"
    )
    assert thyroid.class_concept_id is not None
    assert thyroid.class_concept.slug == "organ"
    assert thyroid.class_concept.name == "Organ"
