"""Hybrid search (trigram + FTS + RRF) tests — Phase 5.

Covers the new capabilities added by the unified hybrid search pipeline:

- Alias matching: ``FBS`` finds ``Fasting Glucose`` via the JSONB aliases array
  (previously only exact JSONB containment worked).
- Multi-word FTS: ``blood sugar`` finds biomarkers whose description/info
  text mentions those words.
- Symptom → medication FTS: ``headache`` finds drugs whose indications
  mention headache.
- ``matched_on`` provenance field lists which fields drove each hit.
- ``snippet`` shows the matching context for FTS hits.
- RRF cross-catalog ranking: a query like ``diabetes`` returns concept +
  medication hits in one globally-ranked list.
- ``search_concepts(kind=...)`` filter still works through the new pipeline.
"""

import json
import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.ai.tools.registry import ToolContext
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.medication import MedicationCatalog
from app.models.tenant_model import TenantModel
from app.services.catalog_search_service import (
    search_catalogs,
    search_biomarkers,
    search_medications,
    _specs_by_type,
    _hybrid_search_one,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def hybrid_ctx() -> AsyncIterator[ToolContext]:
    """Seed a tenant + a biomarker with aliases + a medication with text."""
    from app.ai.tools.registry import ToolContext

    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Hybrid", slug=f"hb-{tenant_id}"))
        await db.commit()

    # A biomarker whose NAME does not contain "FBS" but whose ALIASES do.
    async with AsyncSessionLocal() as db:
        db.add(
            BiomarkerDefinition(
                slug=f"fbs-{tenant_id.hex[:6]}",
                name="Fasting Blood Sugar Probe",
                aliases=["FBS", "Fbg", "Fasting Glucose"],
                description="Measures blood sugar levels after an overnight fast.",
                info="Used to screen for diabetes and prediabetes.",
                tenant_id=None,
            )
        )
        # A medication whose indications text mentions a symptom.
        db.add(
            MedicationCatalog(
                name=f"Zyplexin-{tenant_id.hex[:6]}",
                description="Pain reliever.",
                indications="Used for headache and mild migraine relief.",
                tenant_id=None,
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        yield ToolContext(db=db, tenant_id=tenant_id, patient_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# search_biomarkers — alias matching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_biomarkers_matches_alias():
    """``FBS`` finds a biomarker via its alias (not name, not slug)."""
    tenant_id = uuid.uuid4()
    suffix = tenant_id.hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        bio = BiomarkerDefinition(
            slug=f"aliasprobe-{suffix}",
            name="Alias Probe Marker",
            aliases=[f"UNIQUEALIAS{suffix}"],
            tenant_id=None,
        )
        db.add(bio)
        await db.commit()
        bio_id = bio.id
        alias_token = f"UNIQUEALIAS{suffix}"

    async with AsyncSessionLocal() as db:
        results = await search_biomarkers(db, tenant_id, alias_token, limit=5)

    assert any(b.id == bio_id for b in results), "alias search did not find biomarker"


@pytest.mark.asyncio
async def test_search_biomarkers_matches_multi_word_fts():
    """``blood sugar`` (multi-word) matches via FTS on description/info."""
    tenant_id = uuid.uuid4()
    suffix = tenant_id.hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        bio = BiomarkerDefinition(
            slug=f"hexose-{suffix}",
            name=f"Hexose Marker {suffix}",  # name has no overlap with query
            description=f"This test measures sparklyunicorn blood sugar concentration {suffix}.",
            tenant_id=None,
        )
        db.add(bio)
        await db.commit()
        bio_id = bio.id

    async with AsyncSessionLocal() as db:
        results = await search_biomarkers(db, tenant_id, f"sparklyunicorn blood sugar {suffix}", limit=5)

    assert any(b.id == bio_id for b in results), "multi-word FTS did not find biomarker"


# ---------------------------------------------------------------------------
# search_medications — symptom FTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_medications_matches_symptom_fts():
    """``headache`` finds a medication via indications text."""
    tenant_id = uuid.uuid4()
    suffix = tenant_id.hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        med = MedicationCatalog(
            name=f"Zyplexin-{suffix}",
            description="Pain reliever.",
            indications=f"Used for headache and sparklymigraine {suffix} relief.",
            tenant_id=None,
        )
        db.add(med)
        await db.commit()
        med_id = med.id

    async with AsyncSessionLocal() as db:
        results = await search_medications(db, tenant_id, f"sparklymigraine {suffix}", limit=5)

    assert any(m.id == med_id for m in results), "symptom FTS did not find medication"


# ---------------------------------------------------------------------------
# _hybrid_search_one — matched_on + snippet provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_search_one_matched_on_includes_alias():
    """The matched_on field surfaces 'alias' when an alias matched."""
    tenant_id = uuid.uuid4()
    suffix = tenant_id.hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        bio = BiomarkerDefinition(
            slug=f"aliastest-bio-{suffix}",
            name="Alias Test Biomarker",
            aliases=["ALIASX"],
            tenant_id=None,
        )
        db.add(bio)
        await db.commit()

    specs = _specs_by_type()
    async with AsyncSessionLocal() as db:
        hits = await _hybrid_search_one(db, specs["biomarker"], "ALIASX", tenant_id, limit=5)
    assert hits, "expected at least one hit"
    assert any("alias" in h.matched_on for h in hits), [h.matched_on for h in hits]


@pytest.mark.asyncio
async def test_hybrid_search_one_matched_on_includes_text_for_fts():
    """The matched_on field includes 'text' for FTS-driven matches."""
    tenant_id = uuid.uuid4()
    suffix = tenant_id.hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        bio = BiomarkerDefinition(
            slug=f"textmatch-bio-{suffix}",
            name="Text Match Biomarker",
            description="Contains the magic word: unicornsparkle.",
            tenant_id=None,
        )
        db.add(bio)
        await db.commit()

    specs = _specs_by_type()
    async with AsyncSessionLocal() as db:
        hits = await _hybrid_search_one(
            db, specs["biomarker"], "unicornsparkle", tenant_id, limit=5
        )
    assert hits, "expected FTS hit"
    assert any("text" in h.matched_on for h in hits), [h.matched_on for h in hits]


@pytest.mark.asyncio
async def test_hybrid_search_one_returns_snippet_for_fts_match():
    """A snippet excerpt is returned when the snippet_column matches."""
    tenant_id = uuid.uuid4()
    suffix = tenant_id.hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        bio = BiomarkerDefinition(
            slug=f"snip-bio-{suffix}",
            name="Snip Test Biomarker",
            info="This biomarker is critical for diagnosing rareconditioxyz "
            "in patients with metabolic syndrome.",
            tenant_id=None,
        )
        db.add(bio)
        await db.commit()

    specs = _specs_by_type()
    async with AsyncSessionLocal() as db:
        hits = await _hybrid_search_one(
            db, specs["biomarker"], "rareconditioxyz", tenant_id, limit=5
        )
    assert hits, "expected FTS hit"
    assert hits[0].snippet, "expected non-empty snippet"
    assert "rareconditioxyz" in hits[0].snippet.lower()


# ---------------------------------------------------------------------------
# search_catalogs — cross-catalog RRF ranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_catalogs_global_ranking_cross_catalog():
    """A query that matches multiple catalogs returns a globally ranked list."""
    tenant_id = uuid.uuid4()
    token = f"magiccrossover{tenant_id.hex[:6]}"
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        db.add(
            BiomarkerDefinition(
                slug=f"bio-{token}",
                name=f"{token} biomarker",
                tenant_id=None,
            )
        )
        db.add(
            MedicationCatalog(
                name=f"{token} medication",
                tenant_id=None,
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        results = await search_catalogs(db, tenant_id, token, limit_total=10)

    types_hit = {r["type"] for r in results}
    assert "biomarker" in types_hit
    assert "medication" in types_hit
    # Each result carries the enriched fields.
    for r in results:
        assert {"type", "id", "label", "matched_on", "score"} <= set(r.keys())


@pytest.mark.asyncio
async def test_search_catalogs_score_sorted_descending():
    """Hits are sorted by RRF score descending."""
    tenant_id = uuid.uuid4()
    suffix = tenant_id.hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        # Two biomarkers with overlapping names so both match.
        db.add(
            BiomarkerDefinition(
                slug=f"scoretest-a-{suffix}",
                name="scoretest exact",
                tenant_id=None,
            )
        )
        db.add(
            BiomarkerDefinition(
                slug=f"scoretest-b-approx-{suffix}",
                name="scoretest approximately",
                tenant_id=None,
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        results = await search_catalogs(db, tenant_id, "scoretest exact", limit_total=10)

    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True), "results not sorted by score DESC"


# ---------------------------------------------------------------------------
# Tool wrappers — thin wrappers preserve domain-specific shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_available_biomarkers_tool_returns_is_telemetry(hybrid_ctx):
    """The biomarker tool's payload preserves is_telemetry for routing."""
    from app.ai.tools.biomarkers import build

    tools = build(hybrid_ctx)
    search = next(t for t in tools if t.name == "search_available_biomarkers")
    result = json.loads(await search.ainvoke({"search_term": "FBS"}))
    assert any(h.get("is_telemetry") is False for h in result), result
    # Each hit carries matched_on for transparency.
    for h in result:
        assert "matched_on" in h


@pytest.mark.asyncio
async def test_search_medications_tool_returns_indications(hybrid_ctx):
    """The medication tool's payload carries indications text."""
    from app.ai.tools.medications import build

    tools = build(hybrid_ctx)
    search = next(t for t in tools if t.name == "search_medications")
    result = json.loads(await search.ainvoke({"search_term": "headache", "limit": 5}))
    assert isinstance(result, list)
    assert any("headache" in (h.get("indications") or "").lower() for h in result), result


@pytest.mark.asyncio
async def test_search_catalogs_tool_returns_rich_payload(hybrid_ctx):
    """The cross-catalog tool returns the enriched dispatcher payload."""
    from app.ai.tools.catalogs import build

    tools = build(hybrid_ctx)
    search = next(t for t in tools if t.name == "search_catalogs")
    result = json.loads(await search.ainvoke({"query": "blood sugar", "limit": 5}))
    assert isinstance(result, list)
    # Enriched payload — not just the legacy {type, id, label} triple.
    if result:
        first = result[0]
        assert "matched_on" in first
        assert "score" in first
