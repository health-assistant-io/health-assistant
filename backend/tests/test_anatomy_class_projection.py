"""Anatomy dedicated-endpoint class projection + ``?class=`` filter (Phase 2).

``GET /anatomy`` and ``GET /anatomy/{slug}`` now annotate each item with its
``class_concept_slug`` / ``class_concept_name`` (read off the selectin-loaded
``class_concept`` relation) and accept ``?class=<slug>`` (comma-list OK). This
is what the Anatomy Explorer uses.
"""

import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.models.anatomy_model import AnatomyStructure
from app.models.concept_model import Concept, ConceptKindTag
from app.models.enums import ConceptKind


def _make_concept(slug, name, kind):
    c = Concept(tenant_id=None, slug=slug, name=name, primary_kind=kind)
    c.kind_tags.append(ConceptKindTag(kind=kind))
    return c


async def _seed(suffix):
    organ = _make_concept(f"organ-{suffix}", "Organ", ConceptKind.ANATOMY_CLASS)
    region = _make_concept(f"region-{suffix}", "Region", ConceptKind.ANATOMY_CLASS)
    async with AsyncSessionLocal() as db:
        db.add_all([organ, region])
        await db.flush()
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
async def test_anatomy_list_class_filter_and_projection(
    async_client, system_admin_headers
):
    suffix = uuid.uuid4().hex[:8]
    await _seed(suffix)

    resp = await async_client.get(
        f"/api/v1/anatomy?class=organ-{suffix}", headers=system_admin_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, "expected the seeded organ structure"
    assert all(it["class_concept_slug"] == f"organ-{suffix}" for it in items)
    assert any(it["class_concept_name"] == "Organ" for it in items)


@pytest.mark.asyncio
async def test_anatomy_list_multi_class(async_client, system_admin_headers):
    suffix = uuid.uuid4().hex[:8]
    await _seed(suffix)
    resp = await async_client.get(
        f"/api/v1/anatomy?class=organ-{suffix},region-{suffix}",
        headers=system_admin_headers,
    )
    slugs = {it["slug"] for it in resp.json()["items"]}
    assert slugs == {f"heart-{suffix}", f"head-{suffix}"}


@pytest.mark.asyncio
async def test_anatomy_detail_projects_class(async_client, system_admin_headers):
    suffix = uuid.uuid4().hex[:8]
    await _seed(suffix)
    resp = await async_client.get(
        f"/api/v1/anatomy/heart-{suffix}", headers=system_admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["class_concept_slug"] == f"organ-{suffix}"
    assert body["class_concept_name"] == "Organ"


@pytest.mark.asyncio
async def test_anatomy_create_with_class_slug(async_client, system_admin_headers):
    """Creating with ``class_concept_slug`` resolves to ``class_concept_id``."""
    suffix = uuid.uuid4().hex[:8]
    organ = _make_concept(f"organ-{suffix}", "Organ", ConceptKind.ANATOMY_CLASS)
    async with AsyncSessionLocal() as db:
        db.add(organ)
        await db.commit()

    resp = await async_client.post(
        "/api/v1/anatomy",
        headers=system_admin_headers,
        json={
            "slug": f"new-organ-{suffix}",
            "name": f"New Organ {suffix}",
            "class_concept_slug": f"organ-{suffix}",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["class_concept_slug"] == f"organ-{suffix}"
    assert body["class_concept_name"] == "Organ"
