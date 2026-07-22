"""Anatomy domain endpoint scope parity — the `/anatomy` POST/PATCH/DELETE
paths now delegate to the shared ``DEFAULT_CATALOG_POLICY`` (same path
biomarker/medication/allergy/vaccine use) instead of the legacy binary
``tenant_id``-only check.

Covers:
  * POST as USER → ``scope=user``, ``tenant_id`` set, ``created_by`` set
    (was a latent bug: domain POST stamped ``scope=SYSTEM`` on tenant rows).
  * POST as ADMIN → ``scope=tenant``.
  * POST as SYSTEM_ADMIN → ``scope=system``, ``tenant_id=NULL``.
  * PATCH/DELETE respects ``check_modify`` (USER edits own user-scope row;
    cannot edit system; ADMIN edits tenant).
"""
import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.anatomy_model import AnatomyStructure
from app.models.tenant_model import TenantModel

ROLES = ["USER", "ADMIN", "SYSTEM_ADMIN"]


async def _make_tenant(roles=ROLES):
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Anat Dom", slug=f"anatdom-{tenant_id.hex[:8]}"))
        await db.commit()
    headers = {}
    user_ids = {}
    for role in roles:
        uid = uuid.uuid4()
        user_ids[role] = uid
        token = create_access_token(
            {
                "sub": f"{role.lower()}@anatdom.test",
                "user_id": str(uid),
                "tenant_id": str(tenant_id),
                "role": role,
            }
        )
        headers[role] = {"Authorization": f"Bearer {token}"}
    return tenant_id, headers, user_ids


def _create_payload(suffix: str):
    return {
        "slug": f"anat-{suffix}",
        "name": f"Anatomy {suffix}",
        "class_concept_slug": None,
    }


# ---------------------------------------------------------------------------
# Create → role-derived scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_create_lands_in_user_scope(async_client):
    tenant_id, headers, _ = await _make_tenant(["USER"])
    resp = await async_client.post(
        "/api/v1/anatomy",
        json=_create_payload(uuid.uuid4().hex[:6]),
        headers=headers["USER"],
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["scope"] == "user"
    assert body["tenant_id"] == str(tenant_id)


@pytest.mark.asyncio
async def test_admin_create_lands_in_tenant_scope(async_client):
    tenant_id, headers, _ = await _make_tenant(["ADMIN"])
    resp = await async_client.post(
        "/api/v1/anatomy",
        json=_create_payload(uuid.uuid4().hex[:6]),
        headers=headers["ADMIN"],
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["scope"] == "tenant"
    assert body["tenant_id"] == str(tenant_id)


@pytest.mark.asyncio
async def test_system_admin_create_lands_in_system_scope(async_client):
    _, headers, _ = await _make_tenant(["SYSTEM_ADMIN"])
    resp = await async_client.post(
        "/api/v1/anatomy",
        json=_create_payload(uuid.uuid4().hex[:6]),
        headers=headers["SYSTEM_ADMIN"],
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["scope"] == "system"
    assert body["tenant_id"] is None


# ---------------------------------------------------------------------------
# Update / delete → check_modify RBAC
# ---------------------------------------------------------------------------


async def _insert_anatomy(*, slug, tenant_id, scope, created_by=None):
    node = AnatomyStructure(
        slug=slug, name=f"Node {slug}", scope=scope, tenant_id=tenant_id
    )
    if created_by is not None:
        node.created_by = created_by
    async with AsyncSessionLocal() as db:
        db.add(node)
        await db.commit()
        await db.refresh(node)
        return str(node.id)


@pytest.mark.asyncio
async def test_user_can_update_own_user_scope_row(async_client):
    tenant_id, headers, user_ids = await _make_tenant(["USER"])
    uid = user_ids["USER"]
    item_id = await _insert_anatomy(
        slug=f"own-{uuid.uuid4().hex[:6]}",
        tenant_id=tenant_id,
        scope="user",
        created_by=uid,
    )
    resp = await async_client.patch(
        f"/api/v1/anatomy/{item_id}",
        json={"name": "Renamed by owner"},
        headers=headers["USER"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed by owner"


@pytest.mark.asyncio
async def test_user_cannot_update_system_scope_row(async_client):
    tenant_id, headers, _ = await _make_tenant(["USER"])
    item_id = await _insert_anatomy(
        slug=f"sys-{uuid.uuid4().hex[:6]}", tenant_id=None, scope="system"
    )
    resp = await async_client.patch(
        f"/api/v1/anatomy/{item_id}",
        json={"name": "Attempt"},
        headers=headers["USER"],
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_user_cannot_delete_system_scope_row(async_client):
    _, headers, _ = await _make_tenant(["USER"])
    item_id = await _insert_anatomy(
        slug=f"sysdel-{uuid.uuid4().hex[:6]}", tenant_id=None, scope="system"
    )
    resp = await async_client.delete(
        f"/api/v1/anatomy/{item_id}", headers=headers["USER"]
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_admin_can_delete_tenant_scope_row(async_client):
    tenant_id, headers, _ = await _make_tenant(["ADMIN"])
    item_id = await _insert_anatomy(
        slug=f"ten-{uuid.uuid4().hex[:6]}", tenant_id=tenant_id, scope="tenant"
    )
    resp = await async_client.delete(
        f"/api/v1/anatomy/{item_id}", headers=headers["ADMIN"]
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_user_cannot_delete_other_users_user_scope_row(async_client):
    """A USER cannot delete a user-scope row created by a different user
    (the creator-only rule)."""
    tenant_id, headers, _ = await _make_tenant(["USER"])
    other_uid = uuid.uuid4()
    item_id = await _insert_anatomy(
        slug=f"other-{uuid.uuid4().hex[:6]}",
        tenant_id=tenant_id,
        scope="user",
        created_by=other_uid,
    )
    resp = await async_client.delete(
        f"/api/v1/anatomy/{item_id}", headers=headers["USER"]
    )
    assert resp.status_code == 403, resp.text
