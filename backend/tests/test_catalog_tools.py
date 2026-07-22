"""Phase 8 tests — the `search_catalogs` + `explore_catalog_relations` chatbot tools.

Covers:
- `search_catalogs` returns ranked hits across catalog types with {type, id, label};
  the `types` filter restricts; short/empty queries return [].
- `explore_catalog_relations` returns a pruned subgraph from a start node;
  `max_depth` + `relations` whitelist are respected.
- Both tools are registered in the tool registry and produced by `get_tools`.
"""

import json
import uuid

import pytest
import pytest_asyncio
from app.ai.tools.registry import ToolContext


@pytest_asyncio.fixture
async def catalogs_ctx():
    """Seed catalogs + diseases + edges, then build a ToolContext."""
    from app.core.database import AsyncSessionLocal
    from app.models.tenant_model import TenantModel
    from app.services.seed_service import SeedService

    tid = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tid, name="Cat Tools", slug=f"cat-{tid}"))
        await session.commit()

    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_diseases()
    await svc.seed_medications()
    await svc.seed_vaccines()
    await svc.seed_concept_edges()

    async with AsyncSessionLocal() as session:
        ctx = ToolContext(
            db=session,
            tenant_id=tid,
            patient_id=uuid.uuid4(),
        )
        yield ctx


# ---------------------------------------------------------------------------
# search_catalogs tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_catalogs_finds_medication(catalogs_ctx):
    from app.ai.tools.catalogs import build

    tools = build(catalogs_ctx)
    search = next(t for t in tools if t.name == "search_catalogs")

    result = await search.ainvoke({"query": "metformin"})
    data = json.loads(result)
    assert isinstance(data, list)
    assert any(h["type"] == "medication" for h in data), data
    assert any("metformin" in h["label"].lower() for h in data)


@pytest.mark.asyncio
async def test_search_catalogs_finds_disease(catalogs_ctx):
    from app.ai.tools.catalogs import build

    tools = build(catalogs_ctx)
    search = next(t for t in tools if t.name == "search_catalogs")

    result = await search.ainvoke({"query": "diabetes"})
    data = json.loads(result)
    assert any(h["type"] == "concept" for h in data), data


@pytest.mark.asyncio
async def test_search_catalogs_types_filter(catalogs_ctx):
    from app.ai.tools.catalogs import build

    tools = build(catalogs_ctx)
    search = next(t for t in tools if t.name == "search_catalogs")

    result = await search.ainvoke({"query": "metformin", "types": ["biomarker"]})
    data = json.loads(result)
    # Filtering to biomarker only → no medication hits.
    assert all(h["type"] == "biomarker" for h in data), data


@pytest.mark.asyncio
async def test_search_catalogs_short_query_returns_empty(catalogs_ctx):
    from app.ai.tools.catalogs import build

    tools = build(catalogs_ctx)
    search = next(t for t in tools if t.name == "search_catalogs")

    result = await search.ainvoke({"query": "a"})
    data = json.loads(result)
    assert data == []


# ---------------------------------------------------------------------------
# explore_catalog_relations tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explore_relations_from_medication(catalogs_ctx):
    """Metformin TREATS type-2-diabetes — the subgraph must reach it."""
    from sqlalchemy import func, select

    from app.ai.tools.catalogs import build
    from app.models.fhir.medication import MedicationCatalog

    metformin_id = await catalogs_ctx.db.scalar(
        select(MedicationCatalog.id).where(
            func.lower(MedicationCatalog.name) == "metformin",
            MedicationCatalog.tenant_id.is_(None),
        )
    )
    assert metformin_id is not None

    tools = build(catalogs_ctx)
    explore = next(t for t in tools if t.name == "explore_catalog_relations")

    result = await explore.ainvoke(
        {"type": "medication", "id": str(metformin_id), "max_depth": 1}
    )
    data = json.loads(result)
    assert "nodes" in data and "edges" in data
    labels = [n.get("label", "") for n in data["nodes"]]
    assert any("diabetes" in (lbl or "").lower() for lbl in labels), labels
    relations = [e["relation"] for e in data["edges"]]
    assert "TREATS" in relations, relations


