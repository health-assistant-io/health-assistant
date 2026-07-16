"""Whole-system tests for the seed pipeline (Phases 2+ of the robustness plan)."""

import pytest

from app.services.seed_service import SeedService


@pytest.mark.asyncio
async def test_seed_all_stage_names_match_public_methods(monkeypatch):
    """Every name in _SEED_STAGE_NAMES resolves to a real seed_<name> method
    on SeedService — guards against a typo silently skipping a stage."""
    svc = SeedService()
    for name in svc._SEED_STAGE_NAMES:
        assert hasattr(svc, f"seed_{name}"), f"seed_{name} not found for stage '{name}'"


@pytest.mark.asyncio
async def test_seed_all_runs_stages_in_declared_order(monkeypatch):
    """seed_all() invokes seed_<stage> in the exact order of _SEED_STAGE_NAMES.

    Stages are stubbed (no DB work) so this is a pure ordering contract test —
    it fails fast if someone reorders the list without realizing the dependency
    implications, or adds a stage and forgets to wire it.
    """
    svc = SeedService()
    calls: list[str] = []

    for name in svc._SEED_STAGE_NAMES:

        async def _stub(_name=name):
            calls.append(_name)
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        monkeypatch.setattr(svc, f"seed_{name}", _stub)

    result = await svc.seed_all()

    assert list(result.keys()) == svc._SEED_STAGE_NAMES
    assert calls == svc._SEED_STAGE_NAMES


@pytest.mark.asyncio
async def test_seed_biomarker_panels_creates_membership_edges():
    """The migrated {metadata, items:[{panel_slug, biomarker_slug}]} envelope
    seeds MEMBER_OF edges biomarker -> panel and returns the standard stats."""
    from app.services.seed_service import SeedService
    from app.core.database import AsyncSessionLocal
    from app.models.concept_model import ConceptEdge
    from app.models.enums import EdgeEndpointType, ConceptRelationType
    from sqlalchemy import select, func

    svc = SeedService()
    # Prerequisites: panels (concepts) + biomarker definitions (default catalog).
    await svc.seed_concepts()
    await svc.seed_default_catalog()

    stats = await svc.seed_biomarker_panels()
    # Standard contract.
    for k in ("added", "updated", "skipped", "errors"):
        assert k in stats and isinstance(stats[k], int), f"bad stats: {stats}"
    assert stats["errors"] == 0, f"panel seeding had errors: {stats}"

    async with AsyncSessionLocal() as session:
        member_edges = await session.scalar(
            select(func.count())
            .select_from(ConceptEdge)
            .where(
                ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                ConceptEdge.relation == ConceptRelationType.MEMBER_OF,
                ConceptEdge.tenant_id.is_(None),
            )
        )
    # 7 membership rows in the seed file.
    assert member_edges >= 7, f"expected >=7 MEMBER_OF edges, got {member_edges}"


@pytest.mark.asyncio
async def test_seed_body_parts_reads_split_anatomy_files():
    """anatomy_structures.json seeds nodes; anatomy hierarchy edges now live
    in concept_edges.json (src_type=anatomy, dst_type=anatomy) and are seeded
    by seed_concept_edges, not seed_body_parts."""
    from app.services.seed_service import SeedService
    from app.core.database import AsyncSessionLocal
    from app.models.anatomy_model import AnatomyStructure
    from app.models.concept_model import ConceptEdge
    from app.models.enums import EdgeEndpointType
    from sqlalchemy import select, func

    svc = SeedService()
    stats = await svc.seed_body_parts()
    for k in ("added", "updated", "skipped", "errors"):
        assert k in stats, f"missing {k}: {stats}"

    async with AsyncSessionLocal() as session:
        node_count = await session.scalar(
            select(func.count()).select_from(AnatomyStructure)
        )
        # Anatomy hierarchy edges are now in concept_edges (seeded by
        # seed_concept_edges, not seed_body_parts).
        edge_count = await session.scalar(
            select(func.count()).select_from(ConceptEdge).where(
                ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
            )
        )
    assert node_count >= 54, f"expected >=54 anatomy nodes, got {node_count}"
    assert edge_count >= 0, f"anatomy edges in concept_edges: {edge_count}"


@pytest.mark.asyncio
async def test_seed_all_executes_every_stage_for_real(tmp_path, monkeypatch):
    """seed_all() with no stubbing runs every stage method (they all return a
    stats dict). Validates the getattr-based dispatch + that no stage raises
    on an empty/fresh DB. Uses the real SeedService against the real test DB
    (migrations already run by the session conftest)."""
    # seed_all() includes seed_anatomy_figures, which writes images under
    # settings.UPLOAD_DIR. The production default (/var/healthassistant/uploads)
    # is not writable in CI, so point it at a tmp dir for the test.
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    svc = SeedService()
    result = await svc.seed_all()

    # Every declared stage produced a stats dict with the standard contract:
    # {added, updated, skipped, errors}. default_catalog additionally carries a
    # 'details' sub-dict (units/biomarkers breakdown). Covers Phase 3.
    assert set(result.keys()) == set(svc._SEED_STAGE_NAMES)
    standard_keys = {"added", "updated", "skipped", "errors"}
    for name, stats in result.items():
        assert isinstance(stats, dict), f"stage {name} returned non-dict: {stats}"
        missing = standard_keys - set(stats.keys())
        assert not missing, f"stage {name} stats missing {missing}: {stats}"
        for k in standard_keys:
            assert isinstance(stats[k], int), f"stage {name}.{k} not int: {stats[k]}"
        if name == "default_catalog":
            assert "details" in stats, f"default_catalog missing details: {stats}"
            assert "units_added" in stats["details"]
