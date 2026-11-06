"""Own-session surface — ``/api/v1/me/sessions`` (identity-auth §12).

The device list is the security control self-hosted multi-device users need:
it must show only the caller's live families, mark the calling one, and revoke
exactly one family (hidden-404 for anything not owned).

Sign-ins go through the real issuance path with refresh enabled, because the
device list is exactly the ``auth_sessions`` family rows a real sign-in writes.
"""

import pytest
from httpx import AsyncClient

from app.core.database import AsyncSessionLocal
from app.models.auth_session_model import AuthSessionModel
from app.models.enums import Role
from app.services.auth.auth_session_service import list_for_user as list_families
from app.services.auth.auth_session_service import revoke_family
from tests._auth_helpers import create_user, sign_in


async def _sign_in_headers(user, label: str) -> dict[str, str]:
    issued = await sign_in(user, client_label=label)
    return {"Authorization": f"Bearer {issued.access_token}"}


@pytest.mark.asyncio
async def test_list_returns_own_live_families(async_client: AsyncClient):
    user = await create_user(role=Role.USER, full_name="Session Owner")
    headers = await _sign_in_headers(user, "laptop")
    await _sign_in_headers(user, "tablet")

    response = await async_client.get("/api/v1/me/sessions", headers=headers)

    assert response.status_code == 200, response.text
    rows = response.json()
    labels = sorted(row["client_label"] for row in rows)
    assert labels == ["laptop", "tablet"]
    current = [row for row in rows if row["current"]]
    assert len(current) == 1
    assert current[0]["client_label"] == "laptop"
    assert all(row["revoked_at"] is None for row in rows)


@pytest.mark.asyncio
async def test_list_never_leaks_another_users_families(async_client: AsyncClient):
    owner = await create_user(role=Role.USER)
    stranger = await create_user(role=Role.USER)
    await _sign_in_headers(owner, "owner-laptop")
    await _sign_in_headers(owner, "owner-phone")
    stranger_headers = await _sign_in_headers(stranger, "stranger-laptop")

    response = await async_client.get("/api/v1/me/sessions", headers=stranger_headers)

    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["client_label"] for row in rows] == ["stranger-laptop"]
    assert rows[0]["current"] is True


@pytest.mark.asyncio
async def test_revoke_hides_other_users_family_as_404(async_client: AsyncClient):
    owner = await create_user(role=Role.USER)
    stranger = await create_user(role=Role.USER)
    await _sign_in_headers(owner, "owner-laptop")
    stranger_headers = await _sign_in_headers(stranger, "stranger-laptop")
    owner_families = await list_families(owner.id)

    response = await async_client.delete(
        f"/api/v1/me/sessions/{owner_families[0].id}", headers=stranger_headers
    )

    assert response.status_code == 404
    async with AsyncSessionLocal() as session:
        row = await session.get(AuthSessionModel, owner_families[0].id)
    assert row is not None and row.revoked_at is None


@pytest.mark.asyncio
async def test_revoke_retires_the_family(async_client: AsyncClient):
    user = await create_user(role=Role.USER)
    headers = await _sign_in_headers(user, "laptop")
    await _sign_in_headers(user, "tablet")
    families = await list_families(user.id)
    target = next(family for family in families if family.client_label == "tablet")

    response = await async_client.delete(f"/api/v1/me/sessions/{target.id}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"revoked": True}
    remaining = await async_client.get("/api/v1/me/sessions", headers=headers)
    assert remaining.status_code == 200
    assert [row["client_label"] for row in remaining.json()] == ["laptop"]


@pytest.mark.asyncio
async def test_revoke_unknown_family_is_404(async_client: AsyncClient):
    user = await create_user(role=Role.USER)
    headers = await _sign_in_headers(user, "laptop")

    response = await async_client.delete(
        "/api/v1/me/sessions/00000000-0000-0000-0000-000000000000", headers=headers
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_revoking_the_current_family_invalidates_the_bearer(async_client: AsyncClient):
    user = await create_user(role=Role.USER)
    headers = await _sign_in_headers(user, "laptop")
    current = (await async_client.get("/api/v1/me/sessions", headers=headers)).json()
    assert len(current) == 1 and current[0]["current"] is True

    revoked = await async_client.delete(f"/api/v1/me/sessions/{current[0]['id']}", headers=headers)

    assert revoked.status_code == 200, revoked.text
    after = await async_client.get("/api/v1/me/sessions", headers=headers)
    assert after.status_code == 401, after.text
    async with AsyncSessionLocal() as session:
        row = await session.get(AuthSessionModel, current[0]["id"])
        assert row is not None and row.revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_family_is_idempotent_at_the_store(async_client: AsyncClient):
    user = await create_user(role=Role.USER)
    await _sign_in_headers(user, "laptop")
    await _sign_in_headers(user, "tablet")
    families = await list_families(user.id)
    await revoke_family(families[0].id)
    await revoke_family(families[0].id)
    live = await list_families(user.id)
    assert len(live) == 1
