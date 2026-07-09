"""``/catalogs`` meta-layer RBAC tests — Phase 1 + Phase A (scope).

Validates the uniform :class:`~app.catalogs.policy.CatalogAccessPolicy` across the
meta-layer write routes for every catalog type, with USER / ADMIN / SYSTEM_ADMIN
tokens all minted for the SAME tenant (so the role/scope logic is exercised on
identical data):

- USER create -> 201 (lands in user-scope, Phase A ownership model).
- ADMIN create + tenant-row update/delete -> 2xx; ADMIN global-row update/delete -> 403.
- SYSTEM_ADMIN global-row update/delete -> 2xx.
- cross-tenant write -> 404 (row invisible, never a leak).
"""

import uuid
from typing import Any, Callable, Dict, Tuple

import pytest

from app.core.database import AsyncSessionLocal
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.biomarker_model import BiomarkerDefinition
from app.models.tenant_model import TenantModel

ROLES = ["USER", "ADMIN", "SYSTEM_ADMIN"]


async def _make_shared_tenant(
    roles=ROLES,
) -> Tuple[uuid.UUID, Dict[str, Dict[str, str]]]:
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="RBAC", slug=f"rbac-{tenant_id}"))
        await db.commit()
    headers: Dict[str, Dict[str, str]] = {}
    for role in roles:
        token = create_access_token(
            {
                "sub": f"{role.lower()}@test.local",
                "user_id": str(uuid.uuid4()),
                "tenant_id": str(tenant_id),
                "role": role,
            }
        )
        headers[role] = {"Authorization": f"Bearer {token}"}
    return tenant_id, headers


def _model_factory(type: str) -> Callable:
    if type == "biomarker":
        return lambda tid, name: BiomarkerDefinition(
            slug=f"slug-{name}", name=name, tenant_id=tid
        )
    if type == "allergy":
        return lambda tid, name: AllergyCatalog(
            name=name, category="FOOD", tenant_id=tid
        )
    return lambda tid, name: MedicationCatalog(name=name, tenant_id=tid)


async def _create_row(type: str, tenant_id, name: str) -> str:
    """Insert a catalog row directly and return its id (string)."""
    factory = _model_factory(type)
    async with AsyncSessionLocal() as db:
        obj = factory(tenant_id, name)
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return str(obj.id)


def _create_payload(type: str, suffix: str) -> Dict[str, Any]:
    name = f"Item {suffix}"
    if type == "biomarker":
        return {"slug": f"item-{suffix}", "name": name}
    if type == "allergy":
        return {"name": name, "category": "FOOD"}
    return {"name": name}


CATALOG_TYPES = ["biomarker", "medication", "allergy"]


@pytest.mark.parametrize("type", CATALOG_TYPES)
@pytest.mark.asyncio
async def test_user_create_lands_in_user_scope(async_client, type):
    """Phase A: any authenticated user may create; the entry lands in
    user-scope (visible to the tenant, editable only by creator + admins)."""
    _, headers = await _make_shared_tenant(["USER"])
    resp = await async_client.post(
        f"/api/v1/catalogs/{type}",
        json=_create_payload(type, uuid.uuid4().hex[:8]),
        headers=headers["USER"],
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["scope"] == "user"


@pytest.mark.parametrize("type", CATALOG_TYPES)
@pytest.mark.asyncio
async def test_admin_tenant_row_lifecycle(async_client, type):
    _, headers = await _make_shared_tenant(["ADMIN"])
    suffix = uuid.uuid4().hex[:8]
    create = await async_client.post(
        f"/api/v1/catalogs/{type}",
        json=_create_payload(type, suffix),
        headers=headers["ADMIN"],
    )
    assert create.status_code == 201, create.text
    item_id = create.json()["id"]

    update = await async_client.put(
        f"/api/v1/catalogs/{type}/{item_id}",
        json={"name": f"Renamed {suffix}"},
        headers=headers["ADMIN"],
    )
    assert update.status_code == 200, update.text
    assert update.json()["name"] == f"Renamed {suffix}"

    delete = await async_client.delete(
        f"/api/v1/catalogs/{type}/{item_id}", headers=headers["ADMIN"]
    )
    assert delete.status_code == 200, delete.text


@pytest.mark.parametrize("type", CATALOG_TYPES)
@pytest.mark.asyncio
async def test_admin_cannot_modify_global_row(async_client, type):
    _, headers = await _make_shared_tenant(["ADMIN"])
    suffix = uuid.uuid4().hex[:8]
    global_id = await _create_row(type, None, f"Global {suffix}")

    put = await async_client.put(
        f"/api/v1/catalogs/{type}/{global_id}",
        json={"name": f"Hacked {suffix}"},
        headers=headers["ADMIN"],
    )
    assert put.status_code == 403, put.text

    delete = await async_client.delete(
        f"/api/v1/catalogs/{type}/{global_id}", headers=headers["ADMIN"]
    )
    assert delete.status_code == 403, delete.text


@pytest.mark.parametrize("type", CATALOG_TYPES)
@pytest.mark.asyncio
async def test_system_admin_can_delete_global_row(async_client, type):
    _, headers = await _make_shared_tenant(["SYSTEM_ADMIN"])
    suffix = uuid.uuid4().hex[:8]
    global_id = await _create_row(type, None, f"Globalsys {suffix}")

    delete = await async_client.delete(
        f"/api/v1/catalogs/{type}/{global_id}", headers=headers["SYSTEM_ADMIN"]
    )
    assert delete.status_code == 200, delete.text


@pytest.mark.parametrize("type", CATALOG_TYPES)
@pytest.mark.asyncio
async def test_cross_tenant_write_is_404(async_client, type):
    """A row owned by another tenant is invisible (404), not writable."""
    other_tenant, _ = await _make_shared_tenant(["ADMIN"])
    _, headers = await _make_shared_tenant(["ADMIN"])
    suffix = uuid.uuid4().hex[:8]
    secret_id = await _create_row(type, other_tenant, f"Secret {suffix}")

    put = await async_client.put(
        f"/api/v1/catalogs/{type}/{secret_id}",
        json={"name": "stolen"},
        headers=headers["ADMIN"],
    )
    assert put.status_code == 404, put.text
