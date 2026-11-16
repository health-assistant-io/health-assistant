"""Scoped API tokens / personal access tokens (program 12C2B).

Covers the credential itself (one-time plaintext, hash at rest, rotation,
revocation, expiry) and every enforcement boundary the plan names:

- a valid PAT authenticates a domain route and is bounded by its owner's
  live patient reach;
- expired / revoked / malformed / cross-tenant credentials all answer 401
  (the cross-tenant case cannot even resolve a row); a malformed bearer's
  ``api_token.denied`` row records ``token_prefix: null`` (the kit's strict
  parse rejects anything outside the ``ha_pat`` shape);
- the route gate uses the PAT's permission set, not the owner's role;
- patient scope (``all|granted|none``) is enforced at the resource layer
  and never widens past the owner;
- a role demotion narrows an outstanding token on its next request;
- creation/update refuse a permission the owner does not hold;
- a PAT cannot manage tokens, rotate credentials or reach the system
  surfaces (no escalation, no session/refresh issuance);
- use and denial are audited; no plaintext or hash ever appears in the
  database projection, audit payloads or list responses.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from nx_auth.opaque import parse
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import is_pat_token
from app.models.api_token import ApiToken, PatientScope
from app.models.audit_model import AuditEvent
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.models.user_model import UserModel
from app.services.auth import api_tokens
from tests._auth_helpers import create_tenant, create_user, make_user_headers


async def _create_pat(owner: UserModel, permissions: list[str], **kwargs) -> tuple[ApiToken, str]:
    async with AsyncSessionLocal() as db:
        row, plaintext = await api_tokens.create_token(
            db,
            owner=owner,
            name=kwargs.pop("name", "pytest token"),
            permissions=permissions,
            **kwargs,
        )
        await db.commit()
        await db.refresh(row)
        return row, plaintext


def _headers(plaintext: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plaintext}"}


async def _add_patient(tenant_id, user_id, *, name="Record") -> uuid.UUID:
    patient_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                user_id=user_id,
                name={"family": name, "given": ["Own"]},
                gender="UNKNOWN",
            )
        )
        await session.commit()
    return patient_id


async def _audit_actions(action: str) -> list[AuditEvent]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AuditEvent).where(AuditEvent.action == action))
        return list(result.scalars().all())


# --- credential shape -------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_plaintext_once_and_stores_only_a_hash(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    _user, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)

    response = await async_client.post(
        "/api/v1/me/api-tokens",
        json={
            "name": "CLI",
            "permissions": ["patient:read"],
            "patient_scope": "all",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    plaintext = body["token"]
    assert is_pat_token(plaintext)
    assert body["api_token"]["token_prefix"] in plaintext
    assert "token_hash" not in body["api_token"]
    assert plaintext not in response.text.replace(body["token"], "", 1)

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(ApiToken).where(ApiToken.id == uuid.UUID(body["api_token"]["id"]))
            )
        ).scalar_one()
        assert row.token_hash == parse(plaintext, brand="ha_pat")[1]
        assert row.token_hash != plaintext
        assert plaintext not in str(row.to_dict())

    listing = await async_client.get("/api/v1/me/api-tokens", headers=headers)
    assert listing.status_code == 200
    assert plaintext not in listing.text
    assert "token_hash" not in listing.text

    audit = await _audit_actions("api_token.created")
    assert audit and all(plaintext not in str(row.new_value) for row in audit)


#: A token minted by the retired ``generate_token`` (``ha_pat_<12 hex>_<43>``)
#: with its sha256 — hard-coded so a kit change cannot mask a format drift.
_LEGACY_PLAINTEXT = "ha_pat_a1b2c3d4e5f6_Fq7Zr2Lm9Xc4Vb1Ns8Kd5Hg0Tj3Wp6Yu-Io_Er4Aq2B"
_LEGACY_PREFIX = "a1b2c3d4e5f6"
_LEGACY_DIGEST = "7ee2ff71b6f2cb75f454e5d615df898670eb0ccda8466c32724ac02200480975"

GOLDEN_PAT = re.compile(r"ha_pat_[0-9a-f]{12}_[A-Za-z0-9_-]{43}")


@pytest.mark.asyncio
async def test_minted_pat_keeps_the_golden_ha_pat_shape(async_client: AsyncClient):
    """Post-change mint: exact format, prefix column, full-value digest."""
    tenant_id = await create_tenant()
    user, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    row, plaintext = await _create_pat(user, ["patient:read"])

    assert GOLDEN_PAT.fullmatch(plaintext), plaintext
    assert plaintext.startswith(f"ha_pat_{row.token_prefix}_")
    assert len(row.token_prefix) == 12
    assert all(char in "0123456789abcdef" for char in row.token_prefix)
    parsed = parse(plaintext, brand="ha_pat")
    assert parsed is not None
    assert parsed[0] == row.token_prefix
    assert parsed[1] == row.token_hash


@pytest.mark.asyncio
async def test_pre_change_token_still_verifies(async_client: AsyncClient):
    """A pre-adoption ``generate_token`` value keeps authenticating unchanged."""
    tenant_id = await create_tenant()
    user, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    own_id = await _add_patient(tenant_id, user.id)
    async with AsyncSessionLocal() as db:
        db.add(
            ApiToken(
                tenant_id=tenant_id,
                owner_user_id=user.id,
                name="pre-adoption",
                token_prefix=_LEGACY_PREFIX,
                token_hash=_LEGACY_DIGEST,
                permissions=["patient:read"],
                patient_scope=PatientScope.ALL.value,
                patient_ids=[],
            )
        )
        await db.commit()

    response = await async_client.get("/api/v1/patients", headers=_headers(_LEGACY_PLAINTEXT))
    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]} == {str(own_id)}


def test_kit_parse_rejects_non_golden_bearers():
    """Format-only, strict: only the exact minted shape yields a prefix."""
    good_secret = "A" * 43
    assert parse(f"ha_pat_{_LEGACY_PREFIX}_{good_secret}", brand="ha_pat")[0] == _LEGACY_PREFIX
    assert parse("ha_pat_not-a-real-token", brand="ha_pat") is None
    assert parse(f"ha_pat_{_LEGACY_PREFIX.upper()}_{good_secret}", brand="ha_pat") is None
    assert parse(f"ha_pat_a1b2c3d4e5_{good_secret}", brand="ha_pat") is None
    assert parse(f"ha_pat_zzzzzzzzzzzz_{good_secret}", brand="ha_pat") is None
    assert parse(f"ha_int_{_LEGACY_PREFIX}_{good_secret}", brand="ha_pat") is None
    assert parse(f"ha_pat_{_LEGACY_PREFIX}_", brand="ha_pat") is None
    assert parse("", brand="ha_pat") is None


@pytest.mark.asyncio
async def test_valid_pat_authenticates_a_domain_route(async_client: AsyncClient):
    tenant_id = await create_tenant()
    user, _headers_session = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    own_id = await _add_patient(tenant_id, user.id)
    _row, plaintext = await _create_pat(user, ["patient:read"])

    response = await async_client.get("/api/v1/patients", headers=_headers(plaintext))
    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]} == {str(own_id)}

    detail = await async_client.get(f"/api/v1/patients/{own_id}", headers=_headers(plaintext))
    assert detail.status_code == 200

    assert await _audit_actions("api_token.used")
    async with AsyncSessionLocal() as db:
        row = await db.get(ApiToken, _row.id)
        assert row.last_used_at is not None


@pytest.mark.asyncio
async def test_pat_use_is_stamped_and_audited_once_per_window(async_client: AsyncClient):
    """Each use stamps ``last_used_at``; the audit row is throttled (60s).

    The hot path must not write one audit row per request — a chatty CLI
    would flood the stream — so the first use in a window records it and the
    rest ride the stamp.
    """
    tenant_id = await create_tenant()
    user, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    row, plaintext = await _create_pat(user, ["patient:read"])

    before = len(await _audit_actions("api_token.used"))
    assert (
        await async_client.get("/api/v1/patients", headers=_headers(plaintext))
    ).status_code == 200
    after_first = len(await _audit_actions("api_token.used"))
    assert after_first == before + 1

    stamp = None
    async with AsyncSessionLocal() as db:
        stored = await db.get(ApiToken, row.id)
        stamp = stored.last_used_at
    assert stamp is not None

    assert (
        await async_client.get("/api/v1/patients", headers=_headers(plaintext))
    ).status_code == 200
    assert len(await _audit_actions("api_token.used")) == after_first, (
        "a second use inside the window wrote another audit row"
    )

    async with AsyncSessionLocal() as db:
        stored = await db.get(ApiToken, row.id)
        assert stored.last_used_at == stamp


@pytest.mark.asyncio
async def test_a_pat_cannot_open_a_websocket(async_client: AsyncClient):
    """WebSockets are session-only (identity-auth §8/§12): the handshake goes
    through the session verifier, so an opaque ``pat`` can never hold one."""
    from fastapi.testclient import TestClient as FastTestClient
    from starlette.websockets import WebSocketDisconnect

    from app.main import app

    tenant_id = await create_tenant()
    user, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    _row, plaintext = await _create_pat(user, ["patient:read"])

    client = FastTestClient(app)
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "/api/v1/ws/notifications",
            headers={"origin": "http://testserver", "cookie": f"nx_access={plaintext}"},
        ),
    ):
        pass


@pytest.mark.asyncio
async def test_expired_and_revoked_pats_are_401(async_client: AsyncClient):
    tenant_id = await create_tenant()
    user, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)

    expired_row, expired_plain = await _create_pat(
        user, ["patient:read"], expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    async with AsyncSessionLocal() as db:
        row = await db.get(ApiToken, expired_row.id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()

    expired = await async_client.get("/api/v1/patients", headers=_headers(expired_plain))
    assert expired.status_code == 401

    _revoked_row, revoked_plain = await _create_pat(user, ["patient:read"])
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(ApiToken).where(
                    ApiToken.token_prefix == parse(revoked_plain, brand="ha_pat")[0]
                )
            )
        ).scalar_one()
        row.revoked_at = datetime.now(UTC)
        await db.commit()

    revoked = await async_client.get("/api/v1/patients", headers=_headers(revoked_plain))
    assert revoked.status_code == 401
    denied = await _audit_actions("api_token.denied")
    assert len(denied) >= 2


@pytest.mark.asyncio
async def test_cross_tenant_patient_is_a_hidden_404(async_client: AsyncClient):
    tenant_a = await create_tenant()
    tenant_b = await create_tenant()
    user_a, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_a)
    user_b = await create_user(role=Role.USER, tenant_id=tenant_b)
    foreign_patient = await _add_patient(tenant_b, user_b.id)
    _row, plaintext = await _create_pat(user_a, ["patient:read"])

    response = await async_client.get(
        f"/api/v1/patients/{foreign_patient}", headers=_headers(plaintext)
    )
    assert response.status_code == 404
    assert str(foreign_patient) not in response.text

    listing = await async_client.get("/api/v1/patients", headers=_headers(plaintext))
    assert listing.status_code == 200
    assert listing.json()["items"] == []


@pytest.mark.asyncio
async def test_malformed_pat_is_401(async_client: AsyncClient):
    response = await async_client.get(
        "/api/v1/patients", headers={"Authorization": "Bearer ha_pat_not-a-real-token"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_malformed_bearer_denial_records_a_null_prefix(async_client: AsyncClient):
    """Behavior delta vs the retired ``token_prefix_of``: no junk prefix.

    A bearer that cannot have been minted in the ``ha_pat`` shape is
    rejected by the kit's strict ``parse`` before the lookup, so the
    fail-closed ``api_token.denied`` row carries ``token_prefix: null`` —
    the audit stream never gains an arbitrary segment again.
    """
    response = await async_client.get(
        "/api/v1/patients", headers={"Authorization": "Bearer ha_pat_zzzzzzzzzzzz_junk"}
    )
    assert response.status_code == 401

    async with AsyncSessionLocal() as db:
        newest = (
            await db.execute(
                select(AuditEvent)
                .where(AuditEvent.action == "api_token.denied")
                .order_by(AuditEvent.seq.desc())
                .limit(1)
            )
        ).scalar_one()
    assert newest.new_value["token_prefix"] is None
    assert "reason" in newest.new_value


# --- permission subset, route gate, no escalation ---------------------------


@pytest.mark.asyncio
async def test_create_refuses_a_permission_the_owner_does_not_hold(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    _user, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    response = await async_client.post(
        "/api/v1/me/api-tokens",
        json={"name": "escalate", "permissions": ["audit:read"]},
        headers=headers,
    )
    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_gate_uses_the_pat_permission_set_not_the_owner_role(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    admin, _ = await make_user_headers(role=Role.ADMIN, tenant_id=tenant_id)
    _row, plaintext = await _create_pat(admin, ["patient:read"])

    allowed = await async_client.get("/api/v1/patients", headers=_headers(plaintext))
    assert allowed.status_code == 200

    denied = await async_client.get("/api/v1/settings/tenant", headers=_headers(plaintext))
    assert denied.status_code == 403
    assert denied.headers.get("x-authz-denied") == "1"


@pytest.mark.asyncio
async def test_pat_never_reaches_the_system_scope(async_client: AsyncClient):
    tenant_id = await create_tenant()
    system_admin, _ = await make_user_headers(role=Role.SYSTEM_ADMIN, tenant_id=tenant_id)
    _row, plaintext = await _create_pat(system_admin, ["settings:read"])

    response = await async_client.get("/api/v1/settings/system", headers=_headers(plaintext))
    assert response.status_code == 403
    assert response.headers.get("x-authz-denied") == "1"


@pytest.mark.asyncio
async def test_owner_role_shrink_narrows_an_outstanding_token(async_client: AsyncClient):
    tenant_id = await create_tenant()
    admin, _ = await make_user_headers(role=Role.ADMIN, tenant_id=tenant_id)
    _row, plaintext = await _create_pat(admin, ["settings:read"])

    before = await async_client.get("/api/v1/settings/tenant", headers=_headers(plaintext))
    assert before.status_code == 200

    async with AsyncSessionLocal() as db:
        row = await db.get(UserModel, admin.id)
        row.role = Role.USER
        await db.commit()

    after = await async_client.get("/api/v1/settings/tenant", headers=_headers(plaintext))
    assert after.status_code == 403
    assert after.headers.get("x-authz-denied") == "1"


@pytest.mark.asyncio
async def test_pat_cannot_manage_tokens_or_rotate_credentials(async_client: AsyncClient):
    tenant_id = await create_tenant()
    user, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    _row, plaintext = await _create_pat(user, ["patient:read"])

    mint = await async_client.post(
        "/api/v1/me/api-tokens",
        json={"name": "child", "permissions": ["patient:read"]},
        headers=_headers(plaintext),
    )
    assert mint.status_code == 403

    password = await async_client.patch(
        "/api/v1/me/password",
        json={"current_password": "whatever", "new_password": "another-long-one"},
        headers=_headers(plaintext),
    )
    assert password.status_code == 403

    refresh = await async_client.post("/api/v1/auth/refresh", headers=_headers(plaintext))
    assert refresh.status_code in (401, 403)


@pytest.mark.asyncio
async def test_export_stays_step_up_gated_for_a_pat(async_client: AsyncClient):
    """§8 — a bounded bearer can never step up (no family, so no proof can
    exist): a gated action answers the ordinary ``403 step_up_required``."""
    tenant_id = await create_tenant()
    user, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    _row, plaintext = await _create_pat(user, ["export:patient"])

    response = await async_client.post(
        "/api/v1/export",
        json={"scope": "patient", "export_type": "fhir_only", "patient_ids": [str(uuid.uuid4())]},
        headers=_headers(plaintext),
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "step_up_required"
    assert detail["methods"] == []
    assert detail["reason"] == "no_session_family"


# --- patient scope ----------------------------------------------------------


@pytest.mark.asyncio
async def test_granted_scope_denies_a_patient_outside_the_allowlist(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    user, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    first = await _add_patient(tenant_id, user.id, name="First")
    second = await _add_patient(tenant_id, user.id, name="Second")
    _row, plaintext = await _create_pat(
        user,
        ["patient:read"],
        patient_scope=PatientScope.GRANTED.value,
        patient_ids=[first],
    )

    allowed = await async_client.get(f"/api/v1/patients/{first}", headers=_headers(plaintext))
    assert allowed.status_code == 200
    denied = await async_client.get(f"/api/v1/patients/{second}", headers=_headers(plaintext))
    assert denied.status_code == 403
    assert denied.headers.get("x-authz-denied") is None

    listing = await async_client.get("/api/v1/patients", headers=_headers(plaintext))
    assert listing.status_code == 200
    assert {item["id"] for item in listing.json()["items"]} == {str(first)}


@pytest.mark.asyncio
async def test_none_scope_denies_every_patient(async_client: AsyncClient):
    tenant_id = await create_tenant()
    user, _ = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    patient_id = await _add_patient(tenant_id, user.id)
    _row, plaintext = await _create_pat(
        user, ["patient:read"], patient_scope=PatientScope.NONE.value
    )

    denied = await async_client.get(f"/api/v1/patients/{patient_id}", headers=_headers(plaintext))
    assert denied.status_code == 403
    listing = await async_client.get("/api/v1/patients", headers=_headers(plaintext))
    assert listing.status_code == 200
    assert listing.json()["items"] == []


@pytest.mark.asyncio
async def test_granted_scope_cannot_name_a_patient_the_owner_cannot_reach(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    _user, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    stranger = await create_user(role=Role.USER, tenant_id=tenant_id)
    foreign = await _add_patient(tenant_id, stranger.id)

    response = await async_client.post(
        "/api/v1/me/api-tokens",
        json={
            "name": "too wide",
            "permissions": ["patient:read"],
            "patient_scope": "granted",
            "patient_ids": [str(foreign)],
        },
        headers=headers,
    )
    assert response.status_code == 400, response.text


# --- lifecycle: update, rotate, revoke --------------------------------------


@pytest.mark.asyncio
async def test_rotate_revokes_the_predecessor_and_keeps_the_subset(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    user, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    await _add_patient(tenant_id, user.id)
    row, old_plain = await _create_pat(user, ["patient:read"])

    response = await async_client.post(f"/api/v1/me/api-tokens/{row.id}/rotate", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    new_plain = body["token"]
    assert new_plain != old_plain
    assert body["api_token"]["rotated_from"] == str(row.id)
    assert body["api_token"]["permissions"] == ["patient:read"]

    old = await async_client.get("/api/v1/patients", headers=_headers(old_plain))
    assert old.status_code == 401
    new = await async_client.get("/api/v1/patients", headers=_headers(new_plain))
    assert new.status_code == 200

    async with AsyncSessionLocal() as db:
        predecessor = await db.get(ApiToken, row.id)
        assert predecessor.revoked_at is not None


@pytest.mark.asyncio
async def test_rotation_refuses_a_subset_the_owner_no_longer_holds(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    admin, headers = await make_user_headers(role=Role.ADMIN, tenant_id=tenant_id)
    row, _plaintext = await _create_pat(admin, ["settings:read"])

    async with AsyncSessionLocal() as db:
        owner = await db.get(UserModel, admin.id)
        owner.role = Role.USER
        await db.commit()

    response = await async_client.post(f"/api/v1/me/api-tokens/{row.id}/rotate", headers=headers)
    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_update_and_revoke_are_owner_scoped_and_revalidate_subsets(
    async_client: AsyncClient,
):
    tenant_id = await create_tenant()
    user, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    row, plaintext = await _create_pat(user, ["patient:read"])

    escalated = await async_client.patch(
        f"/api/v1/me/api-tokens/{row.id}",
        json={"permissions": ["tenant:manage"]},
        headers=headers,
    )
    assert escalated.status_code == 400

    updated = await async_client.patch(
        f"/api/v1/me/api-tokens/{row.id}",
        json={"name": "renamed", "permissions": ["patient:read", "clinical:read"]},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "renamed"
    assert set(updated.json()["permissions"]) == {"patient:read", "clinical:read"}

    other_tenant = await create_tenant()
    _other_user, other_headers = await make_user_headers(role=Role.USER, tenant_id=other_tenant)
    hidden = await async_client.get(f"/api/v1/me/api-tokens/{row.id}", headers=other_headers)
    assert hidden.status_code == 404

    revoked = await async_client.delete(f"/api/v1/me/api-tokens/{row.id}", headers=headers)
    assert revoked.status_code == 204
    gone = await async_client.get("/api/v1/patients", headers=_headers(plaintext))
    assert gone.status_code == 401


@pytest.mark.asyncio
async def test_permission_catalog_mirrors_the_owner_catalog(async_client: AsyncClient):
    tenant_id = await create_tenant()
    _user, headers = await make_user_headers(role=Role.USER, tenant_id=tenant_id)
    response = await async_client.get("/api/v1/me/api-tokens/permissions", headers=headers)
    assert response.status_code == 200
    body = response.json()
    values = {item["permission"] for item in body["permissions"]}
    assert "patient:read" in values
    assert "tenant:manage" not in values
    assert body["patient_scopes"] == ["all", "granted", "none"]
