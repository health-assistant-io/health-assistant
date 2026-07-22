"""Tests for the ``propose_anatomy_graph_generation`` HITL chat tool.

Covers the contract documented in dev/plans/anatomy-graph-hitl-2026-07-22.md:
  * The tool is produced by the ``hitl_proposals`` factory.
  * It returns the ``{"__hitl__": True, "task": {...}}`` marker.
  * The task payload uses ``task_type="generate_anatomy_graph"``,
    ``status=HitlTaskStatus.PROPOSED``, and carries ``target_structure``.
  * ``_notify_hitl_proposal`` is invoked (best-effort inbox surface) when
    a ``user_id`` is present on the context.
  * The tool performs NO write (read-only — the "AI never writes" model).

The generate+import step happens client-side on confirm (mirrors every other
``propose_*`` handler) and is exercised by the frontend handler tests.
"""
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from app.ai.tools.registry import ToolContext, get_factories
from app.models.enums import HitlTaskStatus


@pytest_asyncio.fixture
async def hitl_ctx():
    """Build a ToolContext with a real session + a user_id (so the notification
    branch is exercised). No DB rows are written by the tool under test."""
    from app.core.database import AsyncSessionLocal
    from app.models.tenant_model import TenantModel

    tenant_id = uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TenantModel(id=tenant_id, name="Anatomy HITL T.", slug=f"anat-{tenant_id.hex[:8]}"))
        await session.commit()

    async with AsyncSessionLocal() as session:
        ctx = ToolContext(
            db=session,
            tenant_id=tenant_id,
            patient_id=uuid4(),
            user_id=uuid4(),
        )
        yield ctx


def _get_tool(ctx):
    """Build the hitl_proposals tool list and return the anatomy-graph tool."""
    factory = get_factories()["hitl_proposals"]
    tools = factory(ctx)
    for t in tools:
        if t.name == "propose_anatomy_graph_generation":
            return t
    pytest.fail("propose_anatomy_graph_generation not returned by hitl_proposals.build()")


@pytest.mark.asyncio
async def test_tool_is_registered_in_factory(hitl_ctx):
    """The anatomy-graph tool must be in the hitl_proposals factory output."""
    factory = get_factories()["hitl_proposals"]
    tools = factory(hitl_ctx)
    names = [t.name for t in tools]
    assert "propose_anatomy_graph_generation" in names


@pytest.mark.asyncio
async def test_tool_returns_hitl_marker_with_correct_task_type(hitl_ctx):
    tool = _get_tool(hitl_ctx)
    raw = await tool.ainvoke({"target_structure": "Cardiovascular System"})
    payload = json.loads(raw)

    assert payload["__hitl__"] is True
    task = payload["task"]
    assert task["task_type"] == "generate_anatomy_graph"
    assert task["status"] == HitlTaskStatus.PROPOSED.value
    assert task["schema_version"] == 3
    assert task["proposed_payload"]["target_structure"] == "Cardiovascular System"
    assert task["proposal_id"]  # uuid4 string
    assert task["context"]["patient_id"]
    # No existing anatomy seeded → existing is None + the note says so.
    assert task["proposed_payload"]["existing"] is None
    assert "from scratch" in payload["note"]
    # The generated field exists (None in the test env where no LLM provider
    # is configured — the tool's try/except falls back gracefully).
    assert "generated" in task["proposed_payload"]


@pytest.mark.asyncio
async def test_tool_searches_existing_anatomy_and_embeds_snapshot(hitl_ctx):
    """When the target already exists in the catalog, the tool embeds its
    2-hop neighborhood (node slugs + slug-based edges) in the payload and the
    LLM-facing note reports the counts — so generation fills gaps, not dupes."""
    from app.models.anatomy_model import AnatomyStructure
    from app.models.concept_model import ConceptEdge
    from app.models.enums import (
        ConceptRelationType,
        EdgeApprovalStatus,
        EdgeEndpointType,
    )
    from app.core.database import AsyncSessionLocal

    root_slug = f"test-heart-{uuid4().hex[:6]}"
    child_slug = f"test-left-ventricle-{uuid4().hex[:6]}"
    async with AsyncSessionLocal() as db:
        root = AnatomyStructure(
            slug=root_slug, name="Heart", scope="system", tenant_id=None
        )
        child = AnatomyStructure(
            slug=child_slug, name="Left Ventricle", scope="system", tenant_id=None
        )
        db.add_all([root, child])
        await db.flush()
        db.add(
            ConceptEdge(
                src_type=EdgeEndpointType.ANATOMY,
                src_id=child.id,
                dst_type=EdgeEndpointType.ANATOMY,
                dst_id=root.id,
                relation=ConceptRelationType.PART_OF,
                status=EdgeApprovalStatus.APPROVED,
            )
        )
        await db.commit()

    tool = _get_tool(hitl_ctx)
    raw = await tool.ainvoke({"target_structure": root_slug})
    payload = json.loads(raw)
    task = payload["task"]
    existing = task["proposed_payload"]["existing"]

    assert existing is not None
    assert existing["root_slug"] == root_slug
    assert existing["node_count"] >= 2
    assert root_slug in existing["node_slugs"]
    assert child_slug in existing["node_slugs"]
    # The edge is slug-resolved (not UUIDs).
    assert any(
        e["source_slug"] == child_slug and e["target_slug"] == root_slug
        for e in existing["edges"]
    )
    # The note tells the LLM what already exists.
    assert "already defined" in payload["note"]
    assert root_slug in payload["note"]


