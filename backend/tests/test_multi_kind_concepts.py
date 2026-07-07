"""Tests for the multi-kind concept model (ConceptKindTag join table).

Covers behavior introduced by migration ``9a3f7c2e1b4d``:
- a concept carries multiple kind tags and is queryable under each
- the (concept_id, kind) pair is unique
- cascade delete removes a concept's tags
- ``Concept.kinds`` returns all tag values; ``to_dict`` exposes both
  ``kinds`` and ``primary_kind``
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.concept_model import Concept, ConceptKindTag
from app.models.enums import ConceptKind, ConceptStatus


@pytest_asyncio.fixture
async def tenant_id():
    from app.models.tenant_model import TenantModel

    tid = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tid, name="MultiKind Tenant", slug=f"mk-{tid}"))
        await session.commit()
    return tid


def _slug(label: str = "") -> str:
    return f"mk-{label}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_concept_with_multiple_kind_tags(tenant_id):
    """A single concept can carry several kind tags and report them all."""
    slug = _slug("blood-lab")
    async with AsyncSessionLocal() as session:
        concept = Concept(
            slug=slug,
            name="Blood Laboratory",
            primary_kind=ConceptKind.EXAMINATION_CATEGORY,
            status=ConceptStatus.ACTIVE,
        )
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.EXAMINATION_CATEGORY))
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.BIOMARKER_CLASS))
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.DOCUMENT_CATEGORY))
        session.add(concept)
        await session.commit()

        fetched = await session.get(Concept, concept.id)
        assert set(fetched.kinds) == {
            "examination_category",
            "biomarker_class",
            "document_category",
        }
        assert fetched.primary_kind == ConceptKind.EXAMINATION_CATEGORY


@pytest.mark.asyncio
async def test_query_by_kind_tag_returns_concept_under_each_kind(tenant_id):
    """A concept tagged with N kinds is returned once for each kind filter."""
    slug = _slug("multi")
    async with AsyncSessionLocal() as session:
        concept = Concept(
            slug=slug,
            name="Multi",
            primary_kind=ConceptKind.SPECIALTY,
            status=ConceptStatus.ACTIVE,
        )
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.SPECIALTY))
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.EXAMINATION_CATEGORY))
        session.add(concept)
        await session.commit()

        for target in (ConceptKind.SPECIALTY, ConceptKind.EXAMINATION_CATEGORY):
            ids = (
                (
                    await session.execute(
                        select(Concept.id).where(
                            Concept.id.in_(
                                select(ConceptKindTag.concept_id).where(
                                    ConceptKindTag.kind == target
                                )
                            ),
                            Concept.slug == slug,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert [concept.id] == ids


@pytest.mark.asyncio
async def test_unique_constraint_concept_id_kind(tenant_id):
    """The (concept_id, kind) pair is unique — duplicate tag insert raises."""
    slug = _slug("uniq")
    async with AsyncSessionLocal() as session:
        concept = Concept(
            slug=slug,
            name="Uniq",
            primary_kind=ConceptKind.DISEASE,
            status=ConceptStatus.ACTIVE,
        )
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.DISEASE))
        session.add(concept)
        await session.commit()

        dup = ConceptKindTag(concept_id=concept.id, kind=ConceptKind.DISEASE)
        session.add(dup)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_cascade_delete_removes_tags(tenant_id):
    """Deleting a concept cascades to its kind_tags rows."""
    slug = _slug("cascade")
    async with AsyncSessionLocal() as session:
        concept = Concept(
            slug=slug,
            name="Cascade",
            primary_kind=ConceptKind.LIFESTYLE,
            status=ConceptStatus.ACTIVE,
        )
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.LIFESTYLE))
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.FACTOR))
        session.add(concept)
        await session.commit()
        cid = concept.id

        await session.delete(concept)
        await session.commit()

        remaining = (
            (
                await session.execute(
                    select(ConceptKindTag).where(ConceptKindTag.concept_id == cid)
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []


@pytest.mark.asyncio
async def test_to_dict_includes_kinds_and_primary_kind(tenant_id):
    """to_dict exposes both the kinds list and the denormalized primary_kind."""
    slug = _slug("dict")
    async with AsyncSessionLocal() as session:
        concept = Concept(
            slug=slug,
            name="Dict",
            primary_kind=ConceptKind.BIOMARKER_CLASS,
            status=ConceptStatus.ACTIVE,
        )
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.BIOMARKER_CLASS))
        concept.kind_tags.append(ConceptKindTag(kind=ConceptKind.BIOMARKER_PANEL))
        session.add(concept)
        await session.commit()

        d = concept.to_dict()
        assert d["primary_kind"] == "biomarker_class"
        assert sorted(d["kinds"]) == ["biomarker_class", "biomarker_panel"]
        # Legacy single-kind key is gone.
        assert "kind" not in d


@pytest.mark.asyncio
async def test_concept_without_kind_tags(tenant_id):
    """A concept may carry no kind tags (primary_kind still settable)."""
    slug = _slug("bare")
    async with AsyncSessionLocal() as session:
        concept = Concept(
            slug=slug,
            name="Bare",
            primary_kind=ConceptKind.SYMPTOM,
            status=ConceptStatus.ACTIVE,
        )
        session.add(concept)
        await session.commit()

        fetched = await session.get(Concept, concept.id)
        # Explicitly load the relationship in the async context (the concept
        # was created without ever touching kind_tags, so the first access
        # would otherwise lazy-load outside an awaited path).
        await session.refresh(fetched, ["kind_tags"])
        assert fetched.kinds == []
        assert fetched.primary_kind == ConceptKind.SYMPTOM
        assert fetched.to_dict()["kinds"] == []
