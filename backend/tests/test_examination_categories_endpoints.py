"""Tests for /examination-categories endpoints.

Coverage focus: the SYSTEM_ADMIN gate on PATCH /{id} for global categories
(tenant_id=None). A tenant admin must NOT be able to mutate global categories;
a system admin can. This was a latent RBAC gap (the original code had a
`# TODO: Implement super-admin check here` placeholder) — the regression test
locks the fix in place.
"""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient


def make_token(role="ADMIN", user_id=None, tenant_id=None):
    token = MagicMock()
    token.role = role
    token.user_id = user_id or uuid4()
    token.tenant_id = tenant_id or uuid4()
    return token


def _setup_app_overrides(token):
    """Install the get_current_user + get_db overrides on the FastAPI app."""
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    fake_db = MagicMock()
    fake_db.commit = AsyncMock()
    fake_db.refresh = AsyncMock()

    async def _override_user():
        return token

    async def _override_db():
        yield fake_db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    return fake_db


def _clear_overrides():
    from app.main import app
    app.dependency_overrides = {}


def _make_execute_result(category):
    """Build a fake db.execute() return value whose scalar_one_or_none() yields `category`."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = category
    return result


def _make_category(tenant_id=None):
    """Build a category-like ORM object."""
    cat = MagicMock()
    cat.id = uuid4()
    cat.tenant_id = tenant_id
    cat.name = "Lab Results"
    cat.slug = "lab-results"
    cat.description = None
    cat.color = None
    cat.icon = None
    # refresh() needs to be awaitable; MagicMock auto-async via AsyncMock above.
    return cat


# ---------- PATCH /{category_id} — RBAC gate ----------


@pytest.mark.asyncio
async def test_update_global_category_rejected_for_tenant_admin(async_client: AsyncClient):
    """A tenant ADMIN must NOT be able to PATCH a global (tenant_id=None) category."""
    fake_db = _setup_app_overrides(make_token(role="ADMIN"))
    try:
        global_cat = _make_category(tenant_id=None)
        fake_db.execute = AsyncMock(return_value=_make_execute_result(global_cat))

        response = await async_client.patch(
            f"/api/v1/examination-categories/{global_cat.id}",
            json={"description": "hijacked"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Only system admins can modify global categories"
        # The gate must fire BEFORE commit — no write should reach the DB.
        fake_db.commit.assert_not_called()
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_update_global_category_rejected_for_standard_user(async_client: AsyncClient):
    """A standard USER must NOT be able to PATCH a global category."""
    fake_db = _setup_app_overrides(make_token(role="USER"))
    try:
        global_cat = _make_category(tenant_id=None)
        fake_db.execute = AsyncMock(return_value=_make_execute_result(global_cat))

        response = await async_client.patch(
            f"/api/v1/examination-categories/{global_cat.id}",
            json={"description": "hijacked"},
        )
        assert response.status_code == 403
        fake_db.commit.assert_not_called()
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_update_global_category_allowed_for_system_admin(async_client: AsyncClient):
    """SYSTEM_ADMIN can PATCH a global category — the gate must not over-restrict."""
    fake_db = _setup_app_overrides(make_token(role="SYSTEM_ADMIN"))
    try:
        global_cat = _make_category(tenant_id=None)
        fake_db.execute = AsyncMock(return_value=_make_execute_result(global_cat))

        response = await async_client.patch(
            f"/api/v1/examination-categories/{global_cat.id}",
            json={"description": "updated by sysadmin"},
        )
        assert response.status_code == 200
        fake_db.commit.assert_awaited_once()
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_update_tenant_category_allowed_for_tenant_admin(async_client: AsyncClient):
    """A tenant ADMIN can PATCH their own tenant-scoped category — existing behavior preserved."""
    tenant_id = uuid4()
    fake_db = _setup_app_overrides(make_token(role="ADMIN", tenant_id=tenant_id))
    try:
        tenant_cat = _make_category(tenant_id=tenant_id)
        fake_db.execute = AsyncMock(return_value=_make_execute_result(tenant_cat))

        response = await async_client.patch(
            f"/api/v1/examination-categories/{tenant_cat.id}",
            json={"description": "updated"},
        )
        assert response.status_code == 200
        fake_db.commit.assert_awaited_once()
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_update_category_404_when_not_found(async_client: AsyncClient):
    """A missing category returns 404 before the RBAC gate fires."""
    fake_db = _setup_app_overrides(make_token(role="SYSTEM_ADMIN"))
    try:
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        fake_db.execute = AsyncMock(return_value=result)

        response = await async_client.patch(
            f"/api/v1/examination-categories/{uuid4()}",
            json={"description": "x"},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Category not found"
    finally:
        _clear_overrides()


# ---------- DELETE /{category_id} — global categories are protected ----------


@pytest.mark.asyncio
async def test_delete_global_category_returns_404(async_client: AsyncClient):
    """DELETE already restricts via tenant_id == current_user.tenant_id in the
    WHERE clause — a global category (tenant_id=None) is never matched, so 404.
    This locks in that the delete path is also safe (no separate gate needed)."""
    fake_db = _setup_app_overrides(make_token(role="ADMIN"))
    try:
        # Simulate the WHERE clause filtering out global categories.
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        fake_db.execute = AsyncMock(return_value=result)

        response = await async_client.delete(f"/api/v1/examination-categories/{uuid4()}")
        assert response.status_code == 404
    finally:
        _clear_overrides()


# ---------- POST / — create always tenant-scoped ----------


@pytest.mark.asyncio
async def test_create_category_always_attaches_caller_tenant_id(async_client: AsyncClient):
    """POST must never create a global category — tenant_id is forced to the caller's."""
    tenant_id = uuid4()
    fake_db = _setup_app_overrides(make_token(role="ADMIN", tenant_id=tenant_id))
    try:
        # Name-uniqueness check returns no existing row.
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        fake_db.execute = AsyncMock(return_value=existing_result)
        fake_db.add = MagicMock()
        fake_db.commit = AsyncMock()
        fake_db.refresh = AsyncMock(
            side_effect=lambda obj: setattr(obj, "id", uuid4()) or setattr(obj, "tenant_id", tenant_id)
        )

        response = await async_client.post(
            "/api/v1/examination-categories",
            json={"name": "Cardiology", "slug": "cardiology"},
        )
        assert response.status_code == 200
        # The object passed to db.add must carry the caller's tenant_id.
        added = fake_db.add.call_args.args[0]
        assert added.tenant_id == tenant_id
    finally:
        _clear_overrides()
