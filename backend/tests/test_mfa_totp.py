"""TOTP MFA (plan 16 H5) — RFC vectors, login challenge flow, recovery
codes, forced enrollment, admin force + audit, encryption at rest.

The lockout interplay decision under test: **failed MFA verification
counts toward the §7 lockout** (the same ``failed_login_attempts``
counter as wrong passwords — 5 strikes ⇒ 423 for 15 minutes); the
counter resets when the password succeeds (challenge issued) and again
when the challenge passes.
"""

import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from unittest.mock import patch

from app.core.database import AsyncSessionLocal
from app.core.encryption import decrypt_secret, encrypt_secret, is_encrypted
from app.core.security import (
    create_mfa_challenge_token,
    verify_mfa_challenge_token,
)
from app.core.totp import (
    build_totp_uri,
    canonical_recovery_code,
    generate_recovery_codes,
    generate_totp_secret,
    totp_code_at,
    verify_totp,
)
from app.models.audit_model import AuditEvent
from app.models.enums import Role
from app.models.user_model import UserModel
from app.services import mfa_service
from tests._auth_helpers import auth_headers, create_tenant, create_user

# ---------------------------------------------------------------------------
# Pure RFC 6238 vectors (RFC Appendix B, SHA1, 6-digit projection)
# ---------------------------------------------------------------------------

RFC_SECRET_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ascii "12345678901234567890"

RFC_VECTORS = [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
    (20000000000, "353130"),
]


@pytest.mark.parametrize("timestamp,expected", RFC_VECTORS)
def test_rfc6238_vectors(timestamp, expected):
    assert totp_code_at(RFC_SECRET_B32, timestamp) == expected
    assert verify_totp(RFC_SECRET_B32, expected, at=float(timestamp))


def test_rfc6238_drift_window():
    """±1 step (30s) of clock skew is accepted; ±2 steps is not."""
    t = 1111111109
    assert verify_totp(
        RFC_SECRET_B32, totp_code_at(RFC_SECRET_B32, t - 30), at=float(t)
    )
    assert verify_totp(
        RFC_SECRET_B32, totp_code_at(RFC_SECRET_B32, t + 30), at=float(t)
    )
    assert not verify_totp(
        RFC_SECRET_B32, totp_code_at(RFC_SECRET_B32, t - 60), at=float(t)
    )


def test_totp_rejects_malformed_codes():
    t = 1111111109
    assert not verify_totp(RFC_SECRET_B32, "", at=float(t))
    assert not verify_totp(RFC_SECRET_B32, "12345", at=float(t))  # 5 digits
    assert not verify_totp(RFC_SECRET_B32, "1234567", at=float(t))  # 7 digits
    assert not verify_totp(RFC_SECRET_B32, "abcdef", at=float(t))
    # Human typing shapes are normalized (spaces).
    assert verify_totp(RFC_SECRET_B32, " 287 082 ", at=59.0)


def test_totp_secret_shape():
    secret = generate_totp_secret()
    assert len(secret) == 32  # 20 bytes → 32 base32 chars, padding stripped
    assert "=" not in secret
    assert secret != generate_totp_secret()


def test_otpauth_uri():
    uri = build_totp_uri("ABC234", "alice@example.com", "Health Assistant")
    assert uri.startswith("otpauth://totp/Health%20Assistant:alice%40example.com?")
    assert "secret=ABC234" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri


def test_recovery_code_helpers():
    codes = generate_recovery_codes()
    assert len(codes) == 8
    assert len(set(codes)) == 8
    for code in codes:
        assert len(code) == 9 and code[4] == "-"
    assert canonical_recovery_code(codes[0]) == codes[0]
    assert canonical_recovery_code(codes[0].lower()) == codes[0]
    assert canonical_recovery_code(codes[0].replace("-", "")) == codes[0]
    assert canonical_recovery_code(f" {codes[0]} ") == codes[0]


# ---------------------------------------------------------------------------
# Helpers — fast fake password hashing for recovery codes (bcrypt cost is
# for at-rest storage, not the flow under test), real TOTP everywhere.
# ---------------------------------------------------------------------------


def _fast_hash(pw: str) -> str:
    return "fast$" + hashlib.sha256(pw.encode()).hexdigest()


def _fast_verify(pw: str, hashed: str) -> bool:
    return hmac.compare_digest(_fast_hash(pw), str(hashed))


@pytest.fixture
def fast_recovery_hashing():
    with (
        patch.object(mfa_service, "get_password_hash", _fast_hash),
        patch.object(mfa_service, "verify_password", _fast_verify),
    ):
        yield


