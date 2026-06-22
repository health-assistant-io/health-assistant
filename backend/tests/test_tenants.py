"""Tests for the legacy tenant self-info endpoints (``/api/v1/tenants``).

The administrative surface (list/create/delete/etc.) moved to
``/api/v1/admin/tenants`` and is covered by ``test_admin_tenants.py``.

These tests pin the remaining public contract:
  * ``GET /tenants`` returns the caller's own tenant.
  * ``GET /tenants/{id}`` allows same-tenant reads, refuses cross-tenant
    reads with 403 (no information leak), and lets SYSTEM_ADMIN read any.
  * ``PATCH /tenants/{id}`` allows tenant-admin self-service updates
    only on their own tenant.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.security import get_current_user
from app.main import app
from app.schemas.user import TokenData


def _token(role: str, tenant_id=None) -> TokenData:
    return TokenData(
        sub=f"{role.lower()}@example.com",
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=role,
    )


@pytest.mark.asyncio
async def test_get_my_tenant_returns_own_tenant(async_client):
    """A caller always sees their own tenant via GET /tenants."""
    tid = uuid.uuid4()
    app.dependency_overrides[get_current_user] = lambda: _token("USER", tid)
    fake = type("T", (), {"id": tid, "name": "Mine", "slug": "mine",
                          "description": None, "is_active": True,
                          "owner_id": None, "settings": {},
                          "created_at": None, "updated_at": None})()
    with patch("app.api.v1.endpoints.tenants.get_tenant", new=AsyncMock(return_value=fake)):
        resp = await async_client.get("/api/v1/tenants")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(tid)
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_tenant_by_id_forbidden_cross_tenant(async_client):
    """A USER cannot read another tenant by guessing the id."""
    own = uuid.uuid4()
    other = uuid.uuid4()
    app.dependency_overrides[get_current_user] = lambda: _token("USER", own)
    resp = await async_client.get(f"/api/v1/tenants/{other}")
    assert resp.status_code == 403
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_tenant_by_id_system_admin_can_read_any(async_client):
    """SYSTEM_ADMIN bypasses the same-tenant gate (read-only here)."""
    other = uuid.uuid4()
    app.dependency_overrides[get_current_user] = lambda: _token("SYSTEM_ADMIN", uuid.uuid4())
    fake = type("T", (), {"id": other, "name": "Other", "slug": "other",
                          "description": None, "is_active": True,
                          "owner_id": None, "settings": {},
                          "created_at": None, "updated_at": None})()
    with patch("app.api.v1.endpoints.tenants.get_tenant", new=AsyncMock(return_value=fake)):
        resp = await async_client.get(f"/api/v1/tenants/{other}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(other)
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_tenant_malformed_uuid_returns_400(async_client):
    app.dependency_overrides[get_current_user] = lambda: _token("USER")
    resp = await async_client.get("/api/v1/tenants/not-a-uuid")
    assert resp.status_code == 400
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_patch_my_tenant_requires_admin_role(async_client):
    """A USER cannot PATCH a tenant even their own."""
    tid = uuid.uuid4()
    app.dependency_overrides[get_current_user] = lambda: _token("USER", tid)
    resp = await async_client.patch(f"/api/v1/tenants/{tid}", json={"name": "New"})
    assert resp.status_code == 403
    app.dependency_overrides = {}
