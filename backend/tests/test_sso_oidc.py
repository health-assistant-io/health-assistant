"""Per-tenant OIDC SSO — the mock-IdP suite (ADR 0054 D2, Phase 12B).

The IdP is mocked at the service seam (``sso._fetch_json`` discovery/JWKS,
``sso._exchange_code``) with a real RSA keypair signing real ID tokens —
the RS256/JWKS verification path runs for real. Covers the D2 law:
``email_verified`` required, link-only via ``(issuer, subject)``, typed 409
+ authenticated link flow on an existing local email, JIT capped by the
allowed-domain list + the role map (never SYSTEM_ADMIN), group→role
changes on login (audited), replayed state fail-closed, and the IdP-down
fallback to the untouched local password path.
"""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt as pyjwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core import tenant_context, token_store
from app.core.database import AsyncSessionLocal
from app.core.security import decode_token, verify_mfa_challenge_token
from app.models.enums import Role
from app.models.tenant_sso_config import TenantSSOConfig
from app.models.user_identity import UserIdentity
from app.models.user_model import UserModel
from tests._auth_helpers import auth_headers, create_tenant, create_user

ISSUER = "https://idp.example.com"
CLIENT_ID = "health-assistant"
CLIENT_SECRET = "mock-client-secret-value"
SLOT = "test/slot"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
_KID = "test-key-1"

_DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "jwks_uri": f"{ISSUER}/jwks",
}


