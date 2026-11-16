"""Tenant-admin SSO config + SCIM token surfaces (Phase 12B).

Covers: permission doors (a plain USER is 403'd by the gate), the tenant
boundary (ADMIN foreign-tenant answers hidden-404, audited; SYSTEM_ADMIN
anywhere), the secret-slot law (slot names only — no value ever crosses
the surface, and the slot response never carries the secret), the
one-time plaintext SCIM token return (create once, never again; revoke
kills the bearer), test-connection answering one bit per check, and the
audit trail.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text

import app.core.rate_limit as rate_limit_module
import app.core.token_store as token_store
from app.core.database import AsyncSessionLocal
from tests._auth_helpers import auth_headers, create_tenant, create_user


class FakeRedis:
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
    monkeypatch.setenv("HA_SSO_CLIENT_SECRET_ADMIN_TEST_SLOT", "value-not-shown-anywhere")
    return fake


@pytest_asyncio.fixture(autouse=True)
async def _clean_identity_tables():
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
async def tenant_admin():
    return await create_user(role="ADMIN", full_name="Tenant Admin")


@pytest_asyncio.fixture
async def system_admin():
    return await create_user(role="SYSTEM_ADMIN")


PAYLOAD = {
    "issuer": "https://admin-sso.example.com",
    "client_id": "client-abc",
    "client_secret_ref": "admin-test/slot",
    "allowed_domains": ["corp.example.com"],
    "default_role": "USER",
    "group_role_map": {"staff": "MANAGER"},
    "jit_enabled": True,
    "enabled": False,
    "scopes": ["openid", "email"],
}


@pytest.mark.asyncio
async def test_system_admin_creates_lists_and_updates(
    async_client: AsyncClient, system_admin, tenant_admin
):
    headers = await auth_headers(system_admin)
    created = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json=PAYLOAD,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["issuer"] == PAYLOAD["issuer"]
    assert body["client_secret_ref"] == "admin-test/slot"
    assert body["enabled"] is False

    listed = await async_client.get(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso", headers=headers
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    updated = await async_client.patch(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso/{body['id']}",
        headers=headers,
        json={"enabled": True, "default_role": "MANAGER"},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert updated.json()["default_role"] == "MANAGER"


@pytest.mark.asyncio
async def test_plain_user_is_denied_by_the_gate(async_client: AsyncClient, tenant_admin):
    plain = await create_user(role="USER", tenant_id=tenant_admin.tenant_id)
    response = await async_client.get(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=await auth_headers(plain),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_tenant_admin_manages_own_tenant_only(async_client: AsyncClient, tenant_admin):
    other = await create_tenant(name="Other Tenant")
    headers = await auth_headers(tenant_admin)

    created = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json=PAYLOAD,
    )
    assert created.status_code == 201

    foreign = await async_client.post(
        f"/api/v1/admin/tenants/{other}/sso", headers=headers, json=PAYLOAD
    )
    assert foreign.status_code == 404
    foreign_list = await async_client.get(f"/api/v1/admin/tenants/{other}/sso", headers=headers)
    assert foreign_list.status_code == 404


@pytest.mark.asyncio
async def test_system_admin_role_and_map_are_capped(
    async_client: AsyncClient, system_admin, tenant_admin
):
    headers = await auth_headers(system_admin)
    poisoned = {**PAYLOAD, "default_role": "SYSTEM_ADMIN"}
    refused = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json=poisoned,
    )
    assert refused.status_code == 422
    poisoned_map = {**PAYLOAD, "group_role_map": {"root": "SYSTEM_ADMIN"}}
    refused_map = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json=poisoned_map,
    )
    assert refused_map.status_code == 422
    no_slot = {**PAYLOAD, "client_secret_ref": ""}
    refused_slot = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json=no_slot,
    )
    assert refused_slot.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_issuer_conflicts(async_client: AsyncClient, tenant_admin):
    headers = await auth_headers(tenant_admin)
    first = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso", headers=headers, json=PAYLOAD
    )
    assert first.status_code == 201
    second = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso", headers=headers, json=PAYLOAD
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_an_enabled_domain_can_only_be_claimed_once(
    async_client: AsyncClient, system_admin, tenant_admin
):
    """The login-page lookup routes a domain to exactly one tenant.

    Two enabled configs listing the same domain would make sign-in depend on
    row order, so the second claim is a typed 409 — cross-tenant included.
    """
    headers = await auth_headers(system_admin)
    first = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json={**PAYLOAD, "enabled": True},
    )
    assert first.status_code == 201, first.text

    other_tenant = await create_tenant()
    clash = await async_client.post(
        f"/api/v1/admin/tenants/{other_tenant}/sso",
        headers=headers,
        json={
            **PAYLOAD,
            "issuer": "https://other-idp.example.com",
            "client_secret_ref": "admin-test/other-slot",
            "allowed_domains": ["corp.example.com", "other.example.com"],
            "enabled": True,
        },
    )
    assert clash.status_code == 409, clash.text
    assert "corp.example.com" in clash.json()["detail"]

    # Disabled configs claim nothing, so the same domain is free.
    disabled = await async_client.post(
        f"/api/v1/admin/tenants/{other_tenant}/sso",
        headers=headers,
        json={
            **PAYLOAD,
            "issuer": "https://other-idp.example.com",
            "client_secret_ref": "admin-test/other-slot",
            "allowed_domains": ["corp.example.com"],
            "enabled": False,
        },
    )
    assert disabled.status_code == 201, disabled.text

    # Enabling it later collides with the first config's claim.
    enable = await async_client.patch(
        f"/api/v1/admin/tenants/{other_tenant}/sso/{disabled.json()['id']}",
        headers=headers,
        json={"enabled": True},
    )
    assert enable.status_code == 409, enable.text


@pytest.mark.asyncio
async def test_issuer_cannot_change_while_the_config_is_enabled(
    async_client: AsyncClient, tenant_admin
):
    """Identity links are keyed on ``(issuer, subject)`` — moving the issuer
    would orphan every link, so a live config is immutable on that field."""
    headers = await auth_headers(tenant_admin)
    created = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json={**PAYLOAD, "enabled": True},
    )
    assert created.status_code == 201, created.text

    moved = await async_client.patch(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso/{created.json()['id']}",
        headers=headers,
        json={"issuer": "https://moved-idp.example.com"},
    )
    assert moved.status_code == 409, moved.text

    renamed = await async_client.patch(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso/{created.json()['id']}",
        headers=headers,
        json={"client_id": "another-client"},
    )
    assert renamed.status_code == 200, renamed.text


@pytest.mark.asyncio
async def test_a_secret_value_cannot_be_pasted_into_the_slot_field(
    async_client: AsyncClient, tenant_admin
):
    """The slot is a *name*: punctuation outside the slot alphabet is refused."""
    headers = await auth_headers(tenant_admin)
    response = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json={**PAYLOAD, "client_secret_ref": "s3cr3t=value:with!punct*"},
    )
    assert response.status_code in (400, 422), response.text


@pytest.mark.asyncio
async def test_delete_sso_config(async_client: AsyncClient, system_admin, tenant_admin):
    headers = await auth_headers(system_admin)
    created = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json=PAYLOAD,
    )
    deleted = await async_client.delete(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso/{created.json()['id']}",
        headers=headers,
    )
    assert deleted.status_code == 200
    listed = await async_client.get(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso", headers=headers
    )
    assert listed.json()["total"] == 0


@pytest.mark.asyncio
async def test_test_connection_answers_without_echoing(
    async_client: AsyncClient, system_admin, tenant_admin
):
    headers = await auth_headers(system_admin)
    created = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso",
        headers=headers,
        json=PAYLOAD,
    )
    config_id = created.json()["id"]
    probe = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/sso/{config_id}/test-connection",
        headers=headers,
    )
    assert probe.status_code == 200
    body = probe.json()
    assert body["secret_resolved"] is True
    assert body["discovery_ok"] is False
    assert "value-not-shown-anywhere" not in probe.text
    assert "admin-sso.example.com/jwks" not in probe.text


@pytest.mark.asyncio
async def test_scim_token_one_time_plaintext_then_revocation(
    async_client: AsyncClient, system_admin, tenant_admin
):
    headers = await auth_headers(system_admin)
    tid = tenant_admin.tenant_id
    created = await async_client.post(
        f"/api/v1/admin/tenants/{tid}/scim-tokens",
        headers=headers,
        json={"name": "azure-ad"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("ha_scim_")
    assert body["active"] is True

    listed = await async_client.get(f"/api/v1/admin/tenants/{tid}/scim-tokens", headers=headers)
    items = listed.json()["items"]
    assert listed.json()["total"] == 1
    assert "token" not in items[0]

    used = await async_client.get(
        "/api/v1/scim/v2/Users", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert used.status_code == 200

    revoked = await async_client.delete(
        f"/api/v1/admin/tenants/{tid}/scim-tokens/{body['id']}", headers=headers
    )
    assert revoked.status_code == 200
    dead = await async_client.get(
        "/api/v1/scim/v2/Users", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert dead.status_code == 401

    again = await async_client.delete(
        f"/api/v1/admin/tenants/{tid}/scim-tokens/{body['id']}", headers=headers
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_tenant_admin_creates_own_scim_token_foreign_hidden(
    async_client: AsyncClient, tenant_admin
):
    other = await create_tenant(name="Other")
    headers = await auth_headers(tenant_admin)
    own = await async_client.post(
        f"/api/v1/admin/tenants/{tenant_admin.tenant_id}/scim-tokens",
        headers=headers,
        json={"name": "own"},
    )
    assert own.status_code == 201
    foreign = await async_client.post(
        f"/api/v1/admin/tenants/{other}/scim-tokens", headers=headers, json={"name": "x"}
    )
    assert foreign.status_code == 404
    foreign_list = await async_client.get(
        f"/api/v1/admin/tenants/{other}/scim-tokens", headers=headers
    )
    assert foreign_list.status_code == 404


@pytest.mark.asyncio
async def test_mutations_are_audited(async_client: AsyncClient, system_admin, tenant_admin):
    headers = await auth_headers(system_admin)
    tid = tenant_admin.tenant_id
    created = await async_client.post(
        f"/api/v1/admin/tenants/{tid}/sso", headers=headers, json=PAYLOAD
    )
    config_id = created.json()["id"]
    await async_client.patch(
        f"/api/v1/admin/tenants/{tid}/sso/{config_id}",
        headers=headers,
        json={"enabled": True},
    )
    await async_client.delete(f"/api/v1/admin/tenants/{tid}/sso/{config_id}", headers=headers)
    token = await async_client.post(
        f"/api/v1/admin/tenants/{tid}/scim-tokens", headers=headers, json={"name": "audit-me"}
    )
    await async_client.delete(
        f"/api/v1/admin/tenants/{tid}/scim-tokens/{token.json()['id']}", headers=headers
    )

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT DISTINCT action FROM audit_events WHERE tenant_id = :t"),
            {"t": str(tid)},
        )
        actions = {r[0] for r in rows}
    assert "sso_config_created" in actions
    assert "sso_config_updated" in actions
    assert "sso_config_deleted" in actions
    assert "scim.token_created" in actions
    assert "scim.token_revoked" in actions
