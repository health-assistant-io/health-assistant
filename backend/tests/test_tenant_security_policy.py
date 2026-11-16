"""Tenant security policy (ADR 0054 Phase 3 / program Phase 8 part B).

Pinned here:

1. **Policy surface** — ``GET/PATCH /tenants/{id}/security-policy``: the
   normalized shape, defaults, partial-patch semantics (omitted = untouched,
   ``null`` = clear), 422 validation (roles, CIDRs, bounds, SYSTEM_ADMIN
   never listable), tenant isolation (hidden 404 cross-tenant), the
   permission doors (MANAGER reads, only ADMIN/SYSTEM_ADMIN write) and the
   required audited diff of exactly the changed keys.
2. **MFA-required roles** — a policy-listed role is challenged at login
   (``mfa_required`` + ``enrollment_needed``), completes forced enrollment,
   and gets a session; unlisted roles sign in plainly; ``SYSTEM_ADMIN`` is
   always required (never stored, never waivable).
3. **Session window** — idle and max-age are enforced per request from the
   family's Redis anchor (a flip applies to a live session — no token
   caching) and the max-age cap is enforced on refresh from the
   authoritative family row.
4. **Concurrent session limit** — the LRU eviction of the oldest live
   family (its refresh chain dies, its bearer is dropped), audited.
5. **IP allowlist** — session issuance and refresh are refused outside the
   allowlist (typed ``ip_not_allowed``), allowed inside.
6. **Grant-surface knobs** — ``patient_sharing`` off blocks patient-initiated
   shares; a mid-session ``manager_scope`` flip narrows the next request
   (policy read per request).
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client
from app.core.security import decode_refresh_token, verify_access_token
from app.core.totp import totp_code_at
from app.models.audit_model import AuditEvent
from app.models.auth_session_model import AuthSessionModel
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.services.auth import security_policy
from tests._auth_helpers import auth_headers, create_tenant, create_user

# ---------------------------------------------------------------------------
# Pure normalization
# ---------------------------------------------------------------------------


def test_normalize_mfa_roles_keeps_policy_eligible_only():
    assert security_policy.normalize_mfa_required_roles(
        ["USER", "ADMIN", "USER", "SYSTEM_ADMIN", "nonsense"]
    ) == ("ADMIN", "USER")
    assert security_policy.normalize_mfa_required_roles(None) == ()
    assert security_policy.normalize_mfa_required_roles("USER") == ()


def test_normalize_bounded_int():
    assert security_policy.normalize_bounded_int(30, 5, 10080) == 30
    assert security_policy.normalize_bounded_int("45", 5, 10080) == 45
    assert security_policy.normalize_bounded_int(4, 5, 10080) is None
    assert security_policy.normalize_bounded_int(True, 1, 10) is None
    assert security_policy.normalize_bounded_int("x", 1, 10) is None


def test_normalize_ip_allowlist_canonicalizes():
    assert security_policy.normalize_ip_allowlist(["10.0.0.0/8", "10.0.5.0/24"]) == (
        "10.0.0.0/8",
        "10.0.5.0/24",
    )
    assert security_policy.normalize_ip_allowlist(["2001:db8::/32"]) == ("2001:db8::/32",)
    assert security_policy.normalize_ip_allowlist(["not-a-cidr"]) == ()
    assert security_policy.normalize_ip_allowlist(None) == ()


def test_requires_mfa_and_blocks_disable():
    policy = security_policy.SecurityPolicy(mfa_required_roles=("USER",))
    assert policy.requires_mfa(Role.USER)
    assert not policy.requires_mfa(Role.ADMIN)
    assert policy.requires_mfa(Role.SYSTEM_ADMIN)
    assert policy.blocks_mfa_disable(Role.USER)
    assert not policy.blocks_mfa_disable(Role.SYSTEM_ADMIN)


def test_allows_ip_semantics():
    empty = security_policy.SecurityPolicy()
    assert empty.allows_ip(None)
    assert empty.allows_ip("8.8.8.8")
    policy = security_policy.SecurityPolicy(ip_allowlist=("10.0.0.0/8",))
    assert policy.allows_ip("10.1.2.3")
    assert not policy.allows_ip("11.1.2.3")
    assert not policy.allows_ip(None)
    assert not policy.allows_ip("garbage")


def test_policy_to_dict_is_the_api_shape():
    policy = security_policy.normalize_security_policy(
        {
            "mfa_required_roles": ["MANAGER"],
            "session_idle_minutes": 30,
            "allow_patient_share": False,
            "manager_scope": "assigned",
        },
        tenant_id=uuid.uuid4(),
    )
    shape = policy.to_dict()
    assert shape["mfa_required_roles"] == ["MANAGER"]
    assert shape["system_admin_mfa_required"] is True
    assert shape["session_idle_minutes"] == 30
    assert shape["session_max_hours"] is None
    assert shape["patient_sharing"] is False
    assert shape["manager_scope"] == "assigned"


# ---------------------------------------------------------------------------
# Helpers + fixtures
# ---------------------------------------------------------------------------


async def _set_settings(tenant_id, settings: dict) -> None:
    async with AsyncSessionLocal() as session:
        tenant = await session.get(TenantModel, tenant_id)
        tenant.settings = settings
        flag_modified(tenant, "settings")
        await session.commit()


async def _make_patient(tenant_id, user_id=None) -> Patient:
    async with AsyncSessionLocal() as session:
        patient = Patient(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            name={"family": "Policy", "given": ["Test"]},
            gender="UNKNOWN",
        )
        session.add(patient)
        await session.commit()
        await session.refresh(patient)
    return patient


@pytest.fixture
def no_rate_limit():
    from app.core import rate_limit as rl

    async def _allow(*args, **kwargs):
        return None

    with pytest.MonkeyPatch.context() as monkey:
        monkey.setattr(rl, "_consume", _allow)
        yield


def _patch_client_ip(ip: str | None):
    return pytest.MonkeyPatch.context()


@pytest_asyncio.fixture
async def staffed_tenant():
    """One tenant with an ADMIN (policy writer), a MANAGER (reader) and two
    USERs (owner with a patient + a second member)."""
    tenant_id = await create_tenant()
    admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    manager = await create_user(role=Role.MANAGER, tenant_id=tenant_id)
    owner = await create_user(role=Role.USER, tenant_id=tenant_id, password="owner-password-123")
    patient = await _make_patient(tenant_id, owner.id)
    return {
        "tenant_id": tenant_id,
        "admin": admin,
        "manager": manager,
        "owner": owner,
        "patient": patient,
    }


async def _patch_policy(async_client: AsyncClient, tenant_id, payload: dict, headers: dict):
    return await async_client.patch(
        f"/api/v1/tenants/{tenant_id}/security-policy", json=payload, headers=headers
    )


async def _login(async_client: AsyncClient, user, password: str):
    return await async_client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": password},
    )


async def _audit_rows(action: str, **filters) -> list[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.action == action)
    for column, value in filters.items():
        stmt = stmt.where(getattr(AuditEvent, column) == value)
    async with AsyncSessionLocal() as session:
        return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Policy API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_policy_defaults(async_client: AsyncClient, staffed_tenant):
    _tenant_id, admin = staffed_tenant["tenant_id"], staffed_tenant["admin"]
    resp = await async_client.get(
        f"/api/v1/tenants/{staffed_tenant['tenant_id']}/security-policy",
        headers=await auth_headers(admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == str(staffed_tenant["tenant_id"])
    assert body["mfa_required_roles"] == []
    assert body["system_admin_mfa_required"] is True
    assert body["session_idle_minutes"] is None
    assert body["session_max_hours"] is None
    assert body["concurrent_session_limit"] is None
    assert body["ip_allowlist"] == []
    assert body["patient_sharing"] is True
    assert body["manager_scope"] == "tenant"


@pytest.mark.asyncio
async def test_patch_persists_normalized_and_audits_changed_keys(
    async_client: AsyncClient, staffed_tenant
):
    tenant_id, admin = staffed_tenant["tenant_id"], staffed_tenant["admin"]
    headers = await auth_headers(admin)
    resp = await _patch_policy(
        async_client,
        tenant_id,
        {
            "mfa_required_roles": ["USER", "MANAGER", "USER"],
            "session_idle_minutes": 30,
            "ip_allowlist": ["10.0.0.0/8"],
            "patient_sharing": False,
            "manager_scope": "assigned",
        },
        headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mfa_required_roles"] == ["MANAGER", "USER"]
    assert body["session_idle_minutes"] == 30
    assert body["ip_allowlist"] == ["10.0.0.0/8"]
    assert body["patient_sharing"] is False
    assert body["manager_scope"] == "assigned"
    assert body["session_max_hours"] is None

    # The stored settings keys: the Phase-2 storage key is preserved.
    async with AsyncSessionLocal() as session:
        raw = (await session.get(TenantModel, tenant_id)).settings
    assert raw["allow_patient_share"] is False
    assert raw["manager_scope"] == "assigned"
    assert raw["session_idle_minutes"] == 30

    # One audited change with a before/after diff of exactly the changed keys.
    rows = await _audit_rows("tenant.security_policy", resource_id=tenant_id)
    assert len(rows) == 1
    assert rows[0].outcome == "ok"
    before, after = rows[0].old_value, rows[0].new_value
    assert sorted(after) == [
        "allow_patient_share",
        "ip_allowlist",
        "manager_scope",
        "mfa_required_roles",
        "session_idle_minutes",
    ]
    assert before["allow_patient_share"] is None  # unset before the patch
    assert after["allow_patient_share"] is False
    assert before["manager_scope"] is None  # the stored key was unset (default applied on read)


@pytest.mark.asyncio
async def test_patch_partial_and_null_clears(async_client: AsyncClient, staffed_tenant):
    tenant_id, admin = staffed_tenant["tenant_id"], staffed_tenant["admin"]
    headers = await auth_headers(admin)
    ok = await _patch_policy(
        async_client, tenant_id, {"session_idle_minutes": 30, "manager_scope": "assigned"}, headers
    )
    assert ok.status_code == 200
    ok = await _patch_policy(async_client, tenant_id, {"session_max_hours": 4}, headers)
    assert ok.status_code == 200
    assert ok.json()["session_idle_minutes"] == 30  # untouched
    assert ok.json()["session_max_hours"] == 4

    cleared = await _patch_policy(async_client, tenant_id, {"session_max_hours": None}, headers)
    assert cleared.status_code == 200
    assert cleared.json()["session_max_hours"] is None
    assert cleared.json()["session_idle_minutes"] == 30
    assert cleared.json()["manager_scope"] == "assigned"  # untouched by null on another key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"mfa_required_roles": ["SYSTEM_ADMIN"]},
        {"mfa_required_roles": ["WIZARD"]},
        {"mfa_required_roles": "USER"},
        {"session_idle_minutes": 4},
        {"session_idle_minutes": 20000},
        {"session_max_hours": 0},
        {"concurrent_session_limit": 0},
        {"concurrent_session_limit": 1000},
        {"ip_allowlist": ["10.0.0.0/999"]},
        {"ip_allowlist": "10.0.0.0/8"},
        {"manager_scope": "everything"},
    ],
)
async def test_patch_validation_422(async_client: AsyncClient, staffed_tenant, payload):
    headers = await auth_headers(staffed_tenant["admin"])
    resp = await _patch_policy(async_client, staffed_tenant["tenant_id"], payload, headers)
    assert resp.status_code == 422, (payload, resp.text)


@pytest.mark.asyncio
async def test_policy_tenant_isolation_hidden_404(async_client: AsyncClient):
    tenant_a = await create_tenant()
    tenant_b = await create_tenant()
    admin_a = await create_user(role=Role.ADMIN, tenant_id=tenant_a)
    headers = await auth_headers(admin_a)
    get = await async_client.get(f"/api/v1/tenants/{tenant_b}/security-policy", headers=headers)
    assert get.status_code == 404
    patch = await _patch_policy(async_client, tenant_b, {"session_idle_minutes": 30}, headers)
    assert patch.status_code == 404


@pytest.mark.asyncio
async def test_system_admin_manages_any_tenant_policy(async_client: AsyncClient, staffed_tenant):
    sysadmin = await create_user(role=Role.SYSTEM_ADMIN)
    headers = await auth_headers(sysadmin)
    resp = await _patch_policy(
        async_client,
        staffed_tenant["tenant_id"],
        {"concurrent_session_limit": 3},
        headers,
    )
    assert resp.status_code == 200
    assert resp.json()["concurrent_session_limit"] == 3


@pytest.mark.asyncio
async def test_policy_permission_doors(async_client: AsyncClient, staffed_tenant):
    tenant_id = staffed_tenant["tenant_id"]
    manager_headers = await auth_headers(staffed_tenant["manager"])
    user_headers = await auth_headers(staffed_tenant["owner"])

    # MANAGER reads the policy (settings:read @ tenant), cannot write.
    read = await async_client.get(
        f"/api/v1/tenants/{tenant_id}/security-policy", headers=manager_headers
    )
    assert read.status_code == 200
    write = await _patch_policy(
        async_client, tenant_id, {"session_idle_minutes": 30}, manager_headers
    )
    assert write.status_code == 403
    assert write.headers.get("x-authz-denied") == "1"

    # USER is denied at the gate (no settings:read).
    denied = await async_client.get(
        f"/api/v1/tenants/{tenant_id}/security-policy", headers=user_headers
    )
    assert denied.status_code == 403


# ---------------------------------------------------------------------------
# MFA-required roles at login
# ---------------------------------------------------------------------------


async def _enroll_and_confirm(user) -> dict:
    from app.services.auth import mfa_service

    data = await mfa_service.begin_enrollment(user)
    assert await mfa_service.confirm_enrollment(user.id, totp_code_at(data["secret"], time.time()))
    return data


@pytest.mark.asyncio
async def test_policy_required_role_is_challenged_and_enrolls(
    async_client: AsyncClient, staffed_tenant, no_rate_limit
):
    tenant_id, admin, owner = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
    )
    ok = await _patch_policy(
        async_client,
        tenant_id,
        {"mfa_required_roles": ["USER"]},
        await auth_headers(admin),
    )
    assert ok.status_code == 200

    # The listed role is challenged before any session is issued.
    resp = await _login(async_client, owner, "owner-password-123")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"] == "mfa_required"
    assert body["enrollment_needed"] is True

    # Forced enrollment rides the challenge, then verify issues the session.
    provision = await async_client.post(
        "/api/v1/auth/mfa/enroll", json={"mfa_token": body["mfa_token"]}
    )
    assert provision.status_code == 200, provision.text
    verify = await async_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "mfa_token": body["mfa_token"],
            "code": totp_code_at(provision.json()["secret"], time.time()),
        },
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["access_token"] and verify.json()["refresh_token"]


@pytest.mark.asyncio
async def test_unlisted_role_signs_in_plainly(
    async_client: AsyncClient, staffed_tenant, no_rate_limit
):
    """A role the policy does not list signs in plainly even when another
    role in the same tenant is required."""
    manager = await create_user(
        role=Role.MANAGER,
        tenant_id=staffed_tenant["tenant_id"],
        password="manager-password-123",
    )
    ok = await _patch_policy(
        async_client,
        staffed_tenant["tenant_id"],
        {"mfa_required_roles": ["USER"]},
        await auth_headers(staffed_tenant["admin"]),
    )
    assert ok.status_code == 200
    resp = await _login(async_client, manager, "manager-password-123")
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


@pytest.mark.asyncio
async def test_system_admin_login_always_challenged(async_client: AsyncClient, no_rate_limit):
    sysadmin = await create_user(role=Role.SYSTEM_ADMIN, password="root-password-123")
    resp = await _login(async_client, sysadmin, "root-password-123")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"] == "mfa_required"
    assert body["enrollment_needed"] is True
    provision = await async_client.post(
        "/api/v1/auth/mfa/enroll", json={"mfa_token": body["mfa_token"]}
    )
    assert provision.status_code == 200
    verify = await async_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "mfa_token": body["mfa_token"],
            "code": totp_code_at(provision.json()["secret"], time.time()),
        },
    )
    assert verify.status_code == 200


# ---------------------------------------------------------------------------
# Session window (idle / max age)
# ---------------------------------------------------------------------------


def _window_key(fid: str) -> str:
    return f"sesspol:{fid}"


async def _login_tokens(async_client: AsyncClient, user, password: str):
    resp = await _login(async_client, user, password)
    assert resp.status_code == 200, resp.text
    # The tests below drive explicit Bearer headers (and later act as other
    # principals on the same client); the §10 cookie jar would win over
    # them inside get_token, so it is cleared after every credential-free
    # body-token login.
    async_client.cookies.clear()
    body = resp.json()
    access = verify_access_token(body["access_token"])
    refresh = decode_refresh_token(body["refresh_token"])
    return body, access, refresh


@pytest.mark.asyncio
async def test_idle_window_denies_after_gap(
    async_client: AsyncClient, staffed_tenant, no_rate_limit
):
    tenant_id, admin, owner = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
    )
    ok = await _patch_policy(
        async_client, tenant_id, {"session_idle_minutes": 5}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    body, access, _refresh = await _login_tokens(async_client, owner, "owner-password-123")
    fid = access["fid"]
    anchor = await redis_client.hgetall(_window_key(fid))
    assert anchor and "start" in anchor and "seen" in anchor

    # Fresh token → allowed (the window anchor is current).
    fresh = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert fresh.status_code == 200

    # Simulate an idle gap longer than the policy window → next request denied.
    stale_seen = str(int(time.time()) - 6 * 60)
    await redis_client.hset(_window_key(fid), mapping={"seen": stale_seen})
    denied = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert denied.status_code == 401
    assert denied.json()["detail"] == "session_idle_expired"


@pytest.mark.asyncio
async def test_max_age_window_denies_per_request(
    async_client: AsyncClient, staffed_tenant, no_rate_limit
):
    tenant_id, admin, owner = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
    )
    ok = await _patch_policy(
        async_client, tenant_id, {"session_max_hours": 1}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    body, access, _refresh = await _login_tokens(async_client, owner, "owner-password-123")
    fid = access["fid"]
    stale_start = str(int(time.time()) - 2 * 3600)
    await redis_client.hset(_window_key(fid), mapping={"start": stale_start})
    denied = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert denied.status_code == 401
    assert denied.json()["detail"] == "session_max_age_expired"


@pytest.mark.asyncio
async def test_refresh_enforces_max_age_from_family_row(
    async_client: AsyncClient, staffed_tenant, no_rate_limit
):
    tenant_id, admin, owner = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
    )
    ok = await _patch_policy(
        async_client, tenant_id, {"session_max_hours": 1}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    body, _access, refresh = await _login_tokens(async_client, owner, "owner-password-123")
    fid = refresh["fid"]

    # Age the authoritative family row past the policy cap.
    async with AsyncSessionLocal() as session:
        row = await session.get(AuthSessionModel, uuid.UUID(fid))
        row.created_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()

    denied = await async_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert denied.status_code == 401
    assert denied.json()["detail"] == "session_max_age_expired"

    # The family was revoked — the family cannot be replayed either.
    async with AsyncSessionLocal() as session:
        row = await session.get(AuthSessionModel, uuid.UUID(fid))
        assert row.revoked_at is not None


@pytest.mark.asyncio
async def test_no_policy_no_redis_traffic_on_validate(
    async_client: AsyncClient, staffed_tenant, no_rate_limit
):
    """Defaults (no window) keep verification free of window checks."""
    body, _access, _refresh = await _login_tokens(
        async_client, staffed_tenant["owner"], "owner-password-123"
    )
    resp = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Concurrent session limit (LRU eviction via the jti store)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_limit_evicts_oldest_family(
    async_client: AsyncClient, staffed_tenant, no_rate_limit
):
    tenant_id, admin, owner = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
    )
    ok = await _patch_policy(
        async_client, tenant_id, {"concurrent_session_limit": 2}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    first, _a1, r1 = await _login_tokens(async_client, owner, "owner-password-123")
    _second, _a2, r2 = await _login_tokens(async_client, owner, "owner-password-123")
    fid1, fid2 = r1["fid"], r2["fid"]

    # Third login crosses the limit → the oldest live family is evicted.
    third, _a3, r3 = await _login_tokens(async_client, owner, "owner-password-123")
    fid3 = r3["fid"]

    async with AsyncSessionLocal() as session:
        row1 = await session.get(AuthSessionModel, uuid.UUID(fid1))
        row2 = await session.get(AuthSessionModel, uuid.UUID(fid2))
        row3 = await session.get(AuthSessionModel, uuid.UUID(fid3))
    assert row1.revoked_at is not None
    assert row2.revoked_at is None
    assert row3.revoked_at is None

    # The evicted family's refresh chain is dead at once …
    replay = await async_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert replay.status_code == 401
    # … and its bound bearer was dropped from the hot layer (the third
    # session still validates).
    survivor = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": f"Bearer {third['access_token']}"}
    )
    assert survivor.status_code == 200

    evictions = await _audit_rows("session.evict", resource_id=uuid.UUID(fid1))
    assert len(evictions) == 1
    assert evictions[0].new_value["cause"] == "concurrent_session_limit"
    assert evictions[0].new_value["limit"] == 2


@pytest.mark.asyncio
async def test_rotation_does_not_count_against_the_limit(
    async_client: AsyncClient, staffed_tenant, no_rate_limit
):
    """A refresh rotation renews its own family — it never evicts."""
    tenant_id, admin, owner = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
    )
    ok = await _patch_policy(
        async_client, tenant_id, {"concurrent_session_limit": 1}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    body, _a, r = await _login_tokens(async_client, owner, "owner-password-123")
    rotated = await async_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rotated.status_code == 200, rotated.text
    async with AsyncSessionLocal() as session:
        row = await session.get(AuthSessionModel, uuid.UUID(r["fid"]))
    assert row.revoked_at is None
    assert (await _audit_rows("session.evict", tenant_id=tenant_id)) == []


# ---------------------------------------------------------------------------
# IP allowlist (session issuance + refresh)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ip_allowlist_denies_issuance_and_allows_inside(
    async_client: AsyncClient, staffed_tenant, no_rate_limit, monkeypatch
):
    tenant_id, admin, owner = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
    )
    ok = await _patch_policy(
        async_client, tenant_id, {"ip_allowlist": ["10.0.0.0/8"]}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    # The test client's address is not in the allowlist → denied + audited.
    denied = await _login(async_client, owner, "owner-password-123")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "ip_not_allowed"
    denials = await _audit_rows("auth.login", outcome="denied")
    assert any(r.new_value and r.new_value.get("detail") == "ip_not_allowed" for r in denials)

    # Inside the allowlist → allowed.
    with monkeypatch.context() as m:
        m.setattr(security_policy, "request_client_ip", lambda request: "10.1.2.3")
        allowed = await _login(async_client, owner, "owner-password-123")
    assert allowed.status_code == 200, allowed.text


@pytest.mark.asyncio
async def test_ip_allowlist_gates_refresh(
    async_client: AsyncClient, staffed_tenant, no_rate_limit, monkeypatch
):
    tenant_id, admin, owner = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
    )
    with monkeypatch.context() as m:
        m.setattr(security_policy, "request_client_ip", lambda request: "10.10.10.10")
        body, _a, _r = await _login_tokens(async_client, owner, "owner-password-123")
    ok = await _patch_policy(
        async_client, tenant_id, {"ip_allowlist": ["192.168.0.0/16"]}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    # Out of the (new) allowlist → refresh refused, audited denied.
    denied = await async_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "ip_not_allowed"
    denials = await _audit_rows("auth.refresh", outcome="denied")
    assert any(r.new_value and r.new_value.get("detail") == "ip_not_allowed" for r in denials)

    # From an allowed address the same family refreshes.
    with monkeypatch.context() as m:
        m.setattr(security_policy, "request_client_ip", lambda request: "192.168.1.20")
        allowed = await async_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
        )
    assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# Grant-surface knobs (patient_sharing / manager_scope, per request)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_sharing_off_blocks_patient_shares(async_client: AsyncClient, staffed_tenant):
    tenant_id, admin, owner, manager, patient = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
        staffed_tenant["manager"],
        staffed_tenant["patient"],
    )
    ok = await _patch_policy(
        async_client, tenant_id, {"patient_sharing": False}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    owner_headers = await auth_headers(owner, step_up=True)
    resp = await async_client.post(
        f"/api/v1/patients/{patient.id}/access",
        json={"permission": "read", "grantee_user_id": str(manager.id)},
        headers=owner_headers,
    )
    # Create-time refusal for source=patient is the 403 AuthorizationError
    # (the accept-time flip answers 409 — pinned in test_patient_grants.py).
    assert resp.status_code == 403, resp.text
    assert "disabled for this tenant" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patient_sharing_key_roundtrip(async_client: AsyncClient, staffed_tenant):
    """``patient_sharing`` is the policy field of the stored Phase-2 key:
    a settings row written with ``allow_patient_share: false`` reads as
    ``patient_sharing: false`` on the policy surface."""
    tenant_id = staffed_tenant["tenant_id"]
    await _set_settings(tenant_id, {"allow_patient_share": False})
    read = await async_client.get(
        f"/api/v1/tenants/{tenant_id}/security-policy",
        headers=await auth_headers(staffed_tenant["admin"]),
    )
    assert read.json()["patient_sharing"] is False
    await _set_settings(tenant_id, {})


@pytest.mark.asyncio
async def test_manager_scope_flip_applies_mid_session(async_client: AsyncClient, staffed_tenant):
    """The policy is read per request — flipping ``manager_scope`` narrows a
    live MANAGER session on its next request (nothing is token-cached)."""
    tenant_id, admin, manager, patient = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["manager"],
        staffed_tenant["patient"],
    )
    manager_headers = await auth_headers(manager)

    # Tenant-wide default: the manager sees the owner's patient.
    before = await async_client.get("/api/v1/patients", headers=manager_headers)
    assert before.status_code == 200
    assert any(item["id"] == str(patient.id) for item in before.json()["items"])

    ok = await _patch_policy(
        async_client, tenant_id, {"manager_scope": "assigned"}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    # Same token, next request: narrowed to own + grants (the manager owns
    # no patient here).
    after = await async_client.get("/api/v1/patients", headers=manager_headers)
    assert after.status_code == 200
    assert all(item["id"] != str(patient.id) for item in after.json()["items"])


# ---------------------------------------------------------------------------
# S1 — the audit row rides the writer's transaction
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def tolerant_client():
    """A client that returns the 500 response instead of re-raising (the
    AuditWriteError → 500 path)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_policy_change_is_refused_when_its_audit_row_fails(tolerant_client, staffed_tenant):
    """S1 — the policy change and its audit row commit together: an audit
    append failure rolls the change back (nothing landed unrecorded)."""
    from unittest.mock import patch

    tenant_id, admin = staffed_tenant["tenant_id"], staffed_tenant["admin"]
    ok = await _patch_policy(
        tolerant_client,
        tenant_id,
        {"concurrent_session_limit": 3},
        await auth_headers(admin),
    )
    assert ok.status_code == 200

    with patch(
        "app.services.audit.audit_service._chain_append",
        side_effect=RuntimeError("simulated audit append failure"),
    ):
        refused = await _patch_policy(
            tolerant_client,
            tenant_id,
            {"concurrent_session_limit": 5},
            await auth_headers(admin),
        )
        assert refused.status_code == 500

    # Rolled back: the stored policy still says 3.
    async with AsyncSessionLocal() as session:
        raw = (await session.get(TenantModel, tenant_id)).settings
    policy = security_policy.normalize_security_policy(raw, tenant_id=tenant_id)
    assert policy.concurrent_session_limit == 3

    # And the refused attempt left no audit row claiming success.
    rows = await _audit_rows("tenant.security_policy", resource_id=tenant_id)
    assert all((row.new_value or {}).get("concurrent_session_limit") != 5 for row in rows)


