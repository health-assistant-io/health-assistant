"""Tests for concept seeding idempotency."""

import pytest


@pytest.mark.asyncio
async def test_seed_concepts_idempotent():
    """Running seed_concepts twice is idempotent (upsert by slug)."""
    from app.services.seed_service import SeedService

    svc = SeedService()
    stats1 = await svc.seed_concepts()
    total1 = stats1["added"] + stats1["updated"]
    assert total1 > 0, f"First run should add or update concepts: {stats1}"
    assert stats1["errors"] == 0, f"First run had errors: {stats1}"

    stats2 = await svc.seed_concepts()
    assert stats2["added"] == 0, f"Second run should add nothing: {stats2}"
    assert stats2["updated"] == total1, "Second run should update all"
    assert stats2["errors"] == 0


@pytest.mark.asyncio
async def test_seed_concept_edges_idempotent():
    """Running seed_concept_edges twice doesn't duplicate edges in the DB."""
    from app.services.seed_service import SeedService
    from app.core.database import AsyncSessionLocal
    from app.models.concept_model import ConceptEdge
    from sqlalchemy import select, func

    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_concept_edges()

    async with AsyncSessionLocal() as session:
        count_after_first = await session.scalar(
            select(func.count())
            .select_from(ConceptEdge)
            .where(ConceptEdge.tenant_id.is_(None))
        )

    await svc.seed_concept_edges()

    async with AsyncSessionLocal() as session:
        count_after_second = await session.scalar(
            select(func.count())
            .select_from(ConceptEdge)
            .where(ConceptEdge.tenant_id.is_(None))
        )

    assert count_after_first == count_after_second, (
        f"Edge count changed: {count_after_first} -> {count_after_second}"
    )
    assert count_after_first > 0, "Should have seeded edges"


@pytest.mark.asyncio
async def test_seed_concepts_reconciles_kinds_on_existing_row():
    """Re-seeding a concept whose `kinds` changed in the JSON must update the
    kind tags on the existing row — not silently skip them (the drift bug)."""
    from app.services.seed_service import SeedService
    from app.core.database import AsyncSessionLocal
    from app.models.concept_model import Concept, ConceptKindTag
    from app.models.enums import ConceptKind
    from sqlalchemy import select

    svc = SeedService()
    # First seed: runs the full file (concepts.json). Take a known concept.
    await svc.seed_concepts()

    async with AsyncSessionLocal() as session:
        # Pick a concept that exists and mutate its kind_tags to a known-wrong
        # state, then re-seed with the file's kinds and assert reconciliation.
        cardiology = (
            await session.execute(select(Concept).where(Concept.slug == "cardiology"))
        ).scalar_one()
        # Force it to a single tag that the JSON doesn't agree with.
        for t in list(cardiology.kind_tags):
            cardiology.kind_tags.remove(t)  # cascade delete-orphan handles the row
        await session.flush()
        cardiology.kind_tags.append(ConceptKindTag(kind=ConceptKind.DISEASE))
        cardiology.primary_kind = ConceptKind.DISEASE
        await session.commit()

    # Re-seed: should diff DISEASE away and restore the file's kinds.
    await svc.seed_concepts()

    async with AsyncSessionLocal() as session:
        cardiology = (
            await session.execute(select(Concept).where(Concept.slug == "cardiology"))
        ).scalar_one()
        await session.refresh(cardiology, ["kind_tags"])
        assert ConceptKind.DISEASE.value not in cardiology.kinds
        # cardiology in the seed file carries [specialty, examination_category].
        assert "specialty" in cardiology.kinds
        assert "examination_category" in cardiology.kinds
        assert cardiology.primary_kind != ConceptKind.DISEASE


@pytest.mark.asyncio
async def test_seed_concept_to_anatomy_edges():
    """Polymorphic seed edges: a specialty (concept) EXAMINES an organ
    (anatomy_structure), referenced by slug with dst_type='anatomy'. Verifies
    the edge is created with the right endpoint types and the organ is NOT
    duplicated into the concept table (single source of truth)."""
    from app.services.seed_service import SeedService
    from app.core.database import AsyncSessionLocal
    from app.models.concept_model import Concept, ConceptEdge
    from app.models.anatomy_model import AnatomyStructure
    from app.models.enums import EdgeEndpointType, ConceptRelationType
    from sqlalchemy import select

    svc = SeedService()
    await svc.seed_body_parts()  # creates heart, brain, …
    await svc.seed_concepts()  # creates cardiology, …
    await svc.seed_concept_edges()  # creates cardiology EXAMINES heart

    async with AsyncSessionLocal() as session:
        heart = (
            await session.execute(
                select(AnatomyStructure).where(AnatomyStructure.slug == "heart")
            )
        ).scalar_one()
        # The seed edge: cardiology (concept) -[EXAMINES]-> heart (anatomy)
        edge = (
            await session.execute(
                select(ConceptEdge).where(
                    ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                    ConceptEdge.dst_id == heart.id,
                    ConceptEdge.relation == ConceptRelationType.EXAMINES,
                    ConceptEdge.tenant_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        assert edge is not None, "cardiology -> heart EXAMINES edge should be seeded"
        assert edge.src_type == EdgeEndpointType.CONCEPT

        # Single source of truth: no 'heart' concept was created — the organ
        # lives only in anatomy_structures, referenced by the edge.
        heart_concept = (
            await session.execute(select(Concept).where(Concept.slug == "heart"))
        ).scalar_one_or_none()
        assert heart_concept is None, "anatomy must not be duplicated into concepts"


@pytest.mark.asyncio
async def test_seed_includes_all_expected_kinds():
    """Seed data covers at least the core domains."""
    from app.services.seed_service import SeedService

    svc = SeedService()
    await svc.seed_concepts()

    from app.core.database import AsyncSessionLocal
    from app.models.concept_model import Concept
    from app.models.enums import ConceptKind
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as session:
        from app.services.concept_service import concepts_with_kind

        for kind in [
            ConceptKind.SPECIALTY,
            ConceptKind.EXAMINATION_CATEGORY,
            ConceptKind.EVENT_CATEGORY,
            ConceptKind.BIOMARKER_CLASS,
            ConceptKind.BIOMARKER_PANEL,
            ConceptKind.ANATOMY_CLASS,
            ConceptKind.DOCUMENT_CATEGORY,
            ConceptKind.MEDICATION_CLASS,
            ConceptKind.BODY_SYSTEM,
        ]:
            count = await session.scalar(
                select(func.count())
                .select_from(Concept)
                .where(
                    concepts_with_kind(kind),
                    Concept.tenant_id.is_(None),
                    Concept.deleted_at.is_(None),
                )
            )
            assert count > 0, f"No seeded concepts for kind={kind.value}"


@pytest.mark.asyncio
async def test_seed_edges_create_graph():
    """Seeded edges connect specialties to body systems + exam categories."""
    from app.services.seed_service import SeedService

    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_concept_edges()

    from app.core.database import AsyncSessionLocal
    from app.models.concept_model import Concept, ConceptEdge
    from app.models.enums import ConceptRelationType
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        cardio = (
            await session.execute(
                select(Concept).where(
                    Concept.slug == "cardiology",
                )
            )
        ).scalar_one()

        examines = (
            (
                await session.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.src_id == cardio.id,
                        ConceptEdge.relation == ConceptRelationType.EXAMINES,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(examines) >= 1, "Cardiology should EXAMINES at least one body system"
