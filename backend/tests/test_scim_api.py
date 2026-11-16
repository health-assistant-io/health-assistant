"""SCIM 2.0 — ``/scim/v2`` with tenant-bounded bearer tokens (Phase 12B).

Covers: token auth (401 for missing/unknown/revoked/expired and for a
token whose tenant is gone), cross-tenant hidden-404, Users/Groups CRUD,
RFC 7644 pagination + the ``userName eq`` filter, deprovision (SCIM
delete / ``active=false``) deactivating the account and bumping
``token_version`` so **live sessions die**, group membership moves
re-rolling members through the SSO ``group_role_map`` (audited), and the
never-logged plaintext law.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text

import app.core.rate_limit as rate_limit_module
import app.core.token_store as token_store
from app.core import tenant_context
from app.core.database import AsyncSessionLocal
from app.models.scim_token import ScimToken
from app.models.tenant_sso_config import TenantSSOConfig
from app.models.user_model import UserModel
from app.services import scim
from tests._auth_helpers import auth_headers, create_tenant, create_user


class FakeRedis:
    """The per-test Redis fake (see test_sso_oidc) — the exact command
    surface ``token_store`` / ``rate_limit`` use."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = str(value)
        return True

    async def get(self, key):
        return self.store.get(key)

    async def getdel(self, key):
        return self.store.pop(key, None)

    async def delete(self, *keys):
        count = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                count += 1
            if self.hashes.pop(key, None) is not None:
                count += 1
        return count

    async def exists(self, *keys):
        return sum(1 for key in keys if key in self.store or key in self.hashes)

    async def incr(self, key):
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key, ttl):
        return True

    async def hsetnx(self, key, field, value):
        hashes = self.hashes.setdefault(key, {})
        if field in hashes:
            return False
        hashes[field] = str(value)
        return True

    async def hset(self, key, mapping=None):
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in (mapping or {}).items()})
        return 1

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def scan_iter(self, pattern):
        import fnmatch

        for key in list(self.store.keys()) + list(self.hashes.keys()):
            if fnmatch.fnmatch(key, pattern):
                yield key


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(token_store, "redis_client", fake)
    monkeypatch.setattr(rate_limit_module, "redis_client", fake)
    return fake


@pytest_asyncio.fixture(autouse=True)
async def _clean_identity_tables():
    """The identity tables must not leak between tests (rows persist)."""
    async with AsyncSessionLocal() as session:
        for table in ("tenant_sso_configs", "user_identities", "scim_tokens", "scim_groups"):
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        for table in ("tenant_sso_configs", "user_identities", "scim_tokens", "scim_groups"):
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()


@pytest_asyncio.fixture
async def scim_tenant():
    return await create_tenant(name="SCIM Tenant")


@pytest_asyncio.fixture
async def scim_token(scim_tenant):
    """A credential created through the service (plaintext never stored)."""
    with tenant_context.system_scope("tests:scim-token"):
        async with AsyncSessionLocal() as session:
            row, plaintext = await scim.create_token(
                session,
                tenant_id=scim_tenant,
                name="directory",
                created_by=None,
            )
            await session.commit()
            return {"id": row.id, "tenant_id": scim_tenant, "token": plaintext}


