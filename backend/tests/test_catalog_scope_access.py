"""Catalog scope + ownership-based access control — Phase A.

Validates the new ownership-based ``CatalogAccessPolicy`` over the
``/catalogs`` meta-layer for every catalog type. Three roles (USER, ADMIN,
SYSTEM_ADMIN) are all minted for the SAME tenant so the scope/ownership matrix
is exercised on identical data:

- USER creates  -> 201, user-scope, ``created_by`` set, visible to tenant.
- USER updates own  -> 200.
- USER updates someone else's user-scope row  -> 403.
- USER updates system row  -> 403.
- USER promotes  -> 403.
- ADMIN promotes user -> tenant  -> 200; scope flips; audit of flip via scope.
- SYSTEM_ADMIN promotes tenant -> system  -> 200.
- ``scope`` query filter narrows the list.
- Migration backfill: NULL tenant -> system, non-NULL -> tenant.
"""

import uuid
from typing import Any, Callable, Dict, Tuple

import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.vaccine import VaccineCatalog
from app.models.tenant_model import TenantModel

ROLES = ["USER", "ADMIN", "SYSTEM_ADMIN"]

# Catalog types that expose create/update/delete through the meta-layer and
# carry the new ``scope`` column. (concept/anatomy keep the column too but their
# domain write paths are gated separately.)
WRITE_TYPES = ["biomarker", "medication", "allergy", "vaccine"]


async def _make_shared_tenant(
    roles=ROLES,
) -> Tuple[uuid.UUID, Dict[str, Dict[str, str]], Dict[str, uuid.UUID]]:
    """Create one tenant + one JWT header per role + a stable user_id per role."""
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Scope", slug=f"scope-{tenant_id}"))
        await db.commit()
    headers: Dict[str, Dict[str, str]] = {}
    user_ids: Dict[str, uuid.UUID] = {}
    for role in roles:
        uid = uuid.uuid4()
        user_ids[role] = uid
        token = create_access_token(
            {
                "sub": f"{role.lower()}@test.local",
                "user_id": str(uid),
                "tenant_id": str(tenant_id),
                "role": role,
            }
        )
        headers[role] = {"Authorization": f"Bearer {token}"}
    return tenant_id, headers, user_ids


def _model_factory(type: str) -> Callable:
    if type == "biomarker":
        return lambda: BiomarkerDefinition(slug=f"slug-{uuid.uuid4().hex[:8]}", name="x")
    if type == "allergy":
        return lambda: AllergyCatalog(name="x", category="FOOD")
    if type == "vaccine":
        return lambda: VaccineCatalog(slug=f"slug-{uuid.uuid4().hex[:8]}", name="x")
    return lambda: MedicationCatalog(name="x")


def _model_class(type: str):
    return {
        "biomarker": BiomarkerDefinition,
        "allergy": AllergyCatalog,
        "vaccine": VaccineCatalog,
        "medication": MedicationCatalog,
    }[type]


def _create_payload(type: str, suffix: str) -> Dict[str, Any]:
    name = f"Item {suffix}"
    if type == "biomarker":
        return {"slug": f"item-{suffix}", "name": name}
    if type == "allergy":
        return {"name": name, "category": "FOOD"}
    if type == "vaccine":
        return {"slug": f"item-{suffix}", "name": name}
    return {"name": name}


async def _insert_row(
    type: str,
    *,
    tenant_id,
    scope: str,
    created_by=None,
) -> str:
    """Insert a catalog row directly with an explicit scope; return its id."""
    obj = _model_factory(type)()
    obj.tenant_id = tenant_id
    obj.scope = scope
    if created_by is not None:
        obj.created_by = created_by
    async with AsyncSessionLocal() as db:
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return str(obj.id)