def _jwks_document() -> dict:
    jwk = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(_PRIVATE_KEY.public_key()))
    jwk.update({"kid": _KID, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


_JWKS = _jwks_document()


def _mint_id_token(
    *,
    subject: str,
    email: str,
    email_verified: bool = True,
    nonce: str = "",
    groups: list[str] | None = None,
    amr: list[str] | None = None,
    acr: str | None = None,
    auth_time: int | None = None,
    issuer: str = ISSUER,
    audience: str = CLIENT_ID,
    sign_key=None,
) -> str:
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": audience,
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        "nonce": nonce,
        "email": email,
        "email_verified": email_verified,
    }
    if groups is not None:
        claims["groups"] = groups
    if amr is not None:
        claims["amr"] = amr
    if acr is not None:
        claims["acr"] = acr
    if auth_time is not None:
        claims["auth_time"] = auth_time
    key = _PRIVATE_PEM if sign_key is None else sign_key
    return pyjwt.encode(claims, key, algorithm="RS256", headers={"kid": _KID})


@pytest_asyncio.fixture
async def sso_tenant():
    return await create_tenant(name="SSO Tenant")


@pytest_asyncio.fixture
async def sso_config(sso_tenant, _clean_identity_tables):
    with tenant_context.system_scope("tests:sso-config"):
        async with AsyncSessionLocal() as session:
            config = TenantSSOConfig(
                tenant_id=sso_tenant,
                issuer=ISSUER,
                client_id=CLIENT_ID,
                client_secret_ref=SLOT,
                allowed_domains=["example.com", "staff.example.com"],
                default_role="USER",
                group_role_map={"staff": "MANAGER", "admins": "ADMIN"},
                enabled=True,
                jit_enabled=True,
                scopes=["openid", "email", "profile"],
                created_by=None,
            )
            session.add(config)
            await session.commit()
            return config.id


@pytest_asyncio.fixture(autouse=True)
async def _clean_identity_tables():
    """The identity tables must not leak between tests (rows persist across
    the session's DB) — an SSO lookup in test B must never see test A's
    tenant config."""
    from sqlalchemy import text as sql_text

    async with AsyncSessionLocal() as session:
        for table in ("tenant_sso_configs", "user_identities", "scim_tokens"):
            await session.execute(sql_text(f"DELETE FROM {table}"))
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        for table in ("tenant_sso_configs", "user_identities", "scim_tokens"):
            await session.execute(sql_text(f"DELETE FROM {table}"))
        await session.commit()


@pytest.fixture(autouse=True)
def _sso_env(monkeypatch):
    """The secret-slot env fallback (no keyring in tests)."""
    monkeypatch.setenv("HA_SSO_CLIENT_SECRET_TEST_SLOT", CLIENT_SECRET)


class FakeRedis:
    """The per-test Redis fake (the sanctioned pattern — no real Redis in
    tests, and no cross-loop pool bleed): dict storage over the exact
    command surface ``token_store`` / ``rate_limit`` use."""

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
        hashes = self.hashes.setdefault(key, {})
        hashes.update({k: str(v) for k, v in (mapping or {}).items()})
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
    monkeypatch.setattr("app.core.token_store.redis_client", fake)
    monkeypatch.setattr("app.core.rate_limit.redis_client", fake)
    return fake


@pytest.fixture(autouse=True)
def _mock_idp(monkeypatch):
    from app.services.auth import sso

    async def fake_fetch(url: str) -> dict:
        if url.endswith("/.well-known/openid-configuration"):
            return dict(_DISCOVERY)
        if url.endswith("/jwks"):
            return json.loads(json.dumps(_JWKS))
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(sso, "_fetch_json", fake_fetch)
    sso.clear_provider_cache()
    yield


def _extract_state(authorization_url: str) -> str:
    return parse_qs(urlsplit(authorization_url).query)["state"][0]


async def _begin(client: AsyncClient, email: str = "new@example.com") -> str:
    response = await client.post("/api/v1/auth/sso/authorize", json={"email": email})
    assert response.status_code == 200, response.text
    return _extract_state(response.json()["authorization_url"])


async def _flow(
    client: AsyncClient,
    monkeypatch,
    *,
    email: str,
    begin_email: str | None = None,
    nonce_override=None,
    **token_kwargs,
):
    """Begin → mint → exchange, in one step. Returns the callback response."""
    state = await _begin(client, begin_email or email)
    flow = json.loads(await token_store.redis_client.get(f"sso_state:{state}"))
    nonce = nonce_override if nonce_override is not None else flow["nonce"]
    token = _mint_id_token(email=email, nonce=nonce, **token_kwargs)

    async def fake_exchange(endpoint, **kwargs):
        return {"id_token": token}

    monkeypatch.setattr("app.services.auth.sso._exchange_code", fake_exchange)
    return await client.get(
        "/api/v1/auth/sso/callback", params={"code": "auth-code-1", "state": state}
    )


async def _linked_user(subject: str) -> tuple[UserIdentity | None, UserModel | None]:
    with tenant_context.system_scope("tests:sso-assert"):
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserIdentity).where(UserIdentity.subject == subject)
            )
            identity = result.scalar_one_or_none()
            if identity is None:
                return None, None
            user = await session.get(UserModel, identity.user_id)
            session.expunge(identity)
            session.expunge(user)
            return identity, user


async def _audit_actions(tenant_id) -> set[str]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT DISTINCT action FROM audit_events WHERE tenant_id = :t"),
            {"t": str(tenant_id)},
        )
        return {row[0] for row in rows}


# --- discovery / begin -------------------------------------------------------


@pytest.mark.asyncio
async def test_status_reports_the_domain(async_client: AsyncClient, sso_config):
    yes = await async_client.post("/api/v1/auth/sso/status", json={"email": "a@Example.com"})
    assert yes.status_code == 200
    assert yes.json() == {"sso_available": True}
    no = await async_client.post("/api/v1/auth/sso/status", json={"email": "a@unknown.org"})
    assert no.status_code == 200
    assert no.json() == {"sso_available": False}


