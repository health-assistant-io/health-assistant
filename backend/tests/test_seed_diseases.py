"""Phase 6 tests — disease concepts seeded as ``kind=disease`` with ICD-10 codes.

Covers:
- Disease concepts exist after ``seed_diseases`` runs (kind=disease, coding_system=icd10,
  non-empty code).
- Idempotency: running twice does not duplicate or add rows.
- The vaccine-target disease slugs are present (so PREVENTS edges can resolve in
  ``test_disease_edges``).

Assertions are scoped to the seed file's slugs so they stay deterministic even
though the shared test DB accumulates disease-tagged concepts from other tests.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.concept_model import Concept
from app.models.enums import ConceptKind
from app.services.concept_service import concepts_with_kind
from app.services.seed_service import SeedService


def _seed_items():
    path = Path(__file__).parent.parent / "data" / "seeds" / "diseases.json"
    return json.load(open(path))["items"]


def _seed_slugs():
    return {i["slug"] for i in _seed_items()}


@pytest.mark.asyncio
async def test_seed_diseases_creates_disease_concepts():
    """``seed_diseases`` upserts every disease concept from the seed file."""
    svc = SeedService()
    # Diseases depend on the concepts stage having run (parent_slug refs etc.),
    # but diseases.json has no parent_slug refs, so it can run standalone. Run
    # concepts first anyway to mirror the real pipeline ordering.
    await svc.seed_concepts()
    stats = await svc.seed_diseases()

    items = _seed_items()
    assert stats["added"] + stats["updated"] == len(items), stats
    assert stats["errors"] == 0, stats

    slugs = _seed_slugs()
    async with AsyncSessionLocal() as session:
        found = (
            (
                await session.execute(
                    select(Concept.slug).where(
                        Concept.slug.in_(slugs),
                        Concept.tenant_id.is_(None),
                        Concept.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert set(found) == slugs, f"missing: {slugs - set(found)}"


@pytest.mark.asyncio
async def test_seed_diseases_has_icd10_codes():
    """Every seeded disease concept carries a coding_system + code."""
    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_diseases()

    slugs = _seed_slugs()
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Concept.slug, Concept.coding_system, Concept.code).where(
                    Concept.slug.in_(slugs),
                    Concept.tenant_id.is_(None),
                    Concept.deleted_at.is_(None),
                )
            )
        ).all()

    assert rows, "no disease concepts seeded"
    found_slugs = {r[0] for r in rows}
    assert found_slugs == slugs, f"missing: {slugs - found_slugs}"
    for slug, coding_system, code in rows:
        assert coding_system == "icd10", f"{slug} coding_system={coding_system!r}"
        assert code, f"{slug} has empty code"


@pytest.mark.asyncio
async def test_seed_diseases_idempotent():
    """Running ``seed_diseases`` twice doesn't duplicate rows."""
    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_diseases()

    slugs = _seed_slugs()
    async with AsyncSessionLocal() as session:
        count_after_first = await session.scalar(
            select(func.count())
            .select_from(Concept)
            .where(
                Concept.slug.in_(slugs),
                Concept.tenant_id.is_(None),
            )
        )

    stats2 = await svc.seed_diseases()
    assert stats2["added"] == 0, f"second run should add nothing: {stats2}"
    assert stats2["errors"] == 0

    async with AsyncSessionLocal() as session:
        count_after_second = await session.scalar(
            select(func.count())
            .select_from(Concept)
            .where(
                Concept.slug.in_(slugs),
                Concept.tenant_id.is_(None),
            )
        )

    assert count_after_first == count_after_second, (
        f"disease count changed: {count_after_first} -> {count_after_second}"
    )


@pytest.mark.asyncio
async def test_seed_diseases_includes_vaccine_targets():
    """The vaccine-preventable disease slugs are present so PREVENTS edges resolve."""
    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_diseases()

    needed = [
        "measles",
        "mumps",
        "rubella",
        "influenza",
        "covid-19",
        "tetanus",
        "diphtheria",
        "pertussis",
        "hepatitis-b",
        "hpv-infection",
        "cervical-cancer",
        "pneumococcal-pneumonia",
    ]
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Concept.slug).where(
                        concepts_with_kind(ConceptKind.DISEASE),
                        Concept.tenant_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    seeded = set(rows)
    for slug in needed:
        assert slug in seeded, f"vaccine-target disease {slug!r} not seeded"


@pytest.mark.asyncio
async def test_seed_diseases_disease_kind_tagged():
    """Every seeded disease concept carries the ``disease`` kind tag."""
    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_diseases()

    slugs = _seed_slugs()
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Concept.slug).where(
                        Concept.slug.in_(slugs),
                        concepts_with_kind(ConceptKind.DISEASE),
                        Concept.tenant_id.is_(None),
                        Concept.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert set(rows) == slugs, f"not all tagged disease: missing {slugs - set(rows)}"
