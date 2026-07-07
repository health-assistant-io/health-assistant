"""Regression test: ExaminationResponse / ExaminationSummaryResponse must
serialize an ExaminationModel that has its ``category_concept`` relationship
populated.

Bug: after renaming ``category_entity`` → ``category_concept`` and adding the
ORM relationship, the response schema field was typed
``Optional[Dict[str, Any]]``. Pydantic cannot coerce a populated ORM
``Concept`` object into a bare ``Dict`` from ``from_attributes``, so
``POST /api/v1/examinations`` returned a 500
("Input should be a valid dictionary ... input: <Concept object>").

Fix: the field is typed ``Optional[ConceptResponse]`` (a Pydantic model with
``from_attributes=True``), so the ORM Concept is serialized properly. This
test pins that contract against a real DB so a regression to ``Dict[str, Any]``
(or to a type that can't read the ORM relationship) fails fast.
"""
import datetime
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.concept_model import Concept
from app.models.enums import ConceptKind
from app.models.examination_model import ExaminationModel


async def _make_tenant(db) -> uuid.UUID:
    from app.models.tenant_model import TenantModel

    tid = uuid.uuid4()
    db.add(TenantModel(id=tid, name="Resp Test", slug=f"resp-test-{tid}"))
    await db.commit()
    return tid


@pytest.mark.asyncio
async def test_examination_response_serializes_category_concept():
    """An exam whose category_concept is loaded must serialize via the
    ExaminationResponse schema without raising."""
    from app.core.database import AsyncSessionLocal
    from app.schemas.examination import ExaminationResponse

    async with AsyncSessionLocal() as db:
        tenant_id = await _make_tenant(db)
        # Pick any active concept tagged as an examination_category (seeded).
        concept = (
            await db.execute(
                select(Concept)
                .where(Concept.deleted_at.is_(None))
                .order_by(Concept.name.asc())
                .limit(1)
            )
        ).scalars().first()
        assert concept is not None, "test DB has no concepts to attach"

        exam = ExaminationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            notes="serialization regression",
            examination_date=datetime.date(2026, 7, 7),
            category_concept_id=concept.id,
        )
        db.add(exam)
        await db.commit()
        try:
            # Reload with the relationship populated (mirrors the endpoint's
            # post-commit selectinload reload).
            loaded = (
                await db.execute(
                    select(ExaminationModel)
                    .where(ExaminationModel.id == exam.id)
                    .options(selectinload(ExaminationModel.category_concept))
                )
            ).scalar_one()
            assert loaded.category_concept is not None

            # This is exactly what FastAPI does for response_model validation.
            resp = ExaminationResponse.model_validate(loaded)
            assert resp.category_concept is not None
            assert resp.category_concept.id == concept.id
            assert resp.category_concept.name == concept.name
            # category_concept_id carries through too.
            assert resp.category_concept_id == concept.id
        finally:
            await db.delete(exam)
            await db.commit()


@pytest.mark.asyncio
async def test_examination_response_handles_missing_category_concept():
    """An exam with no category must serialize with category_concept=None."""
    from app.core.database import AsyncSessionLocal
    from app.schemas.examination import ExaminationResponse

    async with AsyncSessionLocal() as db:
        tenant_id = await _make_tenant(db)
        exam = ExaminationModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            notes="no category",
            examination_date=datetime.date(2026, 7, 7),
            category_concept_id=None,
        )
        db.add(exam)
        await db.commit()
        try:
            loaded = (
                await db.execute(
                    select(ExaminationModel)
                    .where(ExaminationModel.id == exam.id)
                    .options(selectinload(ExaminationModel.category_concept))
                )
            ).scalar_one()
            resp = ExaminationResponse.model_validate(loaded)
            assert resp.category_concept is None
            assert resp.category_concept_id is None
        finally:
            await db.delete(exam)
            await db.commit()
