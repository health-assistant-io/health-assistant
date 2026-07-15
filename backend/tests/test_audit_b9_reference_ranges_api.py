"""Regression tests for the stratified reference-range CRUD API (audit B9/F3).

Covers the nested ``/biomarkers/{id}/reference-ranges`` endpoints: list,
create, update, delete; parent-not-found 404s; range-not-found 404s; input
validation (inverted range → 422); and the inherited-access model (a USER
cannot manage ranges for a system-scope biomarker → 403, SYSTEM_ADMIN can).
"""
from __future__ import annotations

import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.biomarker_model import BiomarkerDefinition
from app.models.tenant_model import TenantModel


async def _make_biomarker(*, tenant_id=None, slug=None) -> str:
    async with AsyncSessionLocal() as db:
        bio = BiomarkerDefinition(
            id=uuid.uuid4(),
            slug=slug or f"bio-{uuid.uuid4().hex[:8]}",
            name="TSH",
            tenant_id=tenant_id,
        )
        db.add(bio)
        await db.commit()
        return str(bio.id)


def _user_headers(tenant_id, role="USER") -> dict:
    token = create_access_token(
        {
            "sub": f"{role.lower()}@test.local",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": role,
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_crud_full_cycle(async_client, system_admin_headers):
    bio_id = await _make_biomarker()

    # Initially empty.
    resp = await async_client.get(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges", headers=system_admin_headers
    )
    assert resp.status_code == 200
    assert resp.json() == []

    # Create a catch-all + a male-specific range.
    catch_all = await async_client.post(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges",
        headers=system_admin_headers,
        json={"low": 0.4, "high": 4.0, "text": "0.4 - 4.0 mIU/L"},
    )
    assert catch_all.status_code == 201, catch_all.text
    catch_all_id = catch_all.json()["id"]

    male = await async_client.post(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges",
        headers=system_admin_headers,
        json={"sex": "MALE", "age_min": 19, "age_max": 99, "low": 0.5, "high": 5.0},
    )
    assert male.status_code == 201, male.text
    male_id = male.json()["id"]

    # List returns both.
    listed = await async_client.get(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges", headers=system_admin_headers
    )
    rows = listed.json()
    assert {r["id"] for r in rows} == {catch_all_id, male_id}
    # Sex serialises as the enum value.
    sexes = {r["sex"] for r in rows}
    assert "MALE" in sexes

    # Update the catch-all to widen the upper bound.
    updated = await async_client.put(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges/{catch_all_id}",
        headers=system_admin_headers,
        json={"high": 4.5},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["high"] == 4.5
    # Unsupplied fields are preserved.
    assert updated.json()["low"] == 0.4

    # Delete the male-specific row.
    deleted = await async_client.delete(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges/{male_id}",
        headers=system_admin_headers,
    )
    assert deleted.status_code == 200
    remaining = await async_client.get(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges", headers=system_admin_headers
    )
    assert {r["id"] for r in remaining.json()} == {catch_all_id}


@pytest.mark.asyncio
async def test_parent_not_found_returns_404(async_client, system_admin_headers):
    missing = uuid.uuid4()
    for method, extra in (("get", None), ("post", {"low": 1, "high": 2})):
        coro = getattr(async_client, method)(
            f"/api/v1/biomarkers/{missing}/reference-ranges",
            headers=system_admin_headers,
            **({"json": extra} if extra else {}),
        )
        resp = await coro
        assert resp.status_code == 404, (method, resp.text)


@pytest.mark.asyncio
async def test_range_not_found_returns_404(async_client, system_admin_headers):
    bio_id = await _make_biomarker()
    missing_range = uuid.uuid4()
    resp = await async_client.put(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges/{missing_range}",
        headers=system_admin_headers,
        json={"low": 1, "high": 2},
    )
    assert resp.status_code == 404
    resp = await async_client.delete(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges/{missing_range}",
        headers=system_admin_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_inverted_range_rejected(async_client, system_admin_headers):
    bio_id = await _make_biomarker()
    resp = await async_client.post(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges",
        headers=system_admin_headers,
        json={"low": 10, "high": 1},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_user_cannot_manage_system_scope_ranges(async_client, system_admin_headers):
    """A USER may read but not write ranges for a system (NULL-tenant) biomarker."""
    # system-scope biomarker
    bio_id = await _make_biomarker(tenant_id=None)

    async with AsyncSessionLocal() as db:
        # A separate tenant for the USER actor.
        tenant = TenantModel(
            id=uuid.uuid4(), name="UserTenant", slug=f"user-{uuid.uuid4().hex[:6]}"
        )
        db.add(tenant)
        await db.commit()
        user_tenant = tenant.id

    user = _user_headers(user_tenant, role="USER")

    # Read is allowed (system rows are visible).
    read = await async_client.get(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges", headers=user
    )
    assert read.status_code == 200

    # Write is denied (system-scope needs SYSTEM_ADMIN).
    created = await async_client.post(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges",
        headers=user,
        json={"low": 1, "high": 2},
    )
    assert created.status_code == 403, created.text


@pytest.mark.asyncio
async def test_biomarker_response_includes_reference_ranges(
    async_client, system_admin_headers
):
    bio_id = await _make_biomarker()
    await async_client.post(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges",
        headers=system_admin_headers,
        json={"sex": "FEMALE", "low": 0.4, "high": 3.5},
    )
    resp = await async_client.get(
        "/api/v1/biomarkers/", headers=system_admin_headers
    )
    mine = next(b for b in resp.json() if b["id"] == bio_id)
    assert len(mine["reference_ranges"]) == 1
    assert mine["reference_ranges"][0]["sex"] == "FEMALE"


@pytest.mark.asyncio
async def test_reference_ranges_endpoint_reads_from_db(async_client, system_admin_headers):
    """GET /analytics/reference-ranges now reflects the catalog, not a hardcoded dict."""
    slug = f"hdl-{uuid.uuid4().hex[:6]}"
    await _make_biomarker(slug=slug)
    # Give it a legacy global range.
    async with AsyncSessionLocal() as db:
        from sqlalchemy import update

        from app.models.biomarker_model import BiomarkerDefinition

        await db.execute(
            update(BiomarkerDefinition)
            .where(BiomarkerDefinition.slug == slug)
            .values(reference_range_min=1.0, reference_range_max=2.0)
        )
        await db.commit()

    resp = await async_client.get(
        "/api/v1/analytics/reference-ranges", headers=system_admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert slug in body
    assert body[slug]["min"] == 1.0 and body[slug]["max"] == 2.0


@pytest.mark.asyncio
async def test_catalog_adapter_serializes_reference_ranges(
    async_client, system_admin_headers
):
    """The /catalogs/biomarker surface must include stratified ranges (edit form)."""
    bio_id = await _make_biomarker()
    await async_client.post(
        f"/api/v1/biomarkers/{bio_id}/reference-ranges",
        headers=system_admin_headers,
        json={"sex": "FEMALE", "age_min": 18, "age_max": 65, "low": 0.4, "high": 3.5},
    )

    resp = await async_client.get(
        f"/api/v1/catalogs/biomarker/{bio_id}", headers=system_admin_headers
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()
    assert "reference_ranges" in item
    assert len(item["reference_ranges"]) == 1
    rr = item["reference_ranges"][0]
    assert rr["sex"] == "FEMALE"
    assert rr["age_min"] == 18 and rr["age_max"] == 65
    assert rr["low"] == 0.4 and rr["high"] == 3.5

