"""WebAuthn/passkey ceremonies (ADR 0054 Phase 6 — program 12C2).

Drives the real server ceremonies with :class:`VirtualAuthenticator`
(ES256, ``fmt="none"`` attestation, DER assertions): registration,
passwordless/passkey login, step-up, clone detection, user verification,
single-use/expired challenges, origin/RP mismatch, factor-aware MFA status
and last-factor protection. The virtual authenticator is a test double for
the browser only — every server check (attestation, assertion, RP id,
origin, UV flag, counter, Redis challenge) runs for real.
"""

from __future__ import annotations

import hashlib
import struct
import time

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.encryption import decrypt_secret
from app.core.security import decode_token
from app.models.audit_model import AuditEvent
from app.models.enums import Role
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.auth import mfa_service, passkeys
from tests._auth_helpers import auth_headers, create_user, sign_in
from tests._webauthn_helpers import VirtualAuthenticator, b64url

PASSWORD = "correct-password-123"


class FakeRedis:
    """Dict-backed token store / rate limiter surface."""

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
async def user():
    return await create_user(role=Role.ADMIN, password=PASSWORD)


@pytest_asyncio.fixture
async def headers(user):
    return await auth_headers(user, step_up=True)


async def _register(
    client: AsyncClient, headers: dict, authenticator: VirtualAuthenticator, name="Test"
) -> dict:
    options = await client.post("/api/v1/me/mfa/passkeys/options", headers=headers)
    assert options.status_code == 200, options.text
    credential = authenticator.registration_credential(options.json()["options"])
    verified = await client.post(
        "/api/v1/me/mfa/passkeys/verify",
        json={"credential": credential, "name": name},
        headers=headers,
    )
    assert verified.status_code == 200, verified.text
    return verified.json()


async def _login(
    client: AsyncClient, email: str, authenticator: VirtualAuthenticator, **assertion_kwargs
) -> dict:
    client.cookies.clear()
    options = await client.post("/api/v1/auth/passkeys/options", json={"email": email})
    assert options.status_code == 200, options.text
    assertion = authenticator.assertion(options.json()["options"], **assertion_kwargs)
    response = await client.post("/api/v1/auth/passkeys/verify", json={"credential": assertion})
    client.cookies.clear()
    return response


# --- registration -----------------------------------------------------------


@pytest.mark.asyncio
async def test_registration_stores_the_credential_and_lists_it(
    async_client: AsyncClient, headers, user
):
    authenticator = VirtualAuthenticator()
    created = await _register(async_client, headers, authenticator, name="MacBook Touch ID")

    assert created["name"] == "MacBook Touch ID"
    assert created["transports"] == ["internal"]
    assert created["disabled"] is False

    listed = await async_client.get("/api/v1/me/mfa/passkeys", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]

    async with AsyncSessionLocal() as session:
        actions = (
            await session.execute(select(AuditEvent.action).where(AuditEvent.user_id == user.id))
        ).scalars()
        assert "mfa.passkey_register" in set(actions)


@pytest.mark.asyncio
async def test_registration_requires_a_recent_step_up(async_client: AsyncClient, user):
    headers = await auth_headers(user)
    response = await async_client.post("/api/v1/me/mfa/passkeys/options", headers=headers)

    assert response.status_code == 403
    assert response.json()["detail"]["action"] == "mfa_change"
    assert response.json()["detail"]["code"] == "step_up_required"


@pytest.mark.asyncio
async def test_rp_mismatch_is_refused(async_client: AsyncClient, headers):
    authenticator = VirtualAuthenticator()
    options = await async_client.post("/api/v1/me/mfa/passkeys/options", headers=headers)
    credential = authenticator.registration_credential(
        options.json()["options"], rp_id="evil.example"
    )

    response = await async_client.post(
        "/api/v1/me/mfa/passkeys/verify",
        json={"credential": credential, "name": "bad"},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == passkeys.CODE_INVALID_RESPONSE


# --- login ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passkey_login_issues_a_session_with_amr_claims(
    async_client: AsyncClient, headers, user
):
    authenticator = VirtualAuthenticator()
    await _register(async_client, headers, authenticator)

    response = await _login(async_client, user.email, authenticator)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    claims = decode_token(body["access_token"])
    assert claims["auth_mode"] == "password"
    assert claims["amr"] == ["hwk", "user"]
    assert claims["fid"]

    me = await async_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200 and me.json()["email"] == user.email


