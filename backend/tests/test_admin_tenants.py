"""Tests for the system-admin tenant management surface.

These pin the contract for ``/api/v1/admin/tenants`` and the
``TenantAdminService``:

  * RBAC: only SYSTEM_ADMIN may access any of the admin routes.
  * CRUD: list (paginated + filtered), create (slug auto-gen + uniqueness),
    detail (stats), update (partial), soft-delete/reactivate, hard-delete
    (typed-name confirmation).
  * Switching: mints a scoped JWT with the right claims; exit-switch
    restores the original session; both states are audit-logged.
  * User management: list, role change (SYSTEM_ADMIN cannot be granted),
    invite minting.
  * Audit: every mutation writes an AuditLog entry, and the audit viewer
    returns entries scoped to the tenant.

The tests follow the mock-heavy style used by the rest of the suite
(``app.dependency_overrides`` for auth, ``AsyncMock`` for the session).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.security import create_access_token, get_current_user
from app.main import app
from app.models.enums import Role
from app.schemas.tenant import (
    TenantCreate,
    UpdateTenantUser,
)
from app.services.tenant_admin_service import TenantAdminService


# ---------------------------------------------------------------------------
# Token-data fixtures
# ---------------------------------------------------------------------------


def _admin_token_data(
    *,
    switched: bool = False,
    original_tenant_id=None,
    original_user_id=None,
):
    """A SYSTEM_ADMIN TokenData (optionally switched)."""
    return MagicMock(
        spec=[
            "user_id",
            "tenant_id",
            "role",
            "sub",
            "switched",
            "original_tenant_id",
            "original_user_id",
        ],
        user_id=original_user_id or uuid.uuid4(),
        tenant_id=original_tenant_id or uuid.uuid4(),
        role=Role.SYSTEM_ADMIN.value,
        sub="admin@example.com",
        switched=switched,
        original_tenant_id=original_tenant_id,
        original_user_id=original_user_id,
    )


def _user_token_data(role=Role.USER.value):
    return MagicMock(
        spec=["user_id", "tenant_id", "role", "sub", "switched"],
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role=role,
        sub="user@example.com",
        switched=False,
    )


def _tenant_row(
    *,
    id=None,
    name="Acme Health",
    slug="acme-health",
    is_active=True,
    owner_id=None,
    settings=None,
):
    """A fake TenantModel row with the fields the response schema reads."""
    fake = MagicMock()
    fake.id = id or uuid.uuid4()
    fake.name = name
    fake.slug = slug
    fake.description = "Test tenant"
    fake.is_active = is_active
    fake.owner_id = owner_id
    fake.settings = settings or {}
    fake.created_at = datetime.now(timezone.utc)
    fake.updated_at = datetime.now(timezone.utc)
    return fake


def _user_row(*, id=None, tenant_id=None, role=Role.USER, is_active=True):
    fake = MagicMock()
    fake.id = id or uuid.uuid4()
    fake.tenant_id = tenant_id or uuid.uuid4()
    fake.email = "member@example.com"
    fake.role = role if isinstance(role, Role) else Role(role)
    fake.is_active = is_active
    fake.settings = {}
    fake.created_at = datetime.now(timezone.utc)
    fake.updated_at = datetime.now(timezone.utc)
    return fake


def _mock_db_with_results(*results):
    """Build an AsyncMock session whose ``execute`` returns each result in turn."""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(results))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.delete = AsyncMock()
    db.add = MagicMock()
    return db


def _scalar_result(value):
    """A result that returns ``value`` from ``.scalar()``."""
    r = MagicMock()
    r.scalar.return_value = value
    return r


def _scalars_result(items):
    """A result that returns ``items`` from ``.scalars().all()``."""
    r = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = items
    r.scalars.return_value = scalars
    r.scalar_one_or_none.return_value = items[0] if items else None
    return r


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_routes_forbidden_for_non_admin(async_client):
    """Every /admin/tenants route must reject USER / MANAGER / ADMIN tokens."""
    for role in (Role.USER.value, Role.MANAGER.value, Role.ADMIN.value):
        app.dependency_overrides[get_current_user] = lambda r=role: _user_token_data(r)
        cases = [
            ("get", "/api/v1/admin/tenants", None),
            ("post", "/api/v1/admin/tenants", {}),
            ("get", f"/api/v1/admin/tenants/{uuid.uuid4()}", None),
            ("patch", f"/api/v1/admin/tenants/{uuid.uuid4()}", {}),
            ("delete_request", f"/api/v1/admin/tenants/{uuid.uuid4()}", {"permanent": True, "confirm_name": "x"}),
            ("post", f"/api/v1/admin/tenants/{uuid.uuid4()}/switch", None),
        ]
        for method, url, body in cases:
            if method == "delete_request":
                resp = await async_client.request("DELETE", url, json=body)
            elif body is None:
                resp = await getattr(async_client, method)(url)
            else:
                resp = await getattr(async_client, method)(url, json=body)
            assert resp.status_code == 403, f"{role} on {method.upper()} {url} -> {resp.status_code}"
    app.dependency_overrides = {}


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tenants_paginates_and_filters():
    db = _mock_db_with_results(
        _scalar_result(2),  # total count
        _scalars_result([_tenant_row(), _tenant_row()]),  # page
    )
    items, total = await TenantAdminService(db).list_tenants(
        search="acme", is_active=True, limit=10, offset=0
    )
    assert total == 2
    assert len(items) == 2
    # Two statements: count + page.
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_get_tenant_detail_404_on_missing():
    db = _mock_db_with_results(_scalars_result([]))
    with pytest.raises(HTTPException) as exc:
        await TenantAdminService(db).get_tenant_detail(uuid.uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_tenant_auto_generates_slug():
    actor = uuid.uuid4()
    # _ensure_unique_slug returns 0 (no collision) on first check, then
    # get_tenant_detail isn't called because we go straight to flush/commit.
    db = _mock_db_with_results(
        _scalar_result(0),  # slug uniqueness check
    )
    captured = {}

    def _add(obj):
        captured["obj"] = obj

    db.add = _add

    with patch(
        "app.services.tenant_admin_service.log_audit_action", new=AsyncMock()
    ):
        tenant = await TenantAdminService(db).create_tenant(
            TenantCreate(name="Acme Health"), actor_id=actor
        )
    assert tenant.slug == "acme-health"
    assert tenant.is_active is True
    assert tenant.owner_id == actor


@pytest.mark.asyncio
async def test_create_tenant_appends_suffix_on_slug_collision():
    """A duplicate slug must not raise — the service retries with a suffix."""
    db = _mock_db_with_results(
        _scalar_result(1),  # first candidate collides
        _scalar_result(0),  # suffixed candidate is unique
    )
    captured = {}
    db.add = lambda obj: captured.__setitem__("obj", obj)

    with patch(
        "app.services.tenant_admin_service.log_audit_action", new=AsyncMock()
    ):
        tenant = await TenantAdminService(db).create_tenant(
            TenantCreate(name="Acme"), actor_id=uuid.uuid4()
        )
    # Slug got a suffix appended (4-char hex token minimum).
    assert tenant.slug.startswith("acme-")
    assert len(tenant.slug) > len("acme")


@pytest.mark.asyncio
async def test_hard_delete_rejects_wrong_confirm_name():
    tenant = _tenant_row(name="Acme")
    db = _mock_db_with_results(_scalars_result([tenant]))
    svc = TenantAdminService(db)
    with pytest.raises(HTTPException) as exc:
        await svc.hard_delete_tenant(tenant.id, confirm_name="wrong", actor_id=uuid.uuid4())
    assert exc.value.status_code == 400
    # Nothing should have been deleted.
    db.delete.assert_not_called()


@pytest.mark.asyncio
async def test_hard_delete_succeeds_with_matching_confirm_name():
    tenant = _tenant_row(name="Acme")
    db = _mock_db_with_results(_scalars_result([tenant]))
    with patch(
        "app.services.tenant_admin_service.log_audit_action", new=AsyncMock()
    ) as mock_audit:
        await TenantAdminService(db).hard_delete_tenant(
            tenant.id, confirm_name="Acme", actor_id=uuid.uuid4()
        )
    db.delete.assert_awaited_once_with(tenant)
    db.commit.assert_awaited_once()
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs.get("action") == "tenant.delete"


@pytest.mark.asyncio
async def test_switch_into_inactive_tenant_rejected():
    tenant = _tenant_row(is_active=False)
    db = _mock_db_with_results(_scalars_result([tenant]))
    svc = TenantAdminService(db)
    with pytest.raises(HTTPException) as exc:
        await svc.switch_into_tenant(tenant.id, actor=_admin_token_data())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_switch_into_tenant_mints_scoped_jwt():
    """The minted token must carry the right claims (decoded)."""
    admin = _admin_token_data()
    tenant = _tenant_row(is_active=True)
    db = _mock_db_with_results(_scalars_result([tenant]))
    with patch(
        "app.services.tenant_admin_service.log_audit_action", new=AsyncMock()
    ):
        result = await TenantAdminService(db).switch_into_tenant(tenant.id, actor=admin)
    # Decode the access token and verify claims.
    from app.core.security import decode_access_token

    payload = decode_access_token(result.access_token)
    assert payload is not None
    assert payload["tenant_id"] == str(tenant.id)
    assert payload["role"] == Role.SYSTEM_ADMIN.value
    assert payload["original_tenant_id"] == str(admin.tenant_id)
    assert payload["original_user_id"] == str(admin.user_id)
    assert payload["switched"] is True


@pytest.mark.asyncio
async def test_switch_back_requires_switched_session():
    """A non-switched session cannot exit a switch."""
    db = _mock_db_with_results()
    admin = _admin_token_data(switched=False)
    with pytest.raises(HTTPException) as exc:
        await TenantAdminService(db).switch_back(actor=admin)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_switch_back_restores_original_tenant():
    original_tid = uuid.uuid4()
    original_uid = uuid.uuid4()
    admin = _admin_token_data(
        switched=True,
        original_tenant_id=original_tid,
        original_user_id=original_uid,
    )
    tenant = _tenant_row(id=original_tid)
    db = _mock_db_with_results(_scalars_result([tenant]))
    with patch(
        "app.services.tenant_admin_service.log_audit_action", new=AsyncMock()
    ):
        result = await TenantAdminService(db).switch_back(actor=admin)
    from app.core.security import decode_access_token

    payload = decode_access_token(result.access_token)
    assert payload["tenant_id"] == str(original_tid)
    assert payload["user_id"] == str(original_uid)
    assert "original_tenant_id" not in payload
    assert payload.get("switched") in (False, None)


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_tenant_user_refuses_system_admin_grant():
    """SYSTEM_ADMIN cannot be granted via the tenant user surface.

    Defense in depth: (1) the Pydantic schema's Literal type rejects
    ``SYSTEM_ADMIN`` at the API boundary with a 422; (2) the service
    layer also refuses if the enum somehow accepts it (e.g. via direct
    internal callers).
    """
    import pydantic

    # 1. Schema layer.
    with pytest.raises(pydantic.ValidationError):
        UpdateTenantUser(role="SYSTEM_ADMIN")

    # 2. Service layer (call directly with a constructed payload that
    #    bypasses the schema — simulates an internal caller).
    tenant_id = uuid.uuid4()
    user = _user_row(tenant_id=tenant_id, role=Role.USER)
    db = _mock_db_with_results(_scalars_result([user]))
    svc = TenantAdminService(db)
    rogue_payload = UpdateTenantUser.model_construct(role="SYSTEM_ADMIN")
    with pytest.raises(HTTPException) as exc:
        await svc.update_tenant_user(
            tenant_id,
            user.id,
            rogue_payload,
            actor_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 400
    assert "SYSTEM_ADMIN" in exc.value.detail


@pytest.mark.asyncio
async def test_update_tenant_user_cross_tenant_returns_404():
    db = _mock_db_with_results(_scalars_result([]))  # user not in this tenant
    svc = TenantAdminService(db)
    with pytest.raises(HTTPException) as exc:
        await svc.update_tenant_user(
            uuid.uuid4(),
            uuid.uuid4(),
            UpdateTenantUser(role="MANAGER"),
            actor_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_tenant_user_writes_audit_log():
    tenant_id = uuid.uuid4()
    user = _user_row(tenant_id=tenant_id, role=Role.USER)
    db = _mock_db_with_results(_scalars_result([user]))
    with patch(
        "app.services.tenant_admin_service.log_audit_action", new=AsyncMock()
    ) as mock_audit:
        await TenantAdminService(db).update_tenant_user(
            tenant_id,
            user.id,
            UpdateTenantUser(role="MANAGER"),
            actor_id=uuid.uuid4(),
        )
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs.get("action") == "tenant_user.update"
    assert mock_audit.call_args.kwargs.get("old_value", {}).get("role") == Role.USER.value
    assert mock_audit.call_args.kwargs.get("new_value", {}).get("role") == Role.MANAGER.value


# ---------------------------------------------------------------------------
# Endpoint-level tests (with mocked service)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_list_tenants_returns_paginated(async_client):
    app.dependency_overrides[get_current_user] = lambda: _admin_token_data()

    tenant = _tenant_row()
    with patch.object(
        TenantAdminService, "list_tenants", new=AsyncMock(return_value=([tenant], 1))
    ):
        resp = await async_client.get("/api/v1/admin/tenants?limit=10&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == tenant.slug
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_endpoint_create_tenant_returns_201(async_client):
    app.dependency_overrides[get_current_user] = lambda: _admin_token_data()
    tenant = _tenant_row()
    with patch.object(
        TenantAdminService, "create_tenant", new=AsyncMock(return_value=tenant)
    ):
        resp = await async_client.post(
            "/api/v1/admin/tenants",
            json={"name": "Acme Health"},
        )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Acme Health"
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_endpoint_malformed_uuid_returns_400(async_client):
    app.dependency_overrides[get_current_user] = lambda: _admin_token_data()
    resp = await async_client.get("/api/v1/admin/tenants/not-a-uuid")
    assert resp.status_code == 400
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_endpoint_hard_delete_requires_confirmation_body(async_client):
    app.dependency_overrides[get_current_user] = lambda: _admin_token_data()
    tenant_id = uuid.uuid4()
    # Wrong confirm_name → 400 from service.
    async def _boom(*args, **kwargs):
        raise HTTPException(status_code=400, detail="nope")

    with patch.object(TenantAdminService, "hard_delete_tenant", new=_boom):
        resp = await async_client.request(
            "DELETE",
            f"/api/v1/admin/tenants/{tenant_id}",
            json={"permanent": True, "confirm_name": "wrong"},
        )
    assert resp.status_code == 400
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_endpoint_switch_blocks_if_already_switched(async_client):
    """An already-switched token must not be allowed to switch again."""
    switched = _admin_token_data(
        switched=True,
        original_tenant_id=uuid.uuid4(),
        original_user_id=uuid.uuid4(),
    )
    app.dependency_overrides[get_current_user] = lambda: switched
    resp = await async_client.post(f"/api/v1/admin/tenants/{uuid.uuid4()}/switch")
    assert resp.status_code == 400
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_endpoint_invite_mints_token(async_client):
    app.dependency_overrides[get_current_user] = lambda: _admin_token_data()
    tenant_id = uuid.uuid4()
    payload = {
        "invite_token": "tok",
        "tenant_id": str(tenant_id),
        "role": "USER",
        "expires_in_days": 7,
    }
    with patch.object(
        TenantAdminService, "mint_invite", new=AsyncMock(return_value=payload)
    ):
        resp = await async_client.post(
            f"/api/v1/admin/tenants/{tenant_id}/invite",
            json={"role": "USER", "expires_days": 7},
        )
    assert resp.status_code == 200
    assert resp.json()["invite_token"] == "tok"
    app.dependency_overrides = {}


# ---------------------------------------------------------------------------
# Integration: switch token claims flow through TokenData
# ---------------------------------------------------------------------------


def test_switched_token_decodes_into_tokendata_with_claims():
    """A minted switch token must populate TokenData's switch fields."""
    original_tenant = uuid.uuid4()
    target_tenant = uuid.uuid4()
    original_user = uuid.uuid4()
    token = create_access_token(
        {
            "sub": "admin@example.com",
            "user_id": str(original_user),
            "tenant_id": str(target_tenant),
            "role": Role.SYSTEM_ADMIN.value,
            "original_tenant_id": str(original_tenant),
            "original_user_id": str(original_user),
            "switched": True,
        }
    )
    from app.core.security import verify_access_token

    payload = verify_access_token(token)
    assert payload is not None
    from app.schemas.user import TokenData

    td = TokenData(**payload)
    assert td.switched is True
    assert td.tenant_id == target_tenant
    assert td.original_tenant_id == original_tenant
    assert td.original_user_id == original_user
