"""Generic concept-link filter + projection (Phase 2).

``?class=<slug>`` filters any catalog with a ``class_concept_id`` FK by its
taxonomy class — the adapter resolves the slug → concept id (comma-list OK).
Anatomy + biomarker items carry ``class_concept_slug`` / ``class_concept_name``
(anatomy via ``to_dict``, biomarker via ``_serialize``; both read the
selectin-loaded ``class_concept`` relation). ``?kind=`` filters the ``concept``
catalog by ``primary_kind``. Registry-driven: one mechanism for every catalog.
"""

import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept, ConceptKindTag
from app.models.enums import ConceptKind


def _make_concept(slug, name, kind):
    c = Concept(tenant_id=None, slug=slug, name=name, primary_kind=kind)
    c.kind_tags.append(ConceptKindTag(kind=kind))
    return c


async def _seed_anatomy(suffix):
    organ = _make_concept(f"organ-{suffix}", "Organ", ConceptKind.ANATOMY_CLASS)
    region = _make_concept(f"region-{suffix}", "Region", ConceptKind.ANATOMY_CLASS)
    async with AsyncSessionLocal() as db:
        db.add_all([organ, region])
        await db.flush()  # server-side UUID defaults populate .id
        db.add_all(
            [
                AnatomyStructure(
                    slug=f"heart-{suffix}",
                    name=f"Heart {suffix}",
                    class_concept_id=organ.id,
                ),
                AnatomyStructure(
                    slug=f"head-{suffix}",
                    name=f"Head {suffix}",
                    class_concept_id=region.id,
                ),
            ]
        )
        await db.commit()


@pytest.mark.asyncio
async def test_anatomy_projects_class(async_client, system_admin_headers):
    suffix = uuid.uuid4().hex[:8]
    await _seed_anatomy(suffix)
    resp = await async_client.get(
        "/api/v1/catalogs/anatomy", headers=system_admin_headers
    )
    assert resp.status_code == 200
    items = {it["slug"]: it for it in resp.json()["items"]}
    heart = items[f"heart-{suffix}"]
    assert heart["class_concept_slug"] == f"organ-{suffix}"
    assert heart["class_concept_name"] == "Organ"
    assert heart["class_concept_id"] is not None
    assert items[f"head-{suffix}"]["class_concept_slug"] == f"region-{suffix}"


@pytest.mark.asyncio
async def test_anatomy_class_filter_single_and_multi(
    async_client, system_admin_headers
):
    suffix = uuid.uuid4().hex[:8]
    await _seed_anatomy(suffix)
    resp = await async_client.get(
        f"/api/v1/catalogs/anatomy?class=organ-{suffix}",
        headers=system_admin_headers,
    )
    assert resp.status_code == 200
    assert {it["slug"] for it in resp.json()["items"]} == {f"heart-{suffix}"}

    # Comma-separated multi-slug.
    resp2 = await async_client.get(
        f"/api/v1/catalogs/anatomy?class=organ-{suffix},region-{suffix}",
        headers=system_admin_headers,
    )
    assert {it["slug"] for it in resp2.json()["items"]} == {
        f"heart-{suffix}",
        f"head-{suffix}",
    }


@pytest.mark.asyncio
async def test_anatomy_class_filter_unknown_returns_empty(
    async_client, system_admin_headers
):
    resp = await async_client.get(
        "/api/v1/catalogs/anatomy?class=does-not-exist",
        headers=system_admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_biomarker_class_filter_and_projection(
    async_client, system_admin_headers
):
    suffix = uuid.uuid4().hex[:8]
    cls = _make_concept(f"bio-class-{suffix}", "Bio Class", ConceptKind.BIOMARKER_CLASS)
    async with AsyncSessionLocal() as db:
        db.add(cls)
        await db.flush()
        db.add_all(
            [
                BiomarkerDefinition(
                    slug=f"bio-{suffix}",
                    name=f"Bio {suffix}",
                    class_concept_id=cls.id,
                ),
                BiomarkerDefinition(slug=f"bio2-{suffix}", name=f"Bio2 {suffix}"),
            ]
        )
        await db.commit()

    resp = await async_client.get(
        f"/api/v1/catalogs/biomarker?class=bio-class-{suffix}",
        headers=system_admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {it["slug"] for it in body["items"]} == {f"bio-{suffix}"}
    bio = body["items"][0]
    assert bio["class_concept_slug"] == f"bio-class-{suffix}"
    assert bio["class_concept_name"] == "Bio Class"


@pytest.mark.asyncio
async def test_concept_kind_filter(async_client, system_admin_headers):
    """``?kind=`` filters the concept catalog by ``primary_kind``."""
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                _make_concept(f"ac-{suffix}", "AC", ConceptKind.ANATOMY_CLASS),
                _make_concept(f"sp-{suffix}", "SP", ConceptKind.SPECIALTY),
            ]
        )
        await db.commit()

    resp = await async_client.get(
        "/api/v1/catalogs/concept?kind=anatomy_class",
        headers=system_admin_headers,
    )
    assert resp.status_code == 200
    slugs = {it["slug"] for it in resp.json()["items"]}
    assert f"ac-{suffix}" in slugs
    assert f"sp-{suffix}" not in slugs