@pytest.mark.asyncio
async def test_concurrent_limit_evicts_by_creation_not_activity(
    async_client: AsyncClient, staffed_tenant, no_rate_limit
):
    """S6 — eviction is oldest-**created**, not usage-based: the family
    that was used *most recently* but created *first* is still the one
    signed out."""
    tenant_id, admin, owner = (
        staffed_tenant["tenant_id"],
        staffed_tenant["admin"],
        staffed_tenant["owner"],
    )
    ok = await _patch_policy(
        async_client, tenant_id, {"concurrent_session_limit": 2}, await auth_headers(admin)
    )
    assert ok.status_code == 200

    _body1, _access1, r1 = await _login_tokens(async_client, owner, "owner-password-123")
    _second, _a2, _r2 = await _login_tokens(async_client, owner, "owner-password-123")

    # Activity on the OLDEST family — under usage-based LRU it would now be
    # the survivor.
    validate = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": f"Bearer {_body1['access_token']}"}
    )
    assert validate.status_code == 200

    _third, _a3, r3 = await _login_tokens(async_client, owner, "owner-password-123")
    fid1, fid3 = r1["fid"], r3["fid"]

    async with AsyncSessionLocal() as session:
        row1 = await session.get(AuthSessionModel, uuid.UUID(fid1))
        row3 = await session.get(AuthSessionModel, uuid.UUID(fid3))
    assert row1.revoked_at is not None  # oldest-created evicted despite activity
    assert row3.revoked_at is None

    evictions = await _audit_rows("session.evict", resource_id=uuid.UUID(fid1))
    assert len(evictions) == 1
    assert evictions[0].new_value["cause"] == "concurrent_session_limit"
