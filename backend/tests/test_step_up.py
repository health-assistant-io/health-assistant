"""Step-up re-authentication (ADR 0054 Phase 6 — program 12C1).

A live session is not sufficient proof of presence for four actions: a role
change, a data export, a break-glass elevation and an admin-level patient
grant. Each answers ``403 {"code": "step_up_required", "action": ...}``
until ``POST /auth/step-up`` verifies the account's own factors and records
a proof for that action on that sign-in family.

Covered here: the typed refusal on each gated action, password and
TOTP/recovery proofs, the action/family/window binding (a role-change proof
never authorizes an export, another device's proof is invisible, an expired
proof is dead), the federated fallback for SSO-only accounts, and the audit
rows for success and refusal.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core import token_store
from app.core.database import AsyncSessionLocal
from app.core.security import decode_token
from app.core.totp import totp_code_at
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.services.auth import mfa_service, step_up
from tests._auth_helpers import AUTH_MODE_PASSWORD, auth_headers, bearer_token, create_user

ACTION_PASSWORD = "correct-password-123"


class FakeRedis:
    """Dict-backed stand-in for the token store / limiter command surface."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = str(value)
        return True

    async def get(self, key):
        return self.store.get(key)

    async def getdel(self, key):
        return self.store.pop(key, None)

    async def delete(self, *keys):
        return sum(1 for key in keys if self.store.pop(key, None) is not None)

    async def exists(self, *keys):
        return sum(1 for key in keys if key in self.store)

    async def incr(self, key):
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key, ttl):
        return True

    async def scan_iter(self, pattern):
        import fnmatch

        for key in list(self.store):
            if fnmatch.fnmatch(key, pattern):
                yield key


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.core.token_store.redis_client", fake)
    monkeypatch.setattr("app.core.rate_limit.redis_client", fake)
    return fake


@pytest_asyncio.fixture
async def admin_user():
    return await create_user(role=Role.ADMIN, password=ACTION_PASSWORD)


@pytest_asyncio.fixture
async def target_user(admin_user):
    return await create_user(role=Role.USER, tenant_id=admin_user.tenant_id)


async def _session_headers(user, *, auth_mode: str = AUTH_MODE_PASSWORD, extra_claims=None) -> dict:
    """Bearer headers for a real sign-in **with** a family (``fid``).

    ``auth_headers`` mints auth-only tokens by default (no family, no
    refresh) — step-up is family-bound, so this suite signs in for real
    and simply records no proof.
    """
    from app.services.auth.auth_session_service import issue_session

    issued = await issue_session(
        user,
        auth_mode=auth_mode,
        client_label="pytest",
        extra_claims=extra_claims,
    )
    return {"Authorization": f"Bearer {issued.access_token}"}


async def _step_up(client: AsyncClient, headers: dict, action: str, **body) -> object:
    return await client.post(
        "/api/v1/auth/step-up", json={"action": action, **body}, headers=headers
    )


async def _audit_actions(user_id) -> list[str]:
    from sqlalchemy import select

    from app.models.audit_model import AuditEvent

    async with AsyncSessionLocal() as session:
        rows = await session.execute(select(AuditEvent.action).where(AuditEvent.user_id == user_id))
        return list(rows.scalars().all())


# --- the gate ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_change_without_step_up_is_a_typed_403(
    async_client: AsyncClient, admin_user, target_user
):
    headers = await _session_headers(admin_user)

    response = await async_client.put(
        f"/api/v1/users/{target_user.id}", params={"role": "MANAGER"}, headers=headers
    )

    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "step_up_required"
    assert detail["action"] == "role_change"
    assert detail["expires_in"] == step_up.STEP_UP_TTL_SECONDS


@pytest.mark.asyncio
async def test_a_non_role_update_does_not_need_step_up(
    async_client: AsyncClient, admin_user, target_user
):
    headers = await _session_headers(admin_user)

    response = await async_client.put(
        f"/api/v1/users/{target_user.id}",
        params={"email": f"renamed-{uuid.uuid4().hex[:8]}@test.local"},
        headers=headers,
    )

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_export_without_step_up_is_a_typed_403(async_client: AsyncClient, admin_user):
    headers = await _session_headers(admin_user)

    response = await async_client.post("/api/v1/export", json={"scope": "system"}, headers=headers)

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == {
        "code": "step_up_required",
        "action": "export",
        "expires_in": step_up.STEP_UP_TTL_SECONDS,
        "methods": ["password"],
    }