@pytest.mark.asyncio
async def test_explore_relations_relation_whitelist(catalogs_ctx):
    """The `relations` whitelist filters to only matching edges."""
    from sqlalchemy import func, select

    from app.ai.tools.catalogs import build
    from app.models.fhir.medication import MedicationCatalog

    metformin_id = await catalogs_ctx.db.scalar(
        select(MedicationCatalog.id).where(
            func.lower(MedicationCatalog.name) == "metformin",
            MedicationCatalog.tenant_id.is_(None),
        )
    )

    tools = build(catalogs_ctx)
    explore = next(t for t in tools if t.name == "explore_catalog_relations")

    result = await explore.ainvoke(
        {
            "type": "medication",
            "id": str(metformin_id),
            "max_depth": 1,
            "relations": ["PREVENTS"],
        }
    )
    data = json.loads(result)
    # Metformin has TREATS + CONTRAINDICATES edges, no PREVENTS — so the
    # whitelist yields an empty (or start-only) graph.
    relations = [e["relation"] for e in data["edges"]]
    assert "TREATS" not in relations, "whitelist should exclude TREATS"
    assert all(r == "PREVENTS" for r in relations), relations


@pytest.mark.asyncio
async def test_explore_relations_from_vaccine(catalogs_ctx):
    """MMR PREVENTS measles — the subgraph must reach it."""
    from sqlalchemy import select

    from app.ai.tools.catalogs import build
    from app.models.fhir.vaccine import VaccineCatalog

    mmr_id = await catalogs_ctx.db.scalar(
        select(VaccineCatalog.id).where(
            VaccineCatalog.slug == "mmr",
            VaccineCatalog.tenant_id.is_(None),
        )
    )
    assert mmr_id is not None

    tools = build(catalogs_ctx)
    explore = next(t for t in tools if t.name == "explore_catalog_relations")

    result = await explore.ainvoke(
        {"type": "vaccine", "id": str(mmr_id), "max_depth": 1}
    )
    data = json.loads(result)
    labels = [n.get("label", "") for n in data["nodes"]]
    assert any("measles" in (lbl or "").lower() for lbl in labels), labels


@pytest.mark.asyncio
async def test_explore_relations_returns_truncation_metadata(catalogs_ctx):
    """The result carries truncated + node_count + edge_count so the LLM can
    self-correct when the edge cap is hit."""
    from sqlalchemy import func, select

    from app.ai.tools.catalogs import build
    from app.models.fhir.medication import MedicationCatalog

    metformin_id = await catalogs_ctx.db.scalar(
        select(MedicationCatalog.id).where(
            func.lower(MedicationCatalog.name) == "metformin",
            MedicationCatalog.tenant_id.is_(None),
        )
    )
    tools = build(catalogs_ctx)
    explore = next(t for t in tools if t.name == "explore_catalog_relations")

    result = await explore.ainvoke(
        {"type": "medication", "id": str(metformin_id), "max_depth": 1}
    )
    data = json.loads(result)
    assert "truncated" in data
    assert data["truncated"] is False  # the seed graph is small, no truncation
    assert data["node_count"] == len(data["nodes"])
    assert data["edge_count"] == len(data["edges"])