@pytest.mark.asyncio
async def test_authorize_binds_pkce_state_and_nonce(async_client: AsyncClient, sso_config):
    response = await async_client.post(
        "/api/v1/auth/sso/authorize", json={"email": "someone@staff.example.com"}
    )
    assert response.status_code == 200
    url = response.json()["authorization_url"]
    assert url.startswith(f"{ISSUER}/authorize?")
    params = parse_qs(urlsplit(url).query)
    assert params["client_id"] == [CLIENT_ID]
    assert params["code_challenge_method"] == ["S256"]
    assert params["nonce"] and params["state"]
    flow = json.loads(await token_store.redis_client.get(f"sso_state:{params['state'][0]}"))
    assert flow["nonce"] == params["nonce"][0]
    assert flow["verifier"]
    assert "code_verifier" not in params


@pytest.mark.asyncio
async def test_authorize_unknown_domain_is_typed_404(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/auth/sso/authorize", json={"email": "a@nowhere.org"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "sso_unavailable"


@pytest.mark.asyncio
async def test_disabled_config_reads_as_absent(async_client: AsyncClient, sso_tenant, sso_config):
    with tenant_context.system_scope("tests:sso-disable"):
        async with AsyncSessionLocal() as session:
            row = await session.get(TenantSSOConfig, sso_config)
            row.enabled = False
            await session.commit()
    status = await async_client.post("/api/v1/auth/sso/status", json={"email": "a@example.com"})
    assert status.json() == {"sso_available": False}
    response = await async_client.post(
        "/api/v1/auth/sso/authorize", json={"email": "a@example.com"}
    )
    assert response.status_code == 404


# --- callback: JIT + link + refusals -----------------------------------------


@pytest.mark.asyncio
async def test_callback_jit_provisions_mapped_role_and_oidc_session(
    async_client: AsyncClient, sso_config, monkeypatch
):
    response = await _flow(
        async_client,
        monkeypatch,
        email="new.person@example.com",
        subject="sub-1",
        groups=["staff"],
    )
    assert response.status_code == 200, response.text
    payload = decode_token(response.json()["access_token"])
    assert payload["auth_mode"] == "oidc"
    assert payload["role"] == "MANAGER"

    identity, user = await _linked_user("sub-1")
    assert identity is not None and identity.email_verified is True
    assert user.email == "new.person@example.com"
    assert user.role == "MANAGER"
    assert user.password_hash is None
    assert await _audit_actions(user.tenant_id) >= {"auth.sso_jit_provisioned"}


@pytest.mark.asyncio
async def test_jit_role_falls_back_to_default_when_no_group_maps(
    async_client: AsyncClient, sso_config, monkeypatch
):
    response = await _flow(
        async_client,
        monkeypatch,
        email="plain@example.com",
        subject="sub-plain",
        groups=["unmapped"],
    )
    assert response.status_code == 200
    _identity, user = await _linked_user("sub-plain")
    assert user.role == "USER"


@pytest.mark.asyncio
async def test_jit_never_grants_system_admin(async_client: AsyncClient, sso_tenant, monkeypatch):
    """A poisoned group_role_map row cannot mint SYSTEM_ADMIN (role cap)."""
    with tenant_context.system_scope("tests:sso-config"):
        async with AsyncSessionLocal() as session:
            session.add(
                TenantSSOConfig(
                    tenant_id=sso_tenant,
                    issuer=ISSUER,
                    client_id=CLIENT_ID,
                    client_secret_ref=SLOT,
                    allowed_domains=["example.com"],
                    default_role="ADMIN",
                    group_role_map={"root": "USER"},
                    enabled=True,
                    jit_enabled=True,
                    scopes=[],
                    created_by=None,
                )
            )
            await session.commit()

    response = await _flow(
        async_client, monkeypatch, email="root@example.com", subject="sub-root", groups=[]
    )
    assert response.status_code == 200
    _identity, user = await _linked_user("sub-root")
    assert user.role == "ADMIN"
    assert user.role != "SYSTEM_ADMIN"


@pytest.mark.asyncio
async def test_unverified_email_is_refused(async_client: AsyncClient, sso_config, monkeypatch):
    response = await _flow(
        async_client,
        monkeypatch,
        email="shady@example.com",
        subject="sub-shady",
        email_verified=False,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "sso_email_unverified"


@pytest.mark.asyncio
async def test_existing_local_email_answers_typed_409_then_links(
    async_client: AsyncClient, sso_tenant, sso_config, monkeypatch
):
    local = await create_user(
        email="known@example.com",
        password="local-pass-123",
        role="USER",
        tenant_id=sso_tenant,
        unique=False,
    )
    response = await _flow(
        async_client, monkeypatch, email="known@example.com", subject="sub-known"
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "sso_link_required"
    link_token = detail["link_token"]
    assert link_token

    wrong = await create_user(
        email="wrong@example.com", password="x-pass-123", tenant_id=sso_tenant
    )
    denied = await async_client.post(
        "/api/v1/auth/sso/link",
        json={"link_token": link_token},
        headers=await auth_headers(wrong),
    )
    assert denied.status_code == 403

    linked = await async_client.post(
        "/api/v1/auth/sso/link",
        json={"link_token": link_token},
        headers=await auth_headers(local),
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["linked"] is True

    identity, _user = await _linked_user("sub-known")
    assert str(identity.user_id) == str(local.id)
    assert str(identity.tenant_id) == str(local.tenant_id)

    replay = await async_client.post(
        "/api/v1/auth/sso/link",
        json={"link_token": link_token},
        headers=await auth_headers(local),
    )
    assert replay.status_code == 400

    response2 = await _flow(
        async_client, monkeypatch, email="known@example.com", subject="sub-known"
    )
    assert response2.status_code == 200
    payload = decode_token(response2.json()["access_token"])
    assert payload["user_id"] == str(local.id)


@pytest.mark.asyncio
async def test_cross_tenant_email_conflict_never_merges(
    async_client: AsyncClient, sso_config, monkeypatch
):
    await create_user(email="elsewhere@example.com", password="x-pass-123", unique=False)
    response = await _flow(
        async_client, monkeypatch, email="elsewhere@example.com", subject="sub-cross"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "sso_email_conflict"


@pytest.mark.asyncio
async def test_domain_outside_allowlist_is_refused(
    async_client: AsyncClient, sso_config, monkeypatch
):
    response = await _flow(
        async_client,
        monkeypatch,
        email="intruder@else.org",
        begin_email="member@example.com",
        subject="sub-intruder",
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "sso_domain_not_allowed"


@pytest.mark.asyncio
async def test_jit_disabled_refuses_unknown_subject_but_keeps_links(
    async_client: AsyncClient, sso_tenant, monkeypatch
):
    with tenant_context.system_scope("tests:sso-config"):
        async with AsyncSessionLocal() as session:
            session.add(
                TenantSSOConfig(
                    tenant_id=sso_tenant,
                    issuer=ISSUER,
                    client_id=CLIENT_ID,
                    client_secret_ref=SLOT,
                    allowed_domains=["example.com"],
                    default_role="USER",
                    group_role_map={},
                    enabled=True,
                    jit_enabled=False,
                    scopes=[],
                    created_by=None,
                )
            )
            await session.commit()

    response = await _flow(
        async_client, monkeypatch, email="outsider@example.com", subject="sub-outsider"
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "sso_jit_disabled"

    existing = await create_user(
        email="linked@example.com",
        password="x-pass-123",
        role=Role.USER,
        unique=False,
    )
    with tenant_context.system_scope("tests:sso-link"):
        async with AsyncSessionLocal() as session:
            session.add(
                UserIdentity(
                    tenant_id=existing.tenant_id,
                    user_id=existing.id,
                    issuer=ISSUER,
                    subject="sub-linked",
                    email=existing.email,
                    email_verified=True,
                )
            )
            await session.commit()

    response = await _flow(
        async_client, monkeypatch, email="linked@example.com", subject="sub-linked"
    )
    assert response.status_code == 200, response.text


# --- session integrity --------------------------------------------------------


@pytest.mark.asyncio
async def test_replayed_state_fails_closed(async_client: AsyncClient, sso_config, monkeypatch):
    state = await _begin(async_client)
    flow = json.loads(await token_store.redis_client.get(f"sso_state:{state}"))
    token = _mint_id_token(subject="sub-replay", email="replay@example.com", nonce=flow["nonce"])

    async def fake_exchange(endpoint, **kwargs):
        return {"id_token": token}

    monkeypatch.setattr("app.services.auth.sso._exchange_code", fake_exchange)
    first = await async_client.get(
        "/api/v1/auth/sso/callback", params={"code": "code-1", "state": state}
    )
    assert first.status_code == 200
    second = await async_client.get(
        "/api/v1/auth/sso/callback", params={"code": "code-1", "state": state}
    )
    assert second.status_code == 400
    assert second.json()["detail"]["code"] == "sso_state_invalid"


@pytest.mark.asyncio
async def test_tampered_or_foreign_signature_fails(
    async_client: AsyncClient, sso_config, monkeypatch
):
    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    response = await _flow(
        async_client,
        monkeypatch,
        email="rogue@example.com",
        subject="sub-rogue",
        sign_key=rogue,
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "sso_token_invalid"


@pytest.mark.asyncio
async def test_nonce_mismatch_fails(async_client: AsyncClient, sso_config, monkeypatch):
    response = await _flow(
        async_client,
        monkeypatch,
        email="nonce@example.com",
        subject="sub-nonce",
        nonce_override="attacker-chosen",
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "sso_nonce_mismatch"


# --- IdP down / fallback to local / role remap --------------------------------


@pytest.mark.asyncio
async def test_idp_down_answers_typed_503_and_local_login_still_works(
    async_client: AsyncClient, sso_config, monkeypatch
):
    from app.services.auth import sso

    async def down(url: str) -> dict:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(sso, "_fetch_json", down)
    sso.clear_provider_cache()
    response = await async_client.post(
        "/api/v1/auth/sso/authorize", json={"email": "a@example.com"}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "sso_provider_unavailable"

    local = await create_user(
        email="local@example.com", password="local-pass-9", role="USER", unique=False
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        data={"username": local.email, "password": "local-pass-9"},
    )
    assert login.status_code == 200
    assert decode_token(login.json()["access_token"])["auth_mode"] == "password"


@pytest.mark.asyncio
async def test_group_remap_demotes_on_login_and_audits(
    async_client: AsyncClient, sso_config, monkeypatch
):
    linked = await create_user(email="staffer@example.com", password="x-pass-123", unique=False)
    with tenant_context.system_scope("tests:sso-link"):
        async with AsyncSessionLocal() as session:
            user = await session.get(UserModel, linked.id)
            user.role = "MANAGER"
            session.add(
                UserIdentity(
                    tenant_id=linked.tenant_id,
                    user_id=linked.id,
                    issuer=ISSUER,
                    subject="sub-staffer",
                    email=linked.email,
                    email_verified=True,
                )
            )
            await session.commit()

    response = await _flow(
        async_client,
        monkeypatch,
        email="staffer@example.com",
        subject="sub-staffer",
        groups=[],
    )
    assert response.status_code == 200
    payload = decode_token(response.json()["access_token"])
    assert payload["role"] == "USER"
    actions = await _audit_actions(linked.tenant_id)
    assert "sso_role_change" in actions


@pytest.mark.asyncio
async def test_group_remap_demotion_kills_outstanding_tokens(
    async_client: AsyncClient, sso_tenant, sso_config, monkeypatch
):
    """A demotion takes effect immediately (admin/SCIM parity, §8).

    ``_apply_role_on_login`` bumps ``users.token_version`` and the callback
    drops the Redis hot layer, so a token minted while the member still held
    MANAGER is dead by the time the SSO login answers — not 60 minutes later
    at its next refresh. The freshly minted session carries the new role.
    """
    from tests._auth_helpers import sign_in

    user = await _link_user(
        email="demoted@example.com",
        subject="sub-demoted",
        role=Role.MANAGER,
        tenant_id=sso_tenant,
    )
    stale = await sign_in(user)
    stale_headers = {"Authorization": f"Bearer {stale.access_token}"}
    before = await async_client.get("/api/v1/users/me", headers=stale_headers)
    assert before.status_code == 200, before.text
    assert before.json()["role"] == "MANAGER"

    response = await _flow(
        async_client,
        monkeypatch,
        email="demoted@example.com",
        subject="sub-demoted",
        groups=[],
    )
    assert response.status_code == 200, response.text

    # The callback stamped the new session cookies on the shared client; the
    # probe must present the *stale* bearer, not the fresh cookie.
    async_client.cookies.clear()
    after = await async_client.get("/api/v1/users/me", headers=stale_headers)
    assert after.status_code == 401, after.text

    fresh = decode_token(response.json()["access_token"])
    assert fresh["role"] == "USER"
    assert fresh["ver"] == 2


# --- tenant policy parity: MFA + IP (program 12C1) --------------------------


async def _set_tenant_policy(tenant_id, **settings) -> None:
    """Patch ``tenants.settings`` directly (the policy surface has its own suite)."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.tenant_model import TenantModel

    with tenant_context.system_scope("tests:sso-policy"):
        async with AsyncSessionLocal() as session:
            tenant = await session.get(TenantModel, tenant_id)
            merged = dict(tenant.settings or {})
            merged.update(settings)
            tenant.settings = merged
            flag_modified(tenant, "settings")
            await session.commit()


async def _link_user(
    *, email: str, subject: str, role: Role, tenant_id, password: str = "x-pass-123"
) -> UserModel:
    user = await create_user(
        email=email, password=password, role=role, tenant_id=tenant_id, unique=False
    )
    with tenant_context.system_scope("tests:sso-link"):
        async with AsyncSessionLocal() as session:
            session.add(
                UserIdentity(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    issuer=ISSUER,
                    subject=subject,
                    email=email,
                    email_verified=True,
                )
            )
            await session.commit()
    return user


def _bearer(response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_mfa_policy_challenges_sso_login_without_amr(
    async_client: AsyncClient, sso_tenant, sso_config, monkeypatch
):
    await _set_tenant_policy(sso_tenant, mfa_required_roles=["USER"])

    response = await _flow(
        async_client, monkeypatch, email="policy@example.com", subject="sub-policy"
    )

    assert response.status_code == 401, response.text
    body = response.json()
    assert body["detail"] == "mfa_required"
    assert body["enrollment_needed"] is True
    assert body["origin"] == "sso"
    challenge = verify_mfa_challenge_token(body["mfa_token"])
    assert challenge is not None and challenge["origin"] == "sso"
    assert "set-cookie" not in {key.lower() for key in response.headers}


@pytest.mark.asyncio
async def test_mfa_policy_allows_sso_login_when_idp_asserts_amr(
    async_client: AsyncClient, sso_tenant, sso_config, monkeypatch
):
    await _set_tenant_policy(sso_tenant, mfa_required_roles=["USER"])

    response = await _flow(
        async_client,
        monkeypatch,
        email="amr@example.com",
        subject="sub-amr",
        amr=["pwd", "otp"],
        auth_time=int(time.time()),
    )

    assert response.status_code == 200, response.text
    payload = decode_token(response.json()["access_token"])
    assert payload["auth_mode"] == "oidc"
    assert payload["amr"] == ["pwd", "otp"]
    assert payload["auth_time"] == int(payload["auth_time"])


@pytest.mark.asyncio
async def test_acr_mfa_level_also_satisfies_the_policy(
    async_client: AsyncClient, sso_tenant, sso_config, monkeypatch
):
    await _set_tenant_policy(sso_tenant, mfa_required_roles=["USER"])

    response = await _flow(
        async_client,
        monkeypatch,
        email="acr@example.com",
        subject="sub-acr",
        acr="urn:example:acr:mfa",
    )

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_sso_ip_allowlist_denies_the_callback(
    async_client: AsyncClient, sso_tenant, sso_config, monkeypatch
):
    await _set_tenant_policy(sso_tenant, ip_allowlist=["10.0.0.0/8"])

    response = await _flow(
        async_client, monkeypatch, email="ipdenied@example.com", subject="sub-ipdenied"
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "ip_not_allowed"
    assert "set-cookie" not in {key.lower() for key in response.headers}

    # Nothing was provisioned: the refusal runs before the resolution, so a
    # denied address leaves no JIT account and no identity link behind.
    identity, user = await _linked_user("sub-ipdenied")
    assert identity is None and user is None
    actions = await _audit_actions(sso_tenant)
    assert "auth.sso_jit_provisioned" not in actions


@pytest.mark.asyncio
async def test_local_mfa_secret_challenges_sso_and_completes_with_totp(
    async_client: AsyncClient, sso_tenant, sso_config, monkeypatch
):
    """An enrolled local secret is a second factor SSO must not skip."""
    from app.core.totp import totp_code_at
    from app.services.auth import mfa_service

    user = await _link_user(
        email="localtotp@example.com",
        subject="sub-localtotp",
        role=Role.USER,
        tenant_id=sso_tenant,
    )
    enrollment = await mfa_service.begin_enrollment(user)
    assert await mfa_service.confirm_enrollment(
        user.id, totp_code_at(enrollment["secret"], time.time())
    )

    challenged = await _flow(
        async_client, monkeypatch, email="localtotp@example.com", subject="sub-localtotp"
    )
    assert challenged.status_code == 401, challenged.text
    body = challenged.json()
    assert body["detail"] == "mfa_required"
    assert body["enrollment_needed"] is False

    verified = await async_client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "mfa_token": body["mfa_token"],
            "code": totp_code_at(enrollment["secret"], time.time()),
        },
    )

    assert verified.status_code == 200, verified.text
    payload = decode_token(verified.json()["access_token"])
    assert payload["auth_mode"] == "oidc"


@pytest.mark.asyncio
async def test_policy_flip_challenges_the_next_sso_login_but_not_live_sessions(
    async_client: AsyncClient, sso_tenant, sso_config, monkeypatch
):
    user = await _link_user(
        email="flip@example.com", subject="sub-flip", role=Role.USER, tenant_id=sso_tenant
    )

    first = await _flow(async_client, monkeypatch, email="flip@example.com", subject="sub-flip")
    assert first.status_code == 200, first.text
    headers = _bearer(first)
    async_client.cookies.clear()
    assert (await async_client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    await _set_tenant_policy(sso_tenant, mfa_required_roles=["USER"])

    # Live sessions are untouched (MFA gates sign-in, never a live bearer);
    # the *next* SSO sign-in answers the challenge.
    assert (await async_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    second = await _flow(async_client, monkeypatch, email="flip@example.com", subject="sub-flip")
    assert second.status_code == 401
    assert second.json()["detail"] == "mfa_required"
    assert str(user.id)


def test_second_factor_asserted_vocabulary():
    """``amr``/``acr`` parsing: only evidence counts, absence never does."""
    from app.services.auth.sso import IdentityClaims, second_factor_asserted

    def claims(**kwargs) -> IdentityClaims:
        base = {"subject": "s", "email": "a@b.c", "email_verified": True}
        base.update(kwargs)
        return IdentityClaims(**base)

    assert second_factor_asserted(claims(amr=("mfa",))) is True
    assert second_factor_asserted(claims(amr=("pwd", "otp"))) is True
    assert second_factor_asserted(claims(amr=("pwd",))) is False
    assert second_factor_asserted(claims()) is False
    assert second_factor_asserted(claims(acr="urn:x:acr:mfa")) is True
    assert second_factor_asserted(claims(acr="2")) is True
    assert second_factor_asserted(claims(acr="1")) is False
    assert second_factor_asserted(claims(acr="urn:x:acr:phrh")) is False