@pytest.mark.asyncio
async def test_tool_embeds_generated_graph_at_proposal_time(hitl_ctx):
    """When the in-tool generation succeeds, the generated {nodes, edges} is
    embedded in proposed_payload.generated so the review card opens instantly
    (no client-side LLM call on modal open). The LLM resolution + generation
    call are mocked — we only verify the wiring."""
    fake_graph = {
        "nodes": [
            {"slug": "heart", "name": "Heart", "class_concept_slug": "organ"},
            {"slug": "left-ventricle", "name": "Left Ventricle", "class_concept_slug": "organ-part"},
        ],
        "edges": [
            {"source_slug": "left-ventricle", "target_slug": "heart", "relation_type": "PART_OF"},
        ],
    }
    with patch(
        "app.ai.providers.service.AIProviderService.get_llm",
        new=AsyncMock(),
    ) as mock_get_llm, patch(
        "app.ai.assistance.definitions.define_anatomy_graph",
        new=AsyncMock(),
    ) as mock_gen:
        mock_get_llm.return_value = AsyncMock()
        mock_gen.return_value = {"success": True, "suggested_data": fake_graph}
        tool = _get_tool(hitl_ctx)
        raw = await tool.ainvoke({"target_structure": "Heart"})
    payload = json.loads(raw)
    task = payload["task"]
    generated = task["proposed_payload"]["generated"]
    assert generated is not None
    assert len(generated["nodes"]) == 2
    assert len(generated["edges"]) == 1
    # The note tells the LLM the graph is ready.
    assert "Generated 2 nodes" in payload["note"]


@pytest.mark.asyncio
async def test_tool_fires_inbox_notification(hitl_ctx):
    """When a user_id is present, the proposal surfaces in the inbox so it
    survives a closed chat window. Best-effort — never breaks the call."""
    with patch(
        "app.ai.tools.hitl_proposals._notify_hitl_proposal",
        new=AsyncMock(),
    ) as notify:
        tool = _get_tool(hitl_ctx)
        await tool.ainvoke({"target_structure": "Heart"})

    notify.assert_awaited_once()
    args, _ = notify.call_args
    task = args[1]
    assert task["task_type"] == "generate_anatomy_graph"
    assert task["title"] == "Generate Anatomy Graph: Heart"


@pytest.mark.asyncio
async def test_tool_is_read_only_no_db_writes(hitl_ctx):
    """The tool must not write anything — the actual graph generation + import
    happens client-side on user confirm (security model: AI never writes)."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.anatomy_model import AnatomyStructure

    # Snapshot anatomy row count before.
    async with AsyncSessionLocal() as session:
        before = (await session.execute(select(AnatomyStructure))).all()

    tool = _get_tool(hitl_ctx)
    await tool.ainvoke({"target_structure": "Eye"})

    # No new anatomy rows created by the proposal.
    async with AsyncSessionLocal() as session:
        after = (await session.execute(select(AnatomyStructure))).all()
    assert len(after) == len(before)


# ---------------------------------------------------------------------------
# Prompt guidance — the LLM must be told to search before generating
# ---------------------------------------------------------------------------


def test_system_prompt_tells_llm_to_search_anatomy_before_generating():
    """The CHAT + GENERAL prompts must instruct the LLM to call
    `search_catalogs` (types=anatomy) + `explore_catalog_relations` BEFORE
    `propose_anatomy_graph_generation`, so it understands the existing catalog
    and scopes the proposal to gaps rather than generating blind."""
    from app.ai.agents.prompts import CHAT_SYSTEM_PROMPT, GENERAL_CHAT_SYSTEM_PROMPT

    for prompt in (CHAT_SYSTEM_PROMPT, GENERAL_CHAT_SYSTEM_PROMPT):
        assert "propose_anatomy_graph_generation" in prompt
        assert "search_catalogs" in prompt
        assert "explore_catalog_relations" in prompt
        assert 'types="anatomy"' in prompt or "types='anatomy'" in prompt.lower().replace("'", '"') or "anatomy" in prompt
    # The chat prompt carries the full multi-step search procedure.
    assert "SEARCH BEFORE GENERATING ANATOMY" in CHAT_SYSTEM_PROMPT
