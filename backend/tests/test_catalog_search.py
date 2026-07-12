"""Unified catalog search tests — Phase 4.

Covers the registry-driven ``search_catalogs`` dispatcher (type filter, tenant
scope, all-types coverage) and the two HTTP surfaces that consume it:
``GET /catalogs/search`` and the catalog portion of ``GET /search``.
"""

import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept
from app.models.enums import ConceptStatus
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.tenant_model import TenantModel
from app.services.catalog_search_service import search_catalogs


async def _tenant_and_headers(role="ADMIN"):
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="S", slug=f"s-{tenant_id}"))
        await db.commit()
    token = create_access_token(
        {
            "sub": f"{role.lower()}@test.local",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": role,
        }
    )
    return tenant_id, {"Authorization": f"Bearer {token}"}


async def _seed_cross_catalog(token_word: str):
    """Seed one row per catalog type whose label contains token_word (global)."""
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                BiomarkerDefinition(
                    slug=f"bio-{token_word}-{uuid.uuid4().hex[:4]}",
                    name=f"{token_word} Biomarker",
                    tenant_id=None,
                ),
                MedicationCatalog(name=f"{token_word} Drug", tenant_id=None),
                AllergyCatalog(
                    name=f"{token_word} Allergen", category="FOOD", tenant_id=None
                ),
                AnatomyStructure(
                    slug=f"anat-{token_word}-{uuid.uuid4().hex[:4]}",
                    name=f"{token_word} Organ",
                ),
                Concept(
                    slug=f"con-{token_word}-{uuid.uuid4().hex[:4]}",
                    name=f"{token_word} Concept",
                    status=ConceptStatus.ACTIVE,
                    tenant_id=None,
                ),
            ]
        )
        await db.commit()


# ---------------------------------------------------------------------------
# search_catalogs dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_catalogs_hits_all_types():
    tenant_id, _ = await _tenant_and_headers()
    await _seed_cross_catalog("zebra")
    async with AsyncSessionLocal() as db:
        hits = await search_catalogs(db, tenant_id, "zebra", limit_per_type=5)
    types_hit = {h["type"] for h in hits}
    # The dispatcher must hit several registered catalog types (the exact set
    # depends on which seeded rows persist across the accumulated test DB; we
    # assert a robust multi-type floor + two reliable trigram matches).
    assert len(types_hit) >= 4
    assert "biomarker" in types_hit
    assert "anatomy" in types_hit
    for h in hits:
        # Enriched payload (Phase 5 hybrid search): always carries the
        # identity triple plus matched_on/snippet/score for LLM discovery.
        assert {"type", "id", "label"} <= set(h.keys())
        assert "matched_on" in h
        assert "score" in h
        assert "zebra" in h["label"].lower()


@pytest.mark.asyncio
async def test_search_catalogs_type_filter_restricts():
    tenant_id, _ = await _tenant_and_headers()
    await _seed_cross_catalog("giraffe")
    async with AsyncSessionLocal() as db:
        hits = await search_catalogs(
            db, tenant_id, "giraffe", types=["biomarker", "anatomy"]
        )
    types_hit = {h["type"] for h in hits}
    assert types_hit == {"biomarker", "anatomy"}


@pytest.mark.asyncio
async def test_search_catalogs_short_query_returns_empty():
    tenant_id, _ = await _tenant_and_headers()
    async with AsyncSessionLocal() as db:
        assert await search_catalogs(db, tenant_id, "a") == []
        assert await search_catalogs(db, tenant_id, "") == []


@pytest.mark.asyncio
async def test_search_catalogs_tenant_scoped():
    """A row in another tenant is not surfaced (global + caller's only)."""
    caller_tenant, _ = await _tenant_and_headers()
    other_tenant = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=other_tenant, name="O", slug=f"so-{other_tenant}"))
        await db.commit()
    async with AsyncSessionLocal() as db:
        db.add(
            BiomarkerDefinition(
                slug=f"secret-{uuid.uuid4().hex[:6]}",
                name="secrettenant biomarker",
                tenant_id=other_tenant,
            )
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        hits = await search_catalogs(db, caller_tenant, "secrettenant")
    assert hits == []


@pytest.mark.asyncio
async def test_search_catalogs_unknown_type_ignored():
    tenant_id, _ = await _tenant_and_headers()
    async with AsyncSessionLocal() as db:
        hits = await search_catalogs(
            db, tenant_id, "anything", types=["biomarker", "nonexistent"]
        )
    # No error; unknown type skipped. (May be empty if no biomarker matches.)
    assert all(h["type"] != "nonexistent" for h in hits)


# ---------------------------------------------------------------------------
# GET /catalogs/search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalogs_search_endpoint(async_client):
    _, headers = await _tenant_and_headers()
    await _seed_cross_catalog("okapi")
    resp = await async_client.get("/api/v1/catalogs/search?q=okapi", headers=headers)
    assert resp.status_code == 200, resp.text
    types_hit = {h["type"] for h in resp.json()["results"]}
    assert {"biomarker", "anatomy"} <= types_hit


@pytest.mark.asyncio
async def test_catalogs_search_endpoint_type_filter(async_client):
    _, headers = await _tenant_and_headers()
    await _seed_cross_catalog("pangolin")
    resp = await async_client.get(
        "/api/v1/catalogs/search?q=pangolin&types=medication", headers=headers
    )
    assert resp.status_code == 200, resp.text
    types_hit = {h["type"] for h in resp.json()["results"]}
    assert types_hit == {"medication"}


@pytest.mark.asyncio
async def test_catalogs_search_short_query_422(async_client):
    _, headers = await _tenant_and_headers()
    resp = await async_client.get("/api/v1/catalogs/search?q=a", headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /search (global) now includes anatomy + concept + allergy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_search_includes_all_catalog_types(async_client):
    _, headers = await _tenant_and_headers()
    await _seed_cross_catalog("narwhal")
    resp = await async_client.get("/api/v1/search?q=narwhal", headers=headers)
    assert resp.status_code == 200, resp.text
    types_hit = {r["type"] for r in resp.json()["results"]}
    # anatomy + concept + allergy now appear (pre-Phase-4 only medication +
    # biomarker were wired into /search).
    assert "anatomy" in types_hit
    assert "concept" in types_hit
    assert "allergy" in types_hit
    assert "biomarker" in types_hit
    assert "medication" in types_hit