@pytest.mark.asyncio
async def test_explore_relations_types_filter(catalogs_ctx):
    """The `types` whitelist prunes edges to only those touching the listed
    endpoint types. Metformin TREATS diabetes (a concept) — filtering to
    'concept' keeps those edges; filtering to 'anatomy' drops them."""
    from sqlalchemy import func, select

    from app.ai.tools.catalogs import build
    from app.models.fhir.medication import MedicationCatalog

    metformin_id = await catalogs_ctx.db.scalar(
        select(MedicationCatalog.id).where(
            func.lower(MedicationCatalog.name) == "metformin",
            MedicationCatalog.tenant_id.is_(None),
        )
    )
    tools = build(catalogs_ctx)
    explore = next(t for t in tools if t.name == "explore_catalog_relations")

    # No filter — TREATS edges to concepts are present.
    unfiltered = json.loads(
        await explore.ainvoke(
            {"type": "medication", "id": str(metformin_id), "max_depth": 1}
        )
    )
    assert any(e["relation"] == "TREATS" for e in unfiltered["edges"])

    # Filter to anatomy only — the concept edges are pruned.
    anatomy_only = json.loads(
        await explore.ainvoke(
            {
                "type": "medication",
                "id": str(metformin_id),
                "max_depth": 1,
                "types": ["anatomy"],
            }
        )
    )
    if anatomy_only["edges"]:
        for e in anatomy_only["edges"]:
            assert e["src"]["type"] == "anatomy" or e["dst"]["type"] == "anatomy"
    # TREATS→concept edges are gone (metformin has no anatomy edges in the seed).
    assert not any(e["relation"] == "TREATS" for e in anatomy_only["edges"])

    # Filter to concept — the TREATS edges survive.
    concept_only = json.loads(
        await explore.ainvoke(
            {
                "type": "medication",
                "id": str(metformin_id),
                "max_depth": 1,
                "types": ["concept"],
            }
        )
    )
    assert any(e["relation"] == "TREATS" for e in concept_only["edges"])


@pytest.mark.asyncio
async def test_explore_relations_depth_clamp(catalogs_ctx):
    """max_depth > 3 is clamped to 3 at the tool layer — it must not raise
    (the service allows 1-5 and would raise ValueError only above 5; the
    tool's clamp prevents even that)."""
    from sqlalchemy import func, select

    from app.ai.tools.catalogs import build
    from app.models.fhir.medication import MedicationCatalog

    metformin_id = await catalogs_ctx.db.scalar(
        select(MedicationCatalog.id).where(
            func.lower(MedicationCatalog.name) == "metformin",
            MedicationCatalog.tenant_id.is_(None),
        )
    )
    tools = build(catalogs_ctx)
    explore = next(t for t in tools if t.name == "explore_catalog_relations")

    # depth=10 would crash the service (max 5) without the clamp.
    result = await explore.ainvoke(
        {"type": "medication", "id": str(metformin_id), "max_depth": 10}
    )
    data = json.loads(result)
    assert "nodes" in data  # no crash → clamp worked


@pytest.mark.asyncio
async def test_traverse_truncation_flag(catalogs_ctx):
    """The traverse() service sets truncated=True when the edge LIMIT is hit,
    using the limit+1 probe pattern (no extra round-trip)."""
    from sqlalchemy import func, select

    from app.models.fhir.medication import MedicationCatalog
    from app.models.enums import EdgeEndpointType
    from app.services.catalog_graph_service import traverse

    metformin_id = await catalogs_ctx.db.scalar(
        select(MedicationCatalog.id).where(
            func.lower(MedicationCatalog.name) == "metformin",
            MedicationCatalog.tenant_id.is_(None),
        )
    )
    assert metformin_id is not None

    # limit=1 forces truncation when metformin has ≥1 edge (it TREATS diabetes).
    graph = await traverse(
        catalogs_ctx.db,
        EdgeEndpointType.MEDICATION,
        metformin_id,
        tenant_id=catalogs_ctx.tenant_id,
        max_depth=1,
        limit=1,
    )
    assert graph["truncated"] is True
    assert graph["edge_count"] == 1  # trimmed back to the limit
    assert graph["node_count"] == len(graph["nodes"])


# ---------------------------------------------------------------------------
# Registration — tools appear in get_tools
# ---------------------------------------------------------------------------


def test_catalog_tools_registered():
    """The catalogs domain factory is registered + produces both tools."""
    from app.ai.tools.registry import get_factories

    factories = get_factories()
    assert "catalogs" in factories


@pytest.mark.asyncio
async def test_catalog_tools_in_get_tools(catalogs_ctx):
    """get_tools includes search_catalogs + explore_catalog_relations."""
    from app.ai.tools import get_tools

    tools = get_tools(
        catalogs_ctx.db,
        catalogs_ctx.tenant_id,
        catalogs_ctx.patient_id,
    )
    names = {t.name for t in tools}
    assert "search_catalogs" in names
    assert "explore_catalog_relations" in names