async def _enroll_and_confirm(user: UserModel, *, code: str | None = None) -> dict:
    """Drive the service-level enrollment (as the UI would) to active."""
    data = await mfa_service.begin_enrollment(user)
    code = code or totp_code_at(data["secret"], time.time())
    assert await mfa_service.confirm_enrollment(user.id, code)
    return data


async def _login(async_client: AsyncClient, user: UserModel, password: str):
    return await async_client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": password},
    )


@pytest_asyncio.fixture
async def mfa_user():
    return await create_user(password="correct-password-123")


@pytest_asyncio.fixture
async def no_rate_limit():
    """Disable the Redis fixed-window limiter for flow density (the §7
    lockout under test is the DB counter, not the 429 path)."""
    from app.core import rate_limit as rl

    async def _allow(*args, **kwargs):
        return None

    with patch.object(rl, "_consume", _allow):
        yield


# ---------------------------------------------------------------------------
# Login challenge flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_returns_mfa_challenge(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    await _enroll_and_confirm(mfa_user)
    resp = await _login(async_client, mfa_user, "correct-password-123")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"] == "mfa_required"
    assert body["enrollment_needed"] is False
    assert body["mfa_token"]
    challenge = verify_mfa_challenge_token(body["mfa_token"])
    assert challenge is not None
    assert challenge["token_kind"] == "mfa_challenge"
    assert str(challenge["sub"]) == str(mfa_user.id)