def _bearer(scim_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {scim_token['token']}"}


# --- authentication ----------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_or_unknown_bearer_is_401(async_client: AsyncClient):
    missing = await async_client.get("/api/v1/scim/v2/Users")
    assert missing.status_code == 401
    assert missing.json()["detail"]["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    unknown = await async_client.get(
        "/api/v1/scim/v2/Users", headers={"Authorization": "Bearer ha_scim_nope"}
    )
    assert unknown.status_code == 401
    junk = await async_client.get(
        "/api/v1/scim/v2/Users", headers={"Authorization": "Bearer " + "x" * 40}
    )
    assert junk.status_code == 401


@pytest.mark.asyncio
async def test_revoked_token_stops_resolving_immediately(
    async_client: AsyncClient, scim_tenant, scim_token
):
    ok = await async_client.get("/api/v1/scim/v2/Users", headers=_bearer(scim_token))
    assert ok.status_code == 200
    with tenant_context.system_scope("tests:scim-revoke"):
        async with AsyncSessionLocal() as session:
            row = await session.get(ScimToken, scim_token["id"])
            row.active = False
            await session.commit()
    revoked = await async_client.get("/api/v1/scim/v2/Users", headers=_bearer(scim_token))
    assert revoked.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_is_401(async_client: AsyncClient, scim_tenant):
    with tenant_context.system_scope("tests:scim-expired"):
        async with AsyncSessionLocal() as session:
            _row, plaintext = await scim.create_token(
                session,
                tenant_id=scim_tenant,
                name="stale",
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            await session.commit()
    expired = await async_client.get(
        "/api/v1/scim/v2/Users", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert expired.status_code == 401


@pytest.mark.asyncio
async def test_deleted_tenant_kills_its_tokens(async_client: AsyncClient, scim_tenant, scim_token):
    ok = await async_client.get("/api/v1/scim/v2/Users", headers=_bearer(scim_token))
    assert ok.status_code == 200
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(scim_tenant)})
        await session.commit()
    gone = await async_client.get("/api/v1/scim/v2/Users", headers=_bearer(scim_token))
    assert gone.status_code == 401


# --- Users -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_get_and_list_users(async_client: AsyncClient, scim_tenant, scim_token):
    created = await async_client.post(
        "/api/v1/scim/v2/Users",
        headers=_bearer(scim_token),
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "new.hire@example.com",
            "name": {"formatted": "New Hire"},
            "active": True,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["userName"] == "new.hire@example.com"
    assert body["meta"]["resourceType"] == "User"
    assert created.headers["Location"].endswith(f"/Users/{body['id']}")

    fetched = await async_client.get(
        f"/api/v1/scim/v2/Users/{body['id']}", headers=_bearer(scim_token)
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]

    listed = await async_client.get("/api/v1/scim/v2/Users", headers=_bearer(scim_token))
    assert listed.status_code == 200
    envelope = listed.json()
    assert envelope["totalResults"] >= 1
    assert envelope["startIndex"] == 1
    assert any(u["id"] == body["id"] for u in envelope["Resources"])


@pytest.mark.asyncio
async def test_duplicate_user_email_conflicts(async_client: AsyncClient, scim_token):
    payload = {"userName": "dupe@example.com", "active": True}
    first = await async_client.post(
        "/api/v1/scim/v2/Users", headers=_bearer(scim_token), json=payload
    )
    assert first.status_code == 201
    second = await async_client.post(
        "/api/v1/scim/v2/Users", headers=_bearer(scim_token), json=payload
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_pagination_and_username_filter(async_client: AsyncClient, scim_tenant, scim_token):
    for i in range(3):
        await async_client.post(
            "/api/v1/scim/v2/Users",
            headers=_bearer(scim_token),
            json={"userName": f"page{i}@example.com", "active": True},
        )
    page = await async_client.get(
        "/api/v1/scim/v2/Users",
        headers=_bearer(scim_token),
        params={"startIndex": 2, "count": 1},
    )
    body = page.json()
    assert body["startIndex"] == 2
    assert body["itemsPerPage"] == 1
    assert body["totalResults"] == 3

    filtered = await async_client.get(
        "/api/v1/scim/v2/Users",
        headers=_bearer(scim_token),
        params={"filter": 'userName eq "page1@example.com"'},
    )
    assert filtered.json()["totalResults"] == 1
    assert filtered.json()["Resources"][0]["userName"] == "page1@example.com"

    bad = await async_client.get(
        "/api/v1/scim/v2/Users",
        headers=_bearer(scim_token),
        params={"filter": "userName pr"},
    )
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_cross_tenant_user_id_is_hidden_404(
    async_client: AsyncClient, scim_tenant, scim_token
):
    foreign = await create_user(role="USER")
    denied = await async_client.get(
        f"/api/v1/scim/v2/Users/{foreign.id}", headers=_bearer(scim_token)
    )
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_deprovision_via_delete_deactivates_and_kills_sessions(
    async_client: AsyncClient, scim_tenant, scim_token
):
    user = await create_user(role="USER", tenant_id=scim_tenant, password="pass-12345")
    headers = await auth_headers(user)

    deleted = await async_client.delete(
        f"/api/v1/scim/v2/Users/{user.id}", headers=_bearer(scim_token)
    )
    assert deleted.status_code == 204

    async with AsyncSessionLocal() as session:
        row = await session.get(UserModel, user.id)
        assert row.is_active is False
        assert row.token_version > 1

    verify = await async_client.get("/api/v1/auth/validate", headers=headers)
    assert verify.status_code == 401


@pytest.mark.asyncio
async def test_patch_active_false_is_deprovision_true_restores(
    async_client: AsyncClient, scim_tenant, scim_token
):
    user = await create_user(role="USER", tenant_id=scim_tenant, password="pass-12345")
    headers = await auth_headers(user)
    patched = await async_client.patch(
        f"/api/v1/scim/v2/Users/{user.id}",
        headers=_bearer(scim_token),
        json={"operations": [{"op": "replace", "path": "active", "value": False}]},
    )
    assert patched.status_code == 200
    assert patched.json()["active"] is False
    assert (await async_client.get("/api/v1/auth/validate", headers=headers)).status_code == 401

    restored = await async_client.patch(
        f"/api/v1/scim/v2/Users/{user.id}",
        headers=_bearer(scim_token),
        json={"operations": [{"op": "replace", "path": "active", "value": True}]},
    )
    assert restored.json()["active"] is True

    bad = await async_client.patch(
        f"/api/v1/scim/v2/Users/{user.id}",
        headers=_bearer(scim_token),
        json={"operations": [{"op": "replace", "path": "notAPath", "value": 1}]},
    )
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_scim_role_is_capped_by_the_sso_default(
    async_client: AsyncClient, scim_tenant, scim_token
):
    with tenant_context.system_scope("tests:scim-config"):
        async with AsyncSessionLocal() as session:
            session.add(
                TenantSSOConfig(
                    tenant_id=scim_tenant,
                    issuer="https://idp.example.com",
                    client_id="cid",
                    client_secret_ref="slot",
                    allowed_domains=["example.com"],
                    default_role="MANAGER",
                    group_role_map={},
                    enabled=True,
                    jit_enabled=True,
                    scopes=[],
                    created_by=None,
                )
            )
            await session.commit()
    created = await async_client.post(
        "/api/v1/scim/v2/Users",
        headers=_bearer(scim_token),
        json={"userName": "capped@example.com", "active": True},
    )
    assert created.status_code == 201
    async with AsyncSessionLocal() as session:
        row = await session.get(UserModel, created.json()["id"])
        assert row.role == "MANAGER"


# --- Groups ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_crud_and_membership_moves(async_client: AsyncClient, scim_tenant, scim_token):
    member = await create_user(role="USER", tenant_id=scim_tenant)
    created = await async_client.post(
        "/api/v1/scim/v2/Groups",
        headers=_bearer(scim_token),
        json={"displayName": "Nurses", "members": [{"value": str(member.id)}]},
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]
    assert created.json()["members"][0]["value"] == str(member.id)

    fetched = await async_client.get(
        f"/api/v1/scim/v2/Groups/{group_id}", headers=_bearer(scim_token)
    )
    assert fetched.status_code == 200
    assert len(fetched.json()["members"]) == 1

    removed = await async_client.patch(
        f"/api/v1/scim/v2/Groups/{group_id}",
        headers=_bearer(scim_token),
        json={
            "operations": [
                {"op": "remove", "path": "members", "value": [{"value": str(member.id)}]}
            ]
        },
    )
    assert removed.status_code == 200
    assert removed.json()["members"] == []

    duplicate = await async_client.post(
        "/api/v1/scim/v2/Groups",
        headers=_bearer(scim_token),
        json={"displayName": "Nurses"},
    )
    assert duplicate.status_code == 409

    deleted = await async_client.delete(
        f"/api/v1/scim/v2/Groups/{group_id}", headers=_bearer(scim_token)
    )
    assert deleted.status_code == 204
    gone = await async_client.get(f"/api/v1/scim/v2/Groups/{group_id}", headers=_bearer(scim_token))
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_membership_change_applies_group_role_map_and_audits(
    async_client: AsyncClient, scim_tenant, scim_token
):
    with tenant_context.system_scope("tests:scim-config"):
        async with AsyncSessionLocal() as session:
            session.add(
                TenantSSOConfig(
                    tenant_id=scim_tenant,
                    issuer="https://idp.example.com",
                    client_id="cid",
                    client_secret_ref="slot",
                    allowed_domains=["example.com"],
                    default_role="USER",
                    group_role_map={"Doctors": "MANAGER"},
                    enabled=True,
                    jit_enabled=True,
                    scopes=[],
                    created_by=None,
                )
            )
            await session.commit()
    user = await create_user(role="USER", tenant_id=scim_tenant)
    created = await async_client.post(
        "/api/v1/scim/v2/Groups",
        headers=_bearer(scim_token),
        json={"displayName": "Doctors", "members": [{"value": str(user.id)}]},
    )
    group_id = created.json()["id"]

    async with AsyncSessionLocal() as session:
        row = await session.get(UserModel, user.id)
        assert row.role == "MANAGER"

    removed = await async_client.patch(
        f"/api/v1/scim/v2/Groups/{group_id}",
        headers=_bearer(scim_token),
        json={
            "operations": [{"op": "remove", "path": "members", "value": [{"value": str(user.id)}]}]
        },
    )
    assert removed.status_code == 200
    async with AsyncSessionLocal() as session:
        row = await session.get(UserModel, user.id)
        assert row.role == "USER"

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text(
                "SELECT DISTINCT action FROM audit_events "
                "WHERE tenant_id = :t AND action LIKE 'scim.%'"
            ),
            {"t": str(scim_tenant)},
        )
        actions = {r[0] for r in rows}
    assert "scim.role_change" in actions
    assert "scim.group_membership_changed" in actions


@pytest.mark.asyncio
async def test_group_membership_never_grants_system_admin(
    async_client: AsyncClient, scim_tenant, scim_token
):
    """A poisoned map value cannot reach SYSTEM_ADMIN through SCIM."""
    with tenant_context.system_scope("tests:scim-config"):
        async with AsyncSessionLocal() as session:
            session.add(
                TenantSSOConfig(
                    tenant_id=scim_tenant,
                    issuer="https://idp.example.com",
                    client_id="cid",
                    client_secret_ref="slot",
                    allowed_domains=["example.com"],
                    default_role="USER",
                    group_role_map={"Roots": "MANAGER"},
                    enabled=True,
                    jit_enabled=True,
                    scopes=[],
                    created_by=None,
                )
            )
            await session.commit()
    user = await create_user(role="USER", tenant_id=scim_tenant)
    created = await async_client.post(
        "/api/v1/scim/v2/Groups",
        headers=_bearer(scim_token),
        json={"displayName": "Roots", "members": [{"value": str(user.id)}]},
    )
    assert created.status_code == 201
    async with AsyncSessionLocal() as session:
        row = await session.get(UserModel, user.id)
        assert row.role == "MANAGER"
        assert row.role != "SYSTEM_ADMIN"


@pytest.mark.asyncio
async def test_cross_tenant_member_is_hidden_404(async_client: AsyncClient, scim_token):
    foreign = await create_user(role="USER")
    created = await async_client.post(
        "/api/v1/scim/v2/Groups",
        headers=_bearer(scim_token),
        json={"displayName": "Smugglers", "members": [{"value": str(foreign.id)}]},
    )
    assert created.status_code == 404


# --- token hygiene ------------------------------------------------------------


@pytest.mark.asyncio
async def test_plaintext_is_never_stored_or_logged(
    async_client: AsyncClient, scim_tenant, scim_token
):
    async with AsyncSessionLocal() as session:
        row = await session.get(ScimToken, scim_token["id"])
        assert row.token_hash != scim_token["token"]
        assert len(row.token_hash) == 64
        columns = await session.execute(text("SELECT * FROM scim_tokens LIMIT 1"))
        for value in dict(columns.mappings().one()).values():
            assert scim_token["token"] not in str(value)
