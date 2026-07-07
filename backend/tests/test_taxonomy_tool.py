"""Tests for the taxonomy AI chatbot tools."""

import json
import uuid

import pytest
import pytest_asyncio
from app.ai.tools.registry import ToolContext
from app.ai.tools.taxonomy import build


@pytest_asyncio.fixture
async def taxonomy_ctx():
    """Create a tenant + seed concepts + build a ToolContext."""
    from app.core.database import AsyncSessionLocal
    from app.models.tenant_model import TenantModel
    from app.services.seed_service import SeedService

    tid = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tid, name="Tool Test", slug=f"tool-{tid}"))
        await session.commit()

    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_concept_edges()

    async with AsyncSessionLocal() as session:
        ctx = ToolContext(
            db=session,
            tenant_id=tid,
            patient_id=uuid.uuid4(),
        )
        yield ctx


@pytest.mark.asyncio
async def test_search_concepts_tool(taxonomy_ctx):
    """search_concepts finds concepts by name."""
    tools = build(taxonomy_ctx)
    search = tools[0]

    result = await search.ainvoke({"search_term": "cardio", "kind": "specialty"})
    data = json.loads(result)
    assert len(data) > 0
    assert any(c["name"] == "Cardiology" for c in data)
    assert all(c["kind"] == "specialty" for c in data)


@pytest.mark.asyncio
async def test_search_concepts_by_alias(taxonomy_ctx):
    """search_concepts matches on aliases (JSONB containment)."""
    tools = build(taxonomy_ctx)
    search = tools[0]

    result = await search.ainvoke({"search_term": "statins"})
    data = json.loads(result)
    assert any(c["slug"] == "atc-c10aa" for c in data), (
        f"Alias 'statins' should find the statin medication class: {data}"
    )


@pytest.mark.asyncio
async def test_get_concept_neighborhood_tool(taxonomy_ctx):
    """get_concept_neighborhood returns one-hop graph neighbors."""
    from sqlalchemy import select
    from app.models.concept_model import Concept

    tools = build(taxonomy_ctx)
    neighborhood = tools[1]

    cardio = await taxonomy_ctx.db.execute(
        select(Concept).where(
            Concept.slug == "cardiology",
        )
    )
    cardio_id = str(cardio.scalar_one().id)

    result = await neighborhood.ainvoke({"concept_id": cardio_id})
    data = json.loads(result)
    assert len(data) > 0
    relations = [d["edge_relation"] for d in data]
    assert "EXAMINES" in relations or "PERFORMS" in relations


@pytest.mark.asyncio
async def test_get_entity_concepts_invalid_uuid(taxonomy_ctx):
    """get_entity_concepts handles invalid UUID gracefully."""
    tools = build(taxonomy_ctx)
    entity_concepts = tools[2]

    result = await entity_concepts.ainvoke(
        {
            "entity_type": "biomarker",
            "entity_id": "not-a-uuid",
        }
    )
    data = json.loads(result)
    assert "error" in data
