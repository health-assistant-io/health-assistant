"""``/catalogs`` meta-layer API tests — Phase 0.

Real-DB integration tests (conftest runs alembic migrations). Validates that
the meta-layer delegates correctly to each catalog's adapter and that reads are
tenant-scoped (global + caller's tenant visible; other tenants hidden).
"""

import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.medication import MedicationCatalog
from app.models.tenant_model import TenantModel


@pytest.mark.asyncio
async def test_list_catalog_types(async_client, system_admin_headers):
    resp = await async_client.get("/api/v1/catalogs", headers=system_admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    types = {t["type"] for t in body["types"]}
    assert types == {
        "biomarker",
        "medication",
        "allergy",
        "anatomy",
        "concept",
        "vaccine",
    }
    for entry in body["types"]:
        assert entry["ui"]["label_key"]
        assert entry["ui"]["icon"]
        assert entry["edge_endpoint_type"]
        assert isinstance(entry["search_columns"], list)


@pytest.mark.asyncio
async def test_list_catalog_types_requires_auth(async_client):
    resp = await async_client.get("/api/v1/catalogs")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_relation_types(async_client, system_admin_headers):
    """GET /catalogs/relation-types returns one richly-described entry per
    ConceptRelationType member (value/label/group/description/icon)."""
    from app.models.enums import ConceptRelationType

    resp = await async_client.get(
        "/api/v1/catalogs/relation-types", headers=system_admin_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    values = {it["value"] for it in items}
    assert values == {rt.value for rt in ConceptRelationType}
    for it in items:
        assert it["label"]
        assert it["group"]
        assert it["description"]
        assert it["icon"]["type"] == "lucide"
        assert it["icon"]["value"]


@pytest.mark.asyncio
async def test_list_relation_types_requires_auth(async_client):
    resp = await async_client.get("/api/v1/catalogs/relation-types")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_unknown_catalog_type_returns_404(async_client, system_admin_headers):
    resp = await async_client.get(
        "/api/v1/catalogs/nonexistent", headers=system_admin_headers
    )
    assert resp.status_code == 404
    assert "nonexistent" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_biomarker_list_tenant_scoped(async_client, system_admin_headers):
    """Global + caller's tenant visible; another tenant hidden."""
    caller_tenant = _tenant_id_from_headers(system_admin_headers)
    other_tenant = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=other_tenant, name="Other", slug=f"other-{other_tenant}"))
        await db.commit()

    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                BiomarkerDefinition(
                    slug=f"global-{suffix}", name=f"Global {suffix}", tenant_id=None
                ),
                BiomarkerDefinition(
                    slug=f"mine-{suffix}",
                    name=f"Mine {suffix}",
                    tenant_id=caller_tenant,
                ),
                BiomarkerDefinition(
                    slug=f"theirs-{suffix}",
                    name=f"Theirs {suffix}",
                    tenant_id=other_tenant,
                ),
            ]
        )
        await db.commit()

    # Search for this run's suffix so the assertion is isolated to these 3 rows
    # (the catalog accumulates global rows across test runs, which would
    # otherwise push the tenant-scoped row past the default list limit).
    resp = await async_client.get(
        f"/api/v1/catalogs/biomarker?search={suffix}", headers=system_admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    slugs = {item["slug"] for item in body["items"]}
    assert f"global-{suffix}" in slugs
    assert f"mine-{suffix}" in slugs
    assert f"theirs-{suffix}" not in slugs


@pytest.mark.asyncio
async def test_biomarker_item_has_expected_shape(async_client, system_admin_headers):
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        bio = BiomarkerDefinition(
            slug=f"shape-{suffix}", name=f"Shape {suffix}", tenant_id=None
        )
        db.add(bio)
        await db.commit()
        await db.refresh(bio)
        bio_id = bio.id

    resp = await async_client.get(
        f"/api/v1/catalogs/biomarker/{bio_id}", headers=system_admin_headers
    )
    assert resp.status_code == 200
    item = resp.json()
    for key in (
        "id",
        "slug",
        "coding_system",
        "code",
        "name",
        "category",
        "aliases",
        "preferred_unit_id",
        "info",
        "reference_range_min",
        "reference_range_max",
        "is_telemetry",
        "meta_data",
        "preferred_unit_symbol",
    ):
        assert key in item, f"missing key {key}"
    assert item["slug"] == f"shape-{suffix}"


@pytest.mark.asyncio
async def test_biomarker_get_missing_returns_404(async_client, system_admin_headers):
    missing_id = uuid.uuid4()
    resp = await async_client.get(
        f"/api/v1/catalogs/biomarker/{missing_id}", headers=system_admin_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_biomarker_get_cross_tenant_hidden(async_client, system_admin_headers):
    """A biomarker in another tenant is not retrievable (404, not a leak)."""
    other_tenant = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(id=other_tenant, name="Other", slug=f"other2-{other_tenant}")
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        bio = BiomarkerDefinition(
            slug=f"secret-{suffix}", name=f"Secret {suffix}", tenant_id=other_tenant
        )
        db.add(bio)
        await db.commit()
        await db.refresh(bio)
        bio_id = bio.id

    resp = await async_client.get(
        f"/api/v1/catalogs/biomarker/{bio_id}", headers=system_admin_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_medication_list_delegates(async_client, system_admin_headers):
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(MedicationCatalog(name=f"Test drug {suffix}", tenant_id=None))
        await db.commit()

    # Search for this run's suffix so the assertion is isolated (the catalog
    # accumulates rows across test runs, which would push the new drug past the
    # default list limit).
    resp = await async_client.get(
        f"/api/v1/catalogs/medication?search={suffix}", headers=system_admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(d["name"] == f"Test drug {suffix}" for d in body["items"])


@pytest.mark.asyncio
async def test_medication_search_filters(async_client, system_admin_headers):
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(MedicationCatalog(name=f"Uniquedrugname {suffix}", tenant_id=None))
        await db.commit()

    resp = await async_client.get(
        f"/api/v1/catalogs/medication?search=Uniquedrugname%20{suffix}",
        headers=system_admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(d["name"] == f"Uniquedrugname {suffix}" for d in body["items"])


def _tenant_id_from_headers(headers):
    """Extract the tenant UUID from the JWT in the auth header (best-effort)."""
    from app.core.security import decode_access_token

    token = headers["Authorization"].removeprefix("Bearer ")
    payload = decode_access_token(token)
    return uuid.UUID(payload["tenant_id"])