# ---------------------------------------------------------------------------
# Create -> role-derived scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_user_create_lands_in_user_scope(async_client, type):
    _, headers, _ = await _make_shared_tenant(["USER"])
    suffix = uuid.uuid4().hex[:8]
    resp = await async_client.post(
        f"/api/v1/catalogs/{type}",
        json=_create_payload(type, suffix),
        headers=headers["USER"],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope"] == "user"
    item_id = body["id"]

    # Visible to the tenant — fetch by id (authoritative read-back).
    fetched = await async_client.get(
        f"/api/v1/catalogs/{type}/{item_id}", headers=headers["USER"]
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == item_id


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_admin_create_lands_in_tenant_scope(async_client, type):
    _, headers, _ = await _make_shared_tenant(["ADMIN"])
    resp = await async_client.post(
        f"/api/v1/catalogs/{type}",
        json=_create_payload(type, uuid.uuid4().hex[:8]),
        headers=headers["ADMIN"],
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["scope"] == "tenant"


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_system_admin_create_lands_in_system_scope(async_client, type):
    _, headers, _ = await _make_shared_tenant(["SYSTEM_ADMIN"])
    resp = await async_client.post(
        f"/api/v1/catalogs/{type}",
        json=_create_payload(type, uuid.uuid4().hex[:8]),
        headers=headers["SYSTEM_ADMIN"],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope"] == "system"
    assert body.get("tenant_id") is None


# ---------------------------------------------------------------------------
# Update -> ownership matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_user_updates_own_user_scope_row(async_client, type):
    tenant_id, headers, user_ids = await _make_shared_tenant(["USER"])
    item_id = await _insert_row(
        type, tenant_id=tenant_id, scope="user", created_by=user_ids["USER"]
    )
    resp = await async_client.put(
        f"/api/v1/catalogs/{type}/{item_id}",
        json={"name": f"Edited {uuid.uuid4().hex[:4]}"},
        headers=headers["USER"],
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_user_cannot_update_other_user_scope_row(async_client, type):
    tenant_id, headers, user_ids = await _make_shared_tenant(["USER"])
    # A user-scope row created by a DIFFERENT user in the same tenant.
    other_id = uuid.uuid4()
    item_id = await _insert_row(
        type,
        tenant_id=tenant_id,
        scope="user",
        created_by=other_id,
    )
    resp = await async_client.put(
        f"/api/v1/catalogs/{type}/{item_id}",
        json={"name": "hijack"},
        headers=headers["USER"],
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_user_cannot_update_system_row(async_client, type):
    _, headers, _ = await _make_shared_tenant(["USER"])
    item_id = await _insert_row(type, tenant_id=None, scope="system")
    resp = await async_client.put(
        f"/api/v1/catalogs/{type}/{item_id}",
        json={"name": "hijack"},
        headers=headers["USER"],
    )
    # System rows have tenant_id NULL -> visible to all tenants on read, but
    # the scope gate must still deny the write.
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_admin_can_update_any_user_scope_row(async_client, type):
    tenant_id, headers, _ = await _make_shared_tenant(["ADMIN"])
    other_id = uuid.uuid4()
    item_id = await _insert_row(
        type, tenant_id=tenant_id, scope="user", created_by=other_id
    )
    resp = await async_client.put(
        f"/api/v1/catalogs/{type}/{item_id}",
        json={"name": "admin-edit"},
        headers=headers["ADMIN"],
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_admin_cannot_update_system_row(async_client, type):
    _, headers, _ = await _make_shared_tenant(["ADMIN"])
    item_id = await _insert_row(type, tenant_id=None, scope="system")
    resp = await async_client.put(
        f"/api/v1/catalogs/{type}/{item_id}",
        json={"name": "hijack"},
        headers=headers["ADMIN"],
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Promote / demote
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_user_cannot_promote(async_client, type):
    tenant_id, headers, user_ids = await _make_shared_tenant(["USER"])
    item_id = await _insert_row(
        type, tenant_id=tenant_id, scope="user", created_by=user_ids["USER"]
    )
    resp = await async_client.post(
        f"/api/v1/catalogs/{type}/{item_id}/promote",
        json={"scope": "tenant"},
        headers=headers["USER"],
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_admin_promotes_user_to_tenant(async_client, type):
    tenant_id, headers, _ = await _make_shared_tenant(["ADMIN"])
    other_id = uuid.uuid4()
    item_id = await _insert_row(
        type, tenant_id=tenant_id, scope="user", created_by=other_id
    )
    resp = await async_client.post(
        f"/api/v1/catalogs/{type}/{item_id}/promote",
        json={"scope": "tenant"},
        headers=headers["ADMIN"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "tenant"


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_admin_cannot_promote_tenant_to_system(async_client, type):
    tenant_id, headers, _ = await _make_shared_tenant(["ADMIN"])
    item_id = await _insert_row(type, tenant_id=tenant_id, scope="tenant")
    resp = await async_client.post(
        f"/api/v1/catalogs/{type}/{item_id}/promote",
        json={"scope": "system"},
        headers=headers["ADMIN"],
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_system_admin_promotes_tenant_to_system(async_client, type):
    tenant_id, headers, _ = await _make_shared_tenant(["SYSTEM_ADMIN"])
    item_id = await _insert_row(type, tenant_id=tenant_id, scope="tenant")
    resp = await async_client.post(
        f"/api/v1/catalogs/{type}/{item_id}/promote",
        json={"scope": "system"},
        headers=headers["SYSTEM_ADMIN"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "system"
    assert body.get("tenant_id") is None


@pytest.mark.parametrize("type", WRITE_TYPES)
@pytest.mark.asyncio
async def test_system_admin_demotes_system_to_tenant(async_client, type):
    tenant_id, headers, _ = await _make_shared_tenant(["SYSTEM_ADMIN"])
    item_id = await _insert_row(type, tenant_id=None, scope="system")
    resp = await async_client.post(
        f"/api/v1/catalogs/{type}/{item_id}/promote",
        json={"scope": "tenant"},
        headers=headers["SYSTEM_ADMIN"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "tenant"
    assert body.get("tenant_id") == str(tenant_id)


# ---------------------------------------------------------------------------
# scope query filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_filter_narrows_list():
    """The ``scope`` filter narrows the list to one tier."""
    from app.catalogs.registry import CatalogRegistry
    from app.models.enums import CatalogScope

    tenant_id = uuid.uuid4()
    adapter = CatalogRegistry.get("medication").service

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="F", slug=f"f-{tenant_id}"))
        await db.commit()
        for scope, tid in [
            (CatalogScope.SYSTEM, None),
            (CatalogScope.TENANT, tenant_id),
            (CatalogScope.USER, tenant_id),
        ]:
            row = MedicationCatalog(name=f"filter-{scope.value}-{tenant_id.hex[:6]}")
            row.tenant_id = tid
            row.scope = scope
            db.add(row)
        await db.commit()

    async with AsyncSessionLocal() as db:
        all_rows = await adapter.list(db, tenant_id, limit=1000)
        scopes = {i["scope"] for i in all_rows["items"]}
        assert {"system", "tenant", "user"} <= scopes, scopes

        only_system = await adapter.list(db, tenant_id, scope="system", limit=1000)
        assert {i["scope"] for i in only_system["items"]} == {"system"}

        only_tenant = await adapter.list(db, tenant_id, scope="tenant", limit=1000)
        assert {i["scope"] for i in only_tenant["items"]} == {"tenant"}


# ---------------------------------------------------------------------------
# Migration backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_column_exists_on_all_catalog_tables():
    """The ``scope`` column must exist on every registered catalog table."""
    import asyncpg

    from app.catalogs.registry import CatalogRegistry
    from app.core.config import get_settings

    needed = {d.model.__tablename__ for d in CatalogRegistry.all()}
    url = get_settings().DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        for table in needed:
            row = await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = $1 AND column_name = 'scope'",
                table,
            )
            assert row == 1, f"table {table} missing scope column"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_backfill_derives_scope_from_tenant_id():
    """The migration backfill rule: NULL tenant -> system, non-NULL -> tenant.

    Simulates pre-migration rows (scope unclassified) and re-applies the
    derivation UPDATE, asserting it classifies by ``tenant_id``.
    """
    from app.models.enums import CatalogScope

    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="BF", slug=f"bf-{tenant_id}"))
        await db.commit()

    # Insert two rows in the "pre-backfill" state (scope forced to system).
    async with AsyncSessionLocal() as db:
        global_row = MedicationCatalog(name="bf-global")
        global_row.tenant_id = None
        global_row.scope = CatalogScope.SYSTEM
        tenant_row = MedicationCatalog(name="bf-tenant")
        tenant_row.tenant_id = tenant_id
        tenant_row.scope = CatalogScope.SYSTEM
        db.add_all([global_row, tenant_row])
        await db.commit()

    # Re-apply the migration's derivation rule to the test rows.
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE medication_catalog SET scope = 'tenant' "
                "WHERE tenant_id IS NOT NULL AND name LIKE 'bf-%'"
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(MedicationCatalog.name, MedicationCatalog.scope).where(
                MedicationCatalog.name.like("bf-%")
            )
        )
        by_name = {
            n: (s.value if isinstance(s, CatalogScope) else s) for n, s in res.all()
        }
    assert by_name["bf-global"] == "system", by_name
    assert by_name["bf-tenant"] == "tenant", by_name