@pytest.mark.asyncio
async def test_unknown_email_cannot_authenticate(async_client: AsyncClient, headers, user):
    authenticator = VirtualAuthenticator()
    await _register(async_client, headers, authenticator)

    options = await async_client.post(
        "/api/v1/auth/passkeys/options", json={"email": "nobody@test.local"}
    )
    assert options.status_code == 200
    assert options.json()["options"]["allowCredentials"] == []

    foreign = VirtualAuthenticator()
    assertion = foreign.assertion(options.json()["options"])
    response = await async_client.post(
        "/api/v1/auth/passkeys/verify", json={"credential": assertion}
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == passkeys.CODE_UNKNOWN_CREDENTIAL


@pytest.mark.asyncio
async def test_user_verification_is_required(async_client: AsyncClient, headers, user):
    authenticator = VirtualAuthenticator()
    await _register(async_client, headers, authenticator)

    response = await _login(async_client, user.email, authenticator, user_verified=False)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == passkeys.CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_origin_mismatch_is_refused(async_client: AsyncClient, headers, user):
    authenticator = VirtualAuthenticator()
    await _register(async_client, headers, authenticator)

    response = await _login(async_client, user.email, authenticator, origin="https://evil.example")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == passkeys.CODE_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_challenge_is_single_use(async_client: AsyncClient, headers, user):
    authenticator = VirtualAuthenticator()
    await _register(async_client, headers, authenticator)
    options = await async_client.post("/api/v1/auth/passkeys/options", json={"email": user.email})
    assertion = authenticator.assertion(options.json()["options"])

    first = await async_client.post("/api/v1/auth/passkeys/verify", json={"credential": assertion})
    assert first.status_code == 200, first.text
    replay = await async_client.post("/api/v1/auth/passkeys/verify", json={"credential": assertion})

    assert replay.status_code == 400, replay.text
    assert replay.json()["detail"]["code"] == passkeys.CODE_CHALLENGE_EXPIRED


@pytest.mark.asyncio
async def test_expired_challenge_is_refused(async_client: AsyncClient, headers, user, fake_redis):
    authenticator = VirtualAuthenticator()
    await _register(async_client, headers, authenticator)
    options = await async_client.post("/api/v1/auth/passkeys/options", json={"email": user.email})
    assertion = authenticator.assertion(options.json()["options"])
    fake_redis.store = {
        key: value for key, value in fake_redis.store.items() if not key.startswith("wauth:")
    }

    response = await async_client.post(
        "/api/v1/auth/passkeys/verify", json={"credential": assertion}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == passkeys.CODE_CHALLENGE_EXPIRED


@pytest.mark.asyncio
async def test_sign_count_regression_is_a_clone_alarm(async_client: AsyncClient, headers, user):
    authenticator = VirtualAuthenticator()
    created = await _register(async_client, headers, authenticator)
    first = await _login(async_client, user.email, authenticator)
    assert first.status_code == 200, first.text

    # Rewind the counter: a cloned authenticator reports the same/lower value.
    options = await async_client.post("/api/v1/auth/passkeys/options", json={"email": user.email})
    assertion = authenticator.assertion(options.json()["options"], sign_count=1)
    response = await async_client.post(
        "/api/v1/auth/passkeys/verify", json={"credential": assertion}
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == passkeys.CODE_CLONE_DETECTED

    listed = await async_client.get("/api/v1/me/mfa/passkeys", headers=headers)
    assert listed.json()["items"][0]["disabled"] is True
    async with AsyncSessionLocal() as session:
        actions = set(
            (await session.execute(select(AuditEvent.action).where(AuditEvent.user_id == user.id)))
            .scalars()
            .all()
        )
    assert "auth.passkey_clone_detected" in actions
    assert created["disabled"] is False


@pytest.mark.asyncio
async def test_forged_assertion_counter_cannot_disable_a_known_credential(
    async_client: AsyncClient, headers, user
):
    """B1 — the counter is attacker-controlled until the assertion verifies.

    Anyone who knows a credential id can post a bogus ``authenticatorData``
    carrying a low counter. Reading that value before verification would
    disable the passkey (a clone alarm the attacker triggers at will, for
    every account whose credential id leaks). Both verify paths must refuse
    the assertion **without** touching the credential, and a genuine
    assertion must keep working afterwards.
    """
    authenticator = VirtualAuthenticator(sign_count=3)
    created = await _register(async_client, headers, authenticator)
    assert created["disabled"] is False

    def _forged(options: dict) -> dict:
        assertion = authenticator.assertion(options)
        counters = hashlib.sha256(b"attacker.example").digest()
        assertion["response"]["authenticatorData"] = b64url(
            counters + bytes([0x01]) + struct.pack(">I", 1)
        )
        return assertion

    login_options = await async_client.post(
        "/api/v1/auth/passkeys/options", json={"email": user.email}
    )
    forged_login = await async_client.post(
        "/api/v1/auth/passkeys/verify",
        json={"credential": _forged(login_options.json()["options"])},
    )
    assert forged_login.status_code == 401, forged_login.text
    assert forged_login.json()["detail"]["code"] == passkeys.CODE_INVALID_RESPONSE

    step_options = await async_client.post(
        "/api/v1/auth/step-up/passkeys/options",
        json={"action": "mfa_change"},
        headers=headers,
    )
    assert step_options.status_code == 200, step_options.text
    forged_step = await async_client.post(
        "/api/v1/auth/step-up/passkeys/verify",
        json={
            "action": "mfa_change",
            "credential": _forged(step_options.json()["options"]),
        },
        headers=headers,
    )
    assert forged_step.status_code == 401, forged_step.text

    listed = await async_client.get("/api/v1/me/mfa/passkeys", headers=headers)
    assert listed.json()["items"][0]["disabled"] is False, (
        "an unauthenticated forged assertion disabled the credential"
    )

    # The real authenticator still signs in: the credential survived intact.
    again = await _login(async_client, user.email, authenticator)
    assert again.status_code == 200, again.text


# --- policy / factor awareness ---------------------------------------------


@pytest.mark.asyncio
async def test_passkey_only_account_satisfies_a_policy_mfa_requirement(
    async_client: AsyncClient, headers, user
):
    authenticator = VirtualAuthenticator()
    await _register(async_client, headers, authenticator)

    async with AsyncSessionLocal() as session:
        tenant = await session.get(TenantModel, user.tenant_id)
        tenant.settings = {**(tenant.settings or {}), "mfa_required_roles": ["ADMIN"]}
        await session.commit()

    login = await async_client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": PASSWORD},
    )
    assert login.status_code == 401
    challenge = login.json()
    assert challenge["detail"] == "mfa_required"
    assert challenge["enrollment_needed"] is False
    assert challenge["methods"] == ["webauthn"]

    passkey_login = await _login(async_client, user.email, authenticator)
    assert passkey_login.status_code == 200, passkey_login.text
    assert decode_token(passkey_login.json()["access_token"])["amr"] == ["hwk", "user"]


@pytest.mark.asyncio
async def test_mfa_status_is_factor_aware(async_client: AsyncClient, headers, user):
    before = await async_client.get("/api/v1/me/mfa", headers=headers)
    assert before.json()["enabled"] is False
    assert before.json()["passkey_count"] == 0

    authenticator = VirtualAuthenticator()
    await _register(async_client, headers, authenticator)

    after = await async_client.get("/api/v1/me/mfa", headers=headers)
    body = after.json()
    assert body["enabled"] is True
    assert body["totp_enabled"] is False
    assert body["passkey_enabled"] is True
    assert body["passkey_count"] == 1


@pytest.mark.asyncio
async def test_totp_disable_is_allowed_when_a_passkey_remains(
    async_client: AsyncClient, headers, user
):
    await mfa_service.begin_enrollment(user)
    from app.core.totp import totp_code_at

    async with AsyncSessionLocal() as session:
        pending = (await session.get(UserModel, user.id)).mfa_pending
    code = totp_code_at(decrypt_secret(pending["secret_enc"]), time.time())
    assert await mfa_service.confirm_enrollment(user.id, code)
    await mfa_service.set_enforced(user.id, True)
    authenticator = VirtualAuthenticator()
    await _register(async_client, headers, authenticator)

    response = await async_client.request(
        "DELETE",
        "/api/v1/me/mfa",
        json={"password": PASSWORD},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["totp_enabled"] is False
    assert response.json()["passkey_count"] == 1


# --- step-up ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_passkey_assertion_mints_a_step_up_proof(async_client: AsyncClient, user):
    issued = await sign_in(user)
    headers = {"Authorization": f"Bearer {issued.access_token}"}
    registration_headers = await auth_headers(user, step_up=True)
    authenticator = VirtualAuthenticator()
    await _register(async_client, registration_headers, authenticator)

    options = await async_client.post(
        "/api/v1/auth/step-up/passkeys/options",
        json={"action": "export"},
        headers=headers,
    )
    assert options.status_code == 200, options.text
    assertion = authenticator.assertion(options.json()["options"])
    verified = await async_client.post(
        "/api/v1/auth/step-up/passkeys/verify",
        json={"action": "export", "credential": assertion},
        headers=headers,
    )

    assert verified.status_code == 200, verified.text
    assert verified.json()["action"] == "export"
    assert verified.json()["methods"] == ["webauthn"]

    export = await async_client.post("/api/v1/export", json={"scope": "system"}, headers=headers)
    assert export.status_code == 200, export.text


# --- lifecycle --------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_requires_step_up_and_removes_the_credential(
    async_client: AsyncClient, headers, user
):
    authenticator = VirtualAuthenticator()
    created = await _register(async_client, headers, authenticator)

    ungated = await async_client.delete(
        f"/api/v1/me/mfa/passkeys/{created['id']}", headers=await auth_headers(user)
    )
    assert ungated.status_code == 403
    assert ungated.json()["detail"]["action"] == "mfa_change"

    deleted = await async_client.delete(f"/api/v1/me/mfa/passkeys/{created['id']}", headers=headers)
    assert deleted.status_code == 204
    listed = await async_client.get("/api/v1/me/mfa/passkeys", headers=headers)
    assert listed.json()["items"] == []


@pytest.mark.asyncio
async def test_last_factor_cannot_be_deleted_under_an_mfa_requirement(
    async_client: AsyncClient, headers, user
):
    authenticator = VirtualAuthenticator()
    created = await _register(async_client, headers, authenticator)
    await mfa_service.set_enforced(user.id, True)

    response = await async_client.delete(
        f"/api/v1/me/mfa/passkeys/{created['id']}", headers=headers
    )

    assert response.status_code == 403
    listed = await async_client.get("/api/v1/me/mfa/passkeys", headers=headers)
    assert len(listed.json()["items"]) == 1


@pytest.mark.asyncio
async def test_deleting_one_of_two_passkeys_is_allowed(async_client: AsyncClient, headers, user):
    first = VirtualAuthenticator()
    second = VirtualAuthenticator()
    one = await _register(async_client, headers, first, name="one")
    await _register(async_client, headers, second, name="two")
    await mfa_service.set_enforced(user.id, True)

    response = await async_client.delete(f"/api/v1/me/mfa/passkeys/{one['id']}", headers=headers)

    assert response.status_code == 204, response.text
    listed = await async_client.get("/api/v1/me/mfa/passkeys", headers=headers)
    assert [item["name"] for item in listed.json()["items"]] == ["two"]