@pytest.mark.asyncio
async def test_break_glass_request_without_step_up_is_a_typed_403(
    async_client: AsyncClient, admin_user
):
    headers = await _session_headers(admin_user)

    response = await async_client.post(
        "/api/v1/admin/access-elevations",
        json={
            "scope": "tenant",
            "permissions": ["patient:read"],
            "duration_minutes": 30,
            "reason": "incident",
        },
        headers=headers,
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["action"] == "break_glass"


@pytest.mark.asyncio
async def test_admin_grant_without_step_up_is_a_typed_403(async_client: AsyncClient, admin_user):
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Patient(
                id=patient_id,
                tenant_id=admin_user.tenant_id,
                name={"family": "Step", "given": ["Up"]},
                gender="UNKNOWN",
            )
        )
        await session.commit()
    grantee = await create_user(role=Role.MANAGER, tenant_id=admin_user.tenant_id)
    headers = await _session_headers(admin_user)

    response = await async_client.post(
        f"/api/v1/patients/{patient_id}/access",
        json={"permission": "read", "grantee_user_id": str(grantee.id)},
        headers=headers,
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["action"] == "grant_admin"


# --- minting ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_step_up_then_role_change_succeeds(
    async_client: AsyncClient, admin_user, target_user
):
    headers = await _session_headers(admin_user)

    proof = await _step_up(async_client, headers, "role_change", password=ACTION_PASSWORD)

    assert proof.status_code == 200, proof.text
    body = proof.json()
    assert body["action"] == "role_change"
    assert body["methods"] == ["password"]
    # ``expires_in`` is recomputed as ``expires_at - now``, so a sub-second
    # boundary crossed between mint and serialization answers 599.
    assert body["expires_in"] in (
        step_up.STEP_UP_TTL_SECONDS - 1,
        step_up.STEP_UP_TTL_SECONDS,
    )

    changed = await async_client.put(
        f"/api/v1/users/{target_user.id}", params={"role": "MANAGER"}, headers=headers
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["role"] == "MANAGER"
    assert "auth.step_up" in await _audit_actions(admin_user.id)


@pytest.mark.asyncio
async def test_wrong_password_is_401_and_audited(async_client: AsyncClient, admin_user):
    headers = await _session_headers(admin_user)

    response = await _step_up(async_client, headers, "export", password="not-the-password")

    assert response.status_code == 401
    assert "auth.step_up" in await _audit_actions(admin_user.id)


@pytest.mark.asyncio
async def test_missing_factor_answers_required_with_the_methods(
    async_client: AsyncClient, admin_user
):
    headers = await _session_headers(admin_user)

    response = await _step_up(async_client, headers, "export")

    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "step_up_required"
    assert detail["methods"] == ["password"]


@pytest.mark.asyncio
async def test_unknown_action_is_422(async_client: AsyncClient, admin_user):
    headers = await _session_headers(admin_user)

    response = await _step_up(async_client, headers, "delete_everything", password=ACTION_PASSWORD)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_totp_account_must_supply_its_code(async_client: AsyncClient):
    user = await create_user(role=Role.ADMIN, password=ACTION_PASSWORD)
    enrollment = await mfa_service.begin_enrollment(user)
    assert await mfa_service.confirm_enrollment(
        user.id, totp_code_at(enrollment["secret"], time.time())
    )
    headers = await _session_headers(user)

    password_only = await _step_up(async_client, headers, "export", password=ACTION_PASSWORD)
    assert password_only.status_code == 403
    assert password_only.json()["detail"]["methods"] == ["password", "totp"]

    both = await _step_up(
        async_client,
        headers,
        "export",
        password=ACTION_PASSWORD,
        code=totp_code_at(enrollment["secret"], time.time()),
    )
    assert both.status_code == 200, both.text
    assert both.json()["methods"] == ["password", "totp"]


# --- binding: action, family, window ----------------------------------------


@pytest.mark.asyncio
async def test_a_proof_for_one_action_never_covers_another(
    async_client: AsyncClient, admin_user, target_user
):
    headers = await _session_headers(admin_user)
    minted = await _step_up(async_client, headers, "role_change", password=ACTION_PASSWORD)
    assert minted.status_code == 200, minted.text

    export = await async_client.post("/api/v1/export", json={"scope": "system"}, headers=headers)

    assert export.status_code == 403
    assert export.json()["detail"]["action"] == "export"


@pytest.mark.asyncio
async def test_the_proof_window_is_bounded(async_client: AsyncClient, admin_user):
    headers = await _session_headers(admin_user)
    token = bearer_token(headers)
    payload = decode_token(token)
    await token_store.register_step_up(
        str(payload["fid"]),
        "export",
        json.dumps(
            {
                "issued_at": int(time.time()) - step_up.STEP_UP_TTL_SECONDS - 1,
                "methods": ["password"],
            }
        ),
        step_up.STEP_UP_TTL_SECONDS,
    )

    expired = await async_client.post("/api/v1/export", json={"scope": "system"}, headers=headers)

    assert expired.status_code == 403
    assert expired.json()["detail"]["code"] == "step_up_required"


@pytest.mark.asyncio
async def test_the_proof_is_bound_to_the_sign_in_family(async_client: AsyncClient, admin_user):
    first = await _session_headers(admin_user)
    minted = await _step_up(async_client, first, "export", password=ACTION_PASSWORD)
    assert minted.status_code == 200, minted.text

    other_device = await _session_headers(admin_user)
    response = await async_client.post(
        "/api/v1/export", json={"scope": "system"}, headers=other_device
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "step_up_required"


@pytest.mark.asyncio
async def test_a_session_without_a_family_cannot_step_up(async_client: AsyncClient, admin_user):
    headers = await auth_headers(admin_user, step_up=False)
    assert decode_token(bearer_token(headers)).get("fid") is None

    response = await _step_up(async_client, headers, "export", password=ACTION_PASSWORD)

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "step_up_unavailable"
    assert detail["reason"] == "no_session_family"


@pytest.mark.asyncio
async def test_a_gated_action_on_a_familyless_token_names_no_methods(
    async_client: AsyncClient, admin_user
):
    """The denial always carries ``methods``: empty + the reason when the
    principal cannot step up at all (no sign-in family to bind a proof to)."""
    headers = await auth_headers(admin_user, step_up=False)

    response = await async_client.post("/api/v1/export", json={"scope": "system"}, headers=headers)

    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "step_up_required"
    assert detail["action"] == "export"
    assert detail["methods"] == []
    assert detail["reason"] == "no_session_family"


# --- federated freshness (SSO-only accounts) --------------------------------


@pytest.mark.asyncio
async def test_a_fresh_sso_session_steps_up_without_a_local_factor(
    async_client: AsyncClient,
):
    user = await create_user(role=Role.ADMIN, password=None)
    now = int(time.time())

    # The federated claims the SSO callback stamps onto the session.
    fresh = await _session_headers(
        user,
        auth_mode="oidc",
        extra_claims={"amr": ["pwd", "otp"], "auth_time": now},
    )

    response = await _step_up(async_client, fresh, "export")

    assert response.status_code == 200, response.text
    assert response.json()["methods"] == ["federated"]


@pytest.mark.asyncio
async def test_a_stale_sso_sign_in_cannot_step_up(async_client: AsyncClient):
    user = await create_user(role=Role.ADMIN, password=None)
    stale = await _session_headers(
        user,
        auth_mode="oidc",
        extra_claims={
            "amr": ["pwd", "otp"],
            "auth_time": int(time.time()) - step_up.STEP_UP_TTL_SECONDS - 60,
        },
    )

    response = await _step_up(async_client, stale, "export")

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "step_up_unavailable"
    assert detail["reason"] == "no_factor"


@pytest.mark.asyncio
async def test_sso_without_an_asserted_second_factor_cannot_step_up(
    async_client: AsyncClient,
):
    user = await create_user(role=Role.ADMIN, password=None)
    headers = await _session_headers(
        user,
        auth_mode="oidc",
        extra_claims={"amr": ["pwd"], "auth_time": int(time.time())},
    )

    response = await _step_up(async_client, headers, "export")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "step_up_unavailable"


# --- the other two doors, exercised end to end ------------------------------


@pytest.mark.asyncio
async def test_break_glass_request_after_step_up(async_client: AsyncClient, admin_user):
    headers = await _session_headers(admin_user)
    minted = await _step_up(async_client, headers, "break_glass", password=ACTION_PASSWORD)
    assert minted.status_code == 200, minted.text

    created = await async_client.post(
        "/api/v1/admin/access-elevations",
        json={
            "scope": "tenant",
            "permissions": ["patient:read"],
            "duration_minutes": 30,
            "reason": "incident",
        },
        headers=headers,
    )

    assert created.status_code == 201, created.text


@pytest.mark.asyncio
async def test_admin_grant_after_step_up(async_client: AsyncClient, admin_user):
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Patient(
                id=patient_id,
                tenant_id=admin_user.tenant_id,
                name={"family": "Step", "given": ["Up"]},
                gender="UNKNOWN",
            )
        )
        await session.commit()
    grantee = await create_user(role=Role.MANAGER, tenant_id=admin_user.tenant_id)
    headers = await _session_headers(admin_user)
    minted = await _step_up(async_client, headers, "grant_admin", password=ACTION_PASSWORD)
    assert minted.status_code == 200, minted.text

    created = await async_client.post(
        f"/api/v1/patients/{patient_id}/access",
        json={"permission": "read", "grantee_user_id": str(grantee.id)},
        headers=headers,
    )

    assert created.status_code == 201, created.text


@pytest.mark.asyncio
async def test_export_after_step_up_passes_the_gate(async_client: AsyncClient, admin_user):
    """Proof accepted: the call reaches export's own scope validation."""
    headers = await _session_headers(admin_user)
    minted = await _step_up(async_client, headers, "export", password=ACTION_PASSWORD)
    assert minted.status_code == 200, minted.text

    response = await async_client.post(
        "/api/v1/export", json={"scope": "nonsense"}, headers=headers
    )

    assert response.status_code == 422
