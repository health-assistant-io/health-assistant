"""Domain catalog endpoint tests — Phase 1.

Covers the domain-endpoint deliverables (not the meta-layer, which lives in
``test_catalog_rbac.py``):

- Allergy catalog full CRUD round-trip via ``/allergies/catalog*`` (the new
  get-one / update / delete routes + RBAC on create).
- Medication catalog delete route + RBAC on create/update.
- Biomarker RBAC on the domain write routes + the tenant-scoping read fix
  (legacy global-leak closed: a tenant no longer sees another tenant's defs).
"""

import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.biomarker_model import BiomarkerDefinition
from app.models.tenant_model import TenantModel

ROLES = ["USER", "ADMIN", "SYSTEM_ADMIN"]


async def _tenant_and_headers(role: str):
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"dom-{tenant_id}"))
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


# ---------------------------------------------------------------------------
# Allergy catalog CRUD round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allergy_catalog_full_crud(async_client):
    _, admin = await _tenant_and_headers("ADMIN")
    suffix = uuid.uuid4().hex[:8]

    # create
    create = await async_client.post(
        "/api/v1/allergies/catalog",
        json={"name": f"Pollen {suffix}", "category": "ENVIRONMENT"},
        headers=admin,
    )
    assert create.status_code == 200, create.text
    cat_id = create.json()["id"]

    # get-one
    get = await async_client.get(f"/api/v1/allergies/catalog/{cat_id}", headers=admin)
    assert get.status_code == 200, get.text
    assert get.json()["name"] == f"Pollen {suffix}"

    # update
    put = await async_client.put(
        f"/api/v1/allergies/catalog/{cat_id}",
        json={"description": "seasonal allergen"},
        headers=admin,
    )
    assert put.status_code == 200, put.text
    assert put.json()["description"] == "seasonal allergen"

    # delete
    delete = await async_client.delete(
        f"/api/v1/allergies/catalog/{cat_id}", headers=admin
    )
    assert delete.status_code == 200, delete.text

    # now 404
    get_after = await async_client.get(
        f"/api/v1/allergies/catalog/{cat_id}", headers=admin
    )
    assert get_after.status_code == 404


@pytest.mark.asyncio
async def test_allergy_catalog_get_missing_404(async_client):
    _, admin = await _tenant_and_headers("ADMIN")
    resp = await async_client.get(
        f"/api/v1/allergies/catalog/{uuid.uuid4()}", headers=admin
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_allergy_catalog_user_creates_user_scope(async_client):
    """Phase A: a USER may create via the domain endpoint; it lands in
    user-scope (visible to the tenant, owned by the creator)."""
    _, user = await _tenant_and_headers("USER")
    resp = await async_client.post(
        "/api/v1/allergies/catalog",
        json={"name": "x", "category": "FOOD"},
        headers=user,
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_allergy_catalog_admin_cannot_delete_global(async_client):
    _, admin = await _tenant_and_headers("ADMIN")
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        entry = AllergyCatalog(name=f"Global {suffix}", category="FOOD", tenant_id=None)
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        gid = str(entry.id)
    resp = await async_client.delete(f"/api/v1/allergies/catalog/{gid}", headers=admin)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Medication catalog delete + RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_medication_catalog_delete(async_client):
    _, admin = await _tenant_and_headers("ADMIN")
    suffix = uuid.uuid4().hex[:8]
    create = await async_client.post(
        "/api/v1/medications/catalog",
        json={"name": f"Drug {suffix}"},
        headers=admin,
    )
    assert create.status_code == 200, create.text
    med_id = create.json()["id"]

    delete = await async_client.delete(
        f"/api/v1/medications/catalog/{med_id}", headers=admin
    )
    assert delete.status_code == 200, delete.text


@pytest.mark.asyncio
async def test_medication_catalog_user_creates_user_scope(async_client):
    """Phase A: a USER may create via the domain endpoint; it lands in
    user-scope."""
    _, user = await _tenant_and_headers("USER")
    resp = await async_client.post(
        "/api/v1/medications/catalog",
        json={"name": "x"},
        headers=user,
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_medication_catalog_admin_cannot_delete_global(async_client):
    _, admin = await _tenant_and_headers("ADMIN")
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        entry = MedicationCatalog(name=f"Global {suffix}", tenant_id=None)
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        gid = str(entry.id)
    resp = await async_client.delete(
        f"/api/v1/medications/catalog/{gid}", headers=admin
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Biomarker domain RBAC + tenant-scoping read fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_biomarker_user_creates_user_scope(async_client):
    """Phase A: a USER may create a biomarker; it lands in user-scope."""
    _, user = await _tenant_and_headers("USER")
    resp = await async_client.post(
        "/api/v1/biomarkers/",
        json={"slug": f"u-{uuid.uuid4().hex[:6]}", "name": "x"},
        headers=user,
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_biomarker_reads_are_tenant_scoped(async_client):
    """Legacy global-leak fix: a tenant sees global + own, never another tenant."""
    caller_tenant, admin = await _tenant_and_headers("ADMIN")
    other_tenant = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=other_tenant, name="O", slug=f"bio-o-{other_tenant}"))
        await db.commit()
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                BiomarkerDefinition(
                    slug=f"bio-global-{suffix}", name=f"G {suffix}", tenant_id=None
                ),
                BiomarkerDefinition(
                    slug=f"bio-mine-{suffix}",
                    name=f"M {suffix}",
                    tenant_id=caller_tenant,
                ),
                BiomarkerDefinition(
                    slug=f"bio-theirs-{suffix}",
                    name=f"T {suffix}",
                    tenant_id=other_tenant,
                ),
            ]
        )
        await db.commit()

    resp = await async_client.get("/api/v1/biomarkers/", headers=admin)
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()}
    assert f"bio-global-{suffix}" in slugs
    assert f"bio-mine-{suffix}" in slugs
    assert f"bio-theirs-{suffix}" not in slugs


@pytest.mark.asyncio
async def test_biomarker_admin_can_create_and_delete(async_client):
    _, admin = await _tenant_and_headers("ADMIN")
    suffix = uuid.uuid4().hex[:8]
    create = await async_client.post(
        "/api/v1/biomarkers/",
        json={"slug": f"admin-{suffix}", "name": f"Admin {suffix}"},
        headers=admin,
    )
    assert create.status_code == 200, create.text
    bio_id = create.json()["id"]

    delete = await async_client.delete(f"/api/v1/biomarkers/{bio_id}", headers=admin)
    assert delete.status_code == 200, delete.text
