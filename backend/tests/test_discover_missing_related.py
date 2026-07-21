"""Tests for the ``discover_missing_related`` catalog tool — the multi-step
creation discovery helper added alongside ``ask_user``.

Pins:
1. **Happy path** — primary + related items each looked up via search_catalogs;
   missing items surface ``exists=false``; existing items carry their match.
2. **Validation** — bad primary_type, bad related item shapes, length cap.
3. **Resilience** — search failures degrade to empty matches (never raises).
4. **Wiring** — the tool is registered alongside the existing search/explore.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.ai.tools.catalogs import MAX_RELATED_ITEMS, build
from app.ai.tools.registry import ToolContext


def _ctx() -> ToolContext:
    return ToolContext(
        db=MagicMock(),
        tenant_id=uuid4(),
        patient_id=uuid4(),
    )


def _find_tool(tools, name: str):
    return next((t for t in tools if t.name == name), None)


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_primary_and_related_each_looked_up():
    """Each item triggers one search_catalogs call; results surface cleanly."""
    ctx = _ctx()
    tools = build(ctx)
    discover = _find_tool(tools, "discover_missing_related")
    assert discover is not None

    primary_hit = [{"id": "u1", "label": "Metformin", "type": "medication", "slug": "metformin"}]
    t2d_hits = [{"id": "u2", "label": "Type 2 Diabetes", "type": "concept"}]
    hba1c_hits: list = []  # missing

    async def _fake_search(db, tenant_id, query, **kwargs):
        types = kwargs.get("types") or []
        if types == ["medication"]:
            return primary_hit
        if types == ["concept"]:
            return t2d_hits
        if types == ["biomarker"]:
            return hba1c_hits
        return []

    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(side_effect=_fake_search),
    ):
        raw = await discover.ainvoke(
            {
                "primary_type": "medication",
                "primary_name": "Metformin",
                "related": [
                    {"type": "concept", "name": "Type 2 Diabetes", "suggested_relation": "TREATS"},
                    {"type": "biomarker", "name": "HbA1c", "suggested_relation": "AFFECTS"},
                ],
            }
        )

    out = json.loads(raw)
    assert out["primary"]["exists"] is True
    assert out["primary"]["match"]["id"] == "u1"

    assert len(out["items"]) == 2
    t2d = out["items"][0]
    assert t2d["type"] == "concept"
    assert t2d["name"] == "Type 2 Diabetes"
    assert t2d["suggested_relation"] == "TREATS"
    assert t2d["exists"] is True
    assert t2d["matches"][0]["id"] == "u2"

    hba1c = out["items"][1]
    assert hba1c["exists"] is False
    assert hba1c["matches"] == []


# ---------------------------------------------------------------------------
# 2. Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_unknown_primary_type():
    ctx = _ctx()
    discover = _find_tool(build(ctx), "discover_missing_related")
    raw = await discover.ainvoke(
        {
            "primary_type": "not_real",
            "primary_name": "X",
            "related": [{"type": "concept", "name": "Y"}],
        }
    )
    out = json.loads(raw)
    assert "error" in out
    assert "primary_type" in out["error"]


@pytest.mark.asyncio
async def test_rejects_empty_primary_name():
    ctx = _ctx()
    discover = _find_tool(build(ctx), "discover_missing_related")
    raw = await discover.ainvoke(
        {
            "primary_type": "medication",
            "primary_name": "   ",
            "related": [{"type": "concept", "name": "Y"}],
        }
    )
    out = json.loads(raw)
    assert "error" in out


@pytest.mark.asyncio
async def test_rejects_empty_related_list():
    ctx = _ctx()
    discover = _find_tool(build(ctx), "discover_missing_related")
    raw = await discover.ainvoke(
        {"primary_type": "medication", "primary_name": "X", "related": []}
    )
    out = json.loads(raw)
    assert "error" in out


@pytest.mark.asyncio
async def test_rejects_too_many_related_items():
    ctx = _ctx()
    discover = _find_tool(build(ctx), "discover_missing_related")
    raw = await discover.ainvoke(
        {
            "primary_type": "medication",
            "primary_name": "X",
            "related": [
                {"type": "concept", "name": f"Y{i}"} for i in range(MAX_RELATED_ITEMS + 1)
            ],
        }
    )
    out = json.loads(raw)
    assert "error" in out
    assert "too many" in out["error"]


@pytest.mark.asyncio
async def test_bad_related_item_shape_surfaces_per_item_error():
    """A malformed-but-dict related item does NOT fail the whole call — it
    surfaces a per-item error so the LLM can self-correct. (Non-dict items
    are rejected by the LangChain tool's input schema before the function
    runs, so they cannot reach the per-item path.)"""
    ctx = _ctx()
    discover = _find_tool(build(ctx), "discover_missing_related")

    async def _fake_search(db, tenant_id, query, **kwargs):
        return []  # nothing matches

    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(side_effect=_fake_search),
    ):
        raw = await discover.ainvoke(
            {
                "primary_type": "medication",
                "primary_name": "X",
                "related": [
                    {"type": "bad_type", "name": "Y"},  # unknown type
                    {"type": "concept"},  # missing name
                    {"type": "concept", "name": "OK"},  # valid
                ],
            }
        )
    out = json.loads(raw)
    items = out["items"]
    assert len(items) == 3
    assert items[0]["error"].startswith("type")
    assert items[1]["error"] == "name is required"
    assert "error" not in items[2]
    assert items[2]["exists"] is False


# ---------------------------------------------------------------------------
# 3. Resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_failure_degrades_to_empty_matches():
    """A search_catalogs exception is swallowed per-item; the call still
    returns (with empty matches for the failing item)."""
    ctx = _ctx()
    discover = _find_tool(build(ctx), "discover_missing_related")

    call_count = {"n": 0}

    async def _fake_search(db, tenant_id, query, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("search backend down")
        return []

    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(side_effect=_fake_search),
    ):
        raw = await discover.ainvoke(
            {
                "primary_type": "medication",
                "primary_name": "X",
                "related": [{"type": "concept", "name": "Y"}],
            }
        )
    out = json.loads(raw)
    # Primary lookup raised → primary.exists = False (graceful).
    assert out["primary"]["exists"] is False
    assert out["primary"]["match"] is None
    # Related item lookup returned [] → exists = False.
    assert out["items"][0]["exists"] is False


# ---------------------------------------------------------------------------
# 4. Wiring
# ---------------------------------------------------------------------------


def test_catalogs_factory_returns_three_tools_now():
    """The catalogs domain now exposes search + explore + discover_missing_related."""
    tools = build(_ctx())
    names = sorted(t.name for t in tools)
    assert names == ["discover_missing_related", "explore_catalog_relations", "search_catalogs"]


@pytest.mark.asyncio
async def test_trims_matches_to_relevant_fields_only():
    """The output excludes large/PHI fields; only id/name/slug/type/matched_on."""
    ctx = _ctx()
    discover = _find_tool(build(ctx), "discover_missing_related")

    big_hit = [
        {
            "id": "u1",
            "label": "Metformin",
            "slug": "metformin",
            "type": "medication",
            "matched_on": ["name"],
            "description": "very long text... " * 50,
            "snippet": "another long snippet",
            "score": 0.99,
        }
    ]

    with patch(
        "app.services.catalog_search_service.search_catalogs",
        new=AsyncMock(return_value=big_hit),
    ):
        raw = await discover.ainvoke(
            {
                "primary_type": "medication",
                "primary_name": "Metformin",
                "related": [{"type": "concept", "name": "X"}],
            }
        )
    out = json.loads(raw)
    match = out["primary"]["match"]
    assert set(match.keys()) == {"id", "name", "slug", "type", "matched_on"}
    assert "description" not in match
    assert "snippet" not in match
    assert "score" not in match