@pytest.mark.asyncio
async def test_password_still_required_before_challenge(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    await _enroll_and_confirm(mfa_user)
    resp = await _login(async_client, mfa_user, "wrong-password")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_mfa_verify_issues_session_and_cookies(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    data = await _enroll_and_confirm(mfa_user)
    resp = await _login(async_client, mfa_user, "correct-password-123")
    mfa_token = resp.json()["mfa_token"]

    ok = await async_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "mfa_token": mfa_token,
            "code": totp_code_at(data["secret"], time.time()),
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["access_token"] and body["refresh_token"]
    # §10 cookie triple (H3) — the browser rides these.
    cookie_header = ok.headers.get("set-cookie", "")
    assert "nx_access=" in cookie_header
    assert "nx_refresh=" in cookie_header
    assert "nx_csrf=" in cookie_header


@pytest.mark.asyncio
async def test_challenge_token_single_use(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    data = await _enroll_and_confirm(mfa_user)
    resp = await _login(async_client, mfa_user, "correct-password-123")
    mfa_token = resp.json()["mfa_token"]
    code = totp_code_at(data["secret"], time.time())

    first = await async_client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": code}
    )
    assert first.status_code == 200

    # Replay of the same challenge cannot mint a second session.
    replay = await async_client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": code}
    )
    assert replay.status_code == 401
    assert replay.json()["detail"] == "Invalid or expired MFA challenge"


@pytest.mark.asyncio
async def test_challenge_token_expiry(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    await _enroll_and_confirm(mfa_user)
    token, jti = create_mfa_challenge_token(
        str(mfa_user.id), expires_delta=timedelta(seconds=-10)
    )
    resp = await async_client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": token, "code": "123456"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_challenge_token_is_not_a_session_token(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    """Kinds are mutually exclusive (§8): an mfa_challenge JWT must not
    authenticate the domain API as a session token."""
    await _enroll_and_confirm(mfa_user)
    token, _ = create_mfa_challenge_token(str(mfa_user.id))
    resp = await async_client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.contract  # §18.4 — lockout: N strikes ⇒ 423 (shared counter)
async def test_wrong_code_401_then_lockout_423(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    from app.core.config import settings

    data = await _enroll_and_confirm(mfa_user)
    resp = await _login(async_client, mfa_user, "correct-password-123")
    mfa_token = resp.json()["mfa_token"]

    good_code = totp_code_at(data["secret"], time.time())
    for _ in range(settings.HA_AUTH_LOCKOUT_THRESHOLD - 1):
        bad = await async_client.post(
            "/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": "000000"}
        )
        assert bad.status_code == 401
        assert bad.json()["detail"] == "Invalid verification code"

    # 5th wrong code crosses the §7 threshold → the account locks.
    locked = await async_client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": "000000"}
    )
    assert locked.status_code == 423

    # Even the correct code is refused while locked (challenge alive but
    # the account is locked — and a fresh password login gets 423 too).
    refused = await async_client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": good_code}
    )
    assert refused.status_code == 423
    relogin = await _login(async_client, mfa_user, "correct-password-123")
    assert relogin.status_code == 423


@pytest.mark.asyncio
async def test_wrong_code_counter_resets_on_challenge(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    """The counter resets when the password succeeds (challenge issued),
    so MFA failures count from zero — documented H5 lockout decision."""
    from app.services.user_service import get_user_by_id

    await _enroll_and_confirm(mfa_user)
    # Burn one wrong password attempt first.
    await _login(async_client, mfa_user, "wrong-password")
    user = await get_user_by_id(mfa_user.id)
    assert user.failed_login_attempts == 1

    resp = await _login(async_client, mfa_user, "correct-password-123")
    assert resp.status_code == 401 and resp.json()["detail"] == "mfa_required"
    user = await get_user_by_id(mfa_user.id)
    assert user.failed_login_attempts == 0


@pytest.mark.asyncio
@pytest.mark.contract  # §18.4 — lockout resets after success
async def test_lockout_resets_after_successful_verify(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    from app.services.user_service import get_user_by_id

    data = await _enroll_and_confirm(mfa_user)
    resp = await _login(async_client, mfa_user, "correct-password-123")
    mfa_token = resp.json()["mfa_token"]
    await async_client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": "000000"}
    )
    user = await get_user_by_id(mfa_user.id)
    assert user.failed_login_attempts == 1

    ok = await async_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "mfa_token": mfa_token,
            "code": totp_code_at(data["secret"], time.time()),
        },
    )
    assert ok.status_code == 200
    user = await get_user_by_id(mfa_user.id)
    assert user.failed_login_attempts == 0


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_code_login_and_single_use(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    data = await _enroll_and_confirm(mfa_user)

    resp = await _login(async_client, mfa_user, "correct-password-123")
    token_one = resp.json()["mfa_token"]
    ok = await async_client.post(
        "/api/v1/auth/mfa/verify",
        json={"mfa_token": token_one, "code": data["recovery_codes"][0]},
    )
    assert ok.status_code == 200

    # Same recovery code a second time: refused (single-use).
    resp = await _login(async_client, mfa_user, "correct-password-123")
    token_two = resp.json()["mfa_token"]
    spent = await async_client.post(
        "/api/v1/auth/mfa/verify",
        json={"mfa_token": token_two, "code": data["recovery_codes"][0]},
    )
    assert spent.status_code == 401

    # A different recovery code still works (7 remain).
    resp = await _login(async_client, mfa_user, "correct-password-123")
    token_three = resp.json()["mfa_token"]
    ok2 = await async_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "mfa_token": token_three,
            "code": data["recovery_codes"][1].lower().replace("-", ""),
        },
    )
    assert ok2.status_code == 200

    # Exactly 6 hashes remain on the row.
    from app.services.user_service import get_user_by_id

    user = await get_user_by_id(mfa_user.id)
    assert len(json.loads(user.mfa_recovery_codes)) == 6


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_encrypted_at_rest(mfa_user):
    """Real bcrypt for recovery hashes + Fernet for the secret — the raw
    row must never carry plaintext."""
    data = await _enroll_and_confirm(mfa_user)
    from app.services.user_service import get_user_by_id

    user = await get_user_by_id(mfa_user.id)
    assert is_encrypted(user.mfa_secret_enc)
    assert user.mfa_secret_enc.startswith("enc::")
    assert data["secret"] not in user.mfa_secret_enc
    assert decrypt_secret(user.mfa_secret_enc) == data["secret"]

    hashes = json.loads(user.mfa_recovery_codes)
    assert len(hashes) == 8
    for hashed in hashes:
        assert hashed.startswith("$2")  # bcrypt, like passwords
    for code in data["recovery_codes"]:
        assert code not in user.mfa_recovery_codes

    # The pending blob is encrypted + hash-only too.
    other = await create_user()
    pending = await mfa_service.begin_enrollment(other)
    fresh = await mfa_service.get_mfa_user(other.id)
    assert fresh.mfa_pending["secret_enc"].startswith("enc::")
    assert pending["secret"] not in json.dumps(fresh.mfa_pending)
    assert all(h.startswith("$2") for h in fresh.mfa_pending["recovery"])


def test_encrypt_decrypt_roundtrip():
    secret = generate_totp_secret()
    stored = encrypt_secret(secret)
    assert is_encrypted(stored) and decrypt_secret(stored) == secret


# ---------------------------------------------------------------------------
# Self-service /me/mfa surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_mfa_lifecycle(
    async_client: AsyncClient, fast_recovery_hashing, no_rate_limit
):
    user = await create_user(password="my-password-123")
    headers = await auth_headers(user)

    status = await async_client.get("/api/v1/me/mfa", headers=headers)
    assert status.status_code == 200
    assert status.json() == {"enabled": False, "enforced": False, "pending": False}

    enroll = await async_client.post("/api/v1/me/mfa/enroll", headers=headers)
    assert enroll.status_code == 200, enroll.text
    data = enroll.json()
    assert data["secret"] and data["uri"].startswith("otpauth://totp/")
    assert len(data["recovery_codes"]) == 8

    # Pending until confirmed; wrong confirm code is a 400.
    status = await async_client.get("/api/v1/me/mfa", headers=headers)
    assert status.json()["pending"] is True and status.json()["enabled"] is False

    bad = await async_client.post(
        "/api/v1/me/mfa/confirm", json={"code": "000000"}, headers=headers
    )
    assert bad.status_code == 400

    good = await async_client.post(
        "/api/v1/me/mfa/confirm",
        json={"code": totp_code_at(data["secret"], time.time())},
        headers=headers,
    )
    assert good.status_code == 200
    assert good.json() == {"enabled": True, "enforced": False, "pending": False}

    # Already active → re-enroll is a 409.
    again = await async_client.post("/api/v1/me/mfa/enroll", headers=headers)
    assert again.status_code == 409

    # Disable requires the password.
    wrong_pw = await async_client.request(
        "DELETE",
        "/api/v1/me/mfa",
        json={"password": "not-my-password"},
        headers=headers,
    )
    assert wrong_pw.status_code == 401
    ok = await async_client.request(
        "DELETE",
        "/api/v1/me/mfa",
        json={"password": "my-password-123"},
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json()["enabled"] is False


@pytest.mark.asyncio
async def test_me_mfa_requires_session(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/me/mfa")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_disable_refused_while_enforced(
    async_client: AsyncClient, fast_recovery_hashing, no_rate_limit
):
    user = await create_user(password="my-password-123")
    await _enroll_and_confirm(user)
    await mfa_service.set_enforced(user.id, True)
    headers = await auth_headers(user)

    resp = await async_client.request(
        "DELETE",
        "/api/v1/me/mfa",
        json={"password": "my-password-123"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "required by policy" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Admin force + audit + forced-enrollment login path
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def admin_and_target():
    tenant_id = await create_tenant()
    sysadmin = await create_user(role=Role.SYSTEM_ADMIN, tenant_id=tenant_id)
    target = await create_user(tenant_id=tenant_id)
    return tenant_id, sysadmin, target


async def _force(async_client, headers, tenant_id, user_id, enforced=True):
    return await async_client.patch(
        f"/api/v1/admin/tenants/{tenant_id}/users/{user_id}/mfa",
        json={"enforced": enforced},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_admin_force_mfa_audited(
    async_client: AsyncClient, admin_and_target, no_rate_limit
):
    tenant_id, sysadmin, target = admin_and_target
    headers = await auth_headers(sysadmin)

    ok = await _force(async_client, headers, tenant_id, target.id, True)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["mfa_enforced"] is True and body["mfa_enabled"] is False

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        rows = (
            (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.action == "user.mfa_enforce")
                )
            )
            .scalars()
            .all()
        )
    mine = [r for r in rows if str(r.resource_id) == str(target.id)]
    assert mine, "admin force must land in audit_events"
    assert mine[-1].outcome == "ok"
    assert mine[-1].old_value == {"mfa_enforced": False}
    assert mine[-1].new_value["mfa_enforced"] is True

    # Releasing the requirement is audited too and never drops a secret.
    await _enroll_and_confirm(target)
    off = await _force(async_client, headers, tenant_id, target.id, False)
    assert off.status_code == 200
    assert off.json()["mfa_enforced"] is False
    assert off.json()["mfa_enabled"] is True


@pytest.mark.asyncio
async def test_admin_force_scoping(
    async_client: AsyncClient, admin_and_target, no_rate_limit
):
    tenant_id, _, target = admin_and_target

    # Tenant ADMIN may enforce inside their own tenant.
    tenant_admin = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    ok = await _force(
        async_client, await auth_headers(tenant_admin), tenant_id, target.id, True
    )
    assert ok.status_code == 200

    # MANAGER / USER may not.
    for role in (Role.MANAGER, Role.USER):
        member = await create_user(role=role, tenant_id=tenant_id)
        denied = await _force(
            async_client, await auth_headers(member), tenant_id, target.id, False
        )
        assert denied.status_code == 403

    # ADMIN of a different tenant is refused (cross-tenant).
    other_admin = await create_user(role=Role.ADMIN)
    denied = await _force(
        async_client, await auth_headers(other_admin), tenant_id, target.id, False
    )
    assert denied.status_code == 403

    # Unknown user in tenant → 404.
    stranger = await create_user()
    missing = await _force(
        async_client, await auth_headers(tenant_admin), tenant_id, stranger.id, True
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_forced_enrollment_login_path(
    async_client: AsyncClient, admin_and_target, fast_recovery_hashing, no_rate_limit
):
    """Admin forces ⇒ next login requires enrollment before the challenge
    passes: challenge carries enrollment_needed, /auth/mfa/enroll provisions
    against the challenge token, verify confirms + signs in."""
    tenant_id, sysadmin, target = admin_and_target
    target = await create_user(tenant_id=tenant_id, password="member-password-123")
    await mfa_service.set_enforced(target.id, True)

    resp = await _login(async_client, target, "member-password-123")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"] == "mfa_required"
    assert body["enrollment_needed"] is True
    mfa_token = body["mfa_token"]

    # Before enrolling, no code passes the challenge.
    early = await async_client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": "123456"}
    )
    assert early.status_code == 401

    provision = await async_client.post(
        "/api/v1/auth/mfa/enroll", json={"mfa_token": mfa_token}
    )
    assert provision.status_code == 200, provision.text
    data = provision.json()
    assert (
        data["secret"]
        and data["uri"].startswith("otpauth://")
        and data["recovery_codes"]
    )

    ok = await async_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "mfa_token": mfa_token,
            "code": totp_code_at(data["secret"], time.time()),
        },
    )
    assert ok.status_code == 200
    assert ok.json()["access_token"]

    # MFA is now active; the next login is a normal challenge.
    fresh = await mfa_service.get_mfa_user(target.id)
    assert fresh.mfa_secret_enc and fresh.mfa_pending is None
    resp2 = await _login(async_client, target, "member-password-123")
    assert resp2.json()["enrollment_needed"] is False


@pytest.mark.asyncio
async def test_forced_enroll_rejected_when_not_required(
    async_client: AsyncClient, mfa_user, fast_recovery_hashing, no_rate_limit
):
    """The challenge-gated enroll endpoint exists only for forced
    enrollment — a plain account gets a 400."""
    resp = await _login(async_client, mfa_user, "correct-password-123")
    assert resp.status_code == 200  # no MFA, no challenge
    from app.core import token_store

    token, jti = create_mfa_challenge_token(str(mfa_user.id))
    await token_store.register_mfa_challenge(str(mfa_user.id), jti, 300)
    out = await async_client.post("/api/v1/auth/mfa/enroll", json={"mfa_token": token})
    assert out.status_code == 400


# ---------------------------------------------------------------------------
# §9 Bearer clients / bridge untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mfa_does_not_affect_existing_sessions(
    async_client: AsyncClient, fast_recovery_hashing, no_rate_limit
):
    """MFA gates LOGIN only: a session minted before enrollment (or
    before an admin force) keeps working — Bearer §9 clients are
    unaffected by design."""
    user = await create_user()
    headers = await auth_headers(user)
    before = await async_client.get("/api/v1/users/me", headers=headers)
    assert before.status_code == 200

    await _enroll_and_confirm(user)
    await mfa_service.set_enforced(user.id, True)

    after = await async_client.get("/api/v1/users/me", headers=headers)
    assert after.status_code == 200

    status = await async_client.get("/api/v1/me/mfa", headers=headers)
    assert status.json() == {"enabled": True, "enforced": True, "pending": False}


@pytest.mark.asyncio
async def test_api_tokens_unaffected_by_mfa(async_client: AsyncClient, no_rate_limit):
    """The OAuth client-credentials surface (FHIR facade) never sees MFA
    — api tokens are client principals, not users. A client token keeps
    reading the facade while every user login on the instance demands a
    TOTP code."""
    from tests._facade_auth import facade_api_headers

    tenant_id = await create_tenant()
    headers = await facade_api_headers(tenant_id, scopes=["system/*.read"])
    resp = await async_client.get("/api/v1/fhir/R4/metadata", headers=headers)
    assert resp.status_code == 200
