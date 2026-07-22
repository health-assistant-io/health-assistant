"""Slug-collision guard on catalog scope promotion (the 409 path).

When a promote/demote would create a duplicate slug at the target scope tier,
``BaseCatalogAdapter._check_slug_collision`` raises ``CatalogConflict`` which
``main.py`` maps to HTTP 409. This is the one shared guard so no catalog with
a slug column (biomarker / anatomy / vaccine) ends up with two rows owning
the same slug at one scope.

Uses ``anatomy`` (``AnatomyStructure`` has a slug) through the unified
``/catalogs/anatomy/{id}/promote`` endpoint.
"""
import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.anatomy_model import AnatomyStructure
from app.models.tenant_model import TenantModel


async def _make_tenant():
    """One tenant + a SYSTEM_ADMIN header (the only role that promotes to system)."""
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Collide", slug=f"col-{tenant_id.hex[:8]}"))
        await db.commit()
    token = create_access_token(
        {
            "sub": "sysadmin@collide.test",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": "SYSTEM_ADMIN",
        }
    )
    return tenant_id, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_promote_tenant_to_system_blocked_on_slug_collision(async_client):
    """Promoting a tenant row to system is blocked (409) when a system row
    already owns the same slug — the response carries the conflicting item's
    id + name so the client can offer "open existing"."""
    tenant_id, headers = await _make_tenant()
    slug = f"collide-{uuid.uuid4().hex[:6]}"

    async with AsyncSessionLocal() as db:
        db.add(
            AnatomyStructure(
                slug=slug, name="System Node", scope="system", tenant_id=None
            )
        )
        tenant_node = AnatomyStructure(
            slug=slug, name="Tenant Node", scope="tenant", tenant_id=tenant_id
        )
        db.add(tenant_node)
        await db.commit()
        await db.refresh(tenant_node)
        tenant_row_id = str(tenant_node.id)

    resp = await async_client.post(
        f"/api/v1/catalogs/anatomy/{tenant_row_id}/promote",
        json={"scope": "system"},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "catalog_conflict"
    assert body["slug"] == slug
    assert body["target_scope"] == "system"
    assert body["existing_name"] == "System Node"


@pytest.mark.asyncio
async def test_promote_succeeds_when_no_slug_collision(async_client):
    """Promoting to system succeeds when the slug is unique at the target
    scope — the guard is a guard, not a blanket block."""
    tenant_id, headers = await _make_tenant()
    slug = f"unique-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as db:
        node = AnatomyStructure(
            slug=slug, name="Unique Tenant Node", scope="tenant", tenant_id=tenant_id
        )
        db.add(node)
        await db.commit()
        await db.refresh(node)
        item_id = str(node.id)

    resp = await async_client.post(
        f"/api/v1/catalogs/anatomy/{item_id}/promote",
        json={"scope": "system"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "system"
    assert body["tenant_id"] is None


@pytest.mark.asyncio
async def test_same_scope_promote_skips_collision_check(async_client):
    """A same-scope promote (system→system) is a no-op for the collision guard
    — it must not 409 against itself."""
    _, headers = await _make_tenant()
    slug = f"same-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as db:
        node = AnatomyStructure(
            slug=slug, name="Same Node", scope="system", tenant_id=None
        )
        db.add(node)
        await db.commit()
        await db.refresh(node)
        item_id = str(node.id)

    resp = await async_client.post(
        f"/api/v1/catalogs/anatomy/{item_id}/promote",
        json={"scope": "system"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
