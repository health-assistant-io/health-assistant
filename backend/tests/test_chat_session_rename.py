"""PATCH /ai-assistance/sessions/{id} — the family B3 rename contract.

Tenant-scoped title update powering the library `ChatSessionList` rename
row action: the caller only ever renames sessions their own tenant owns,
foreign sessions are a hidden 404, and the persisted title round-trips
through `GET /ai-assistance/sessions`.
"""

import uuid

from app.core.database import AsyncSessionLocal
from app.models.enums import Role
from app.services.chat_session_service import ChatSessionService
from tests._auth_helpers import auth_headers, create_tenant, create_user


async def _create_chat_session(user_id: uuid.UUID, tenant_id: uuid.UUID, title: str):
    async with AsyncSessionLocal() as db:
        return await ChatSessionService(db).create_session(user_id, tenant_id, title=title)


async def test_rename_session_updates_title(async_client):
    tenant_id = await create_tenant()
    user = await create_user(role=Role.USER, tenant_id=tenant_id)
    headers = await auth_headers(user)
    session = await _create_chat_session(user.id, tenant_id, "New Chat")

    response = await async_client.patch(
        f"/api/v1/ai-assistance/sessions/{session.id}",
        headers=headers,
        json={"title": "Metformin review"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(session.id)
    assert body["title"] == "Metformin review"

    listed = await async_client.get("/api/v1/ai-assistance/sessions", headers=headers)
    assert listed.status_code == 200
    assert [s["title"] for s in listed.json()] == ["Metformin review"]


async def test_rename_session_hidden_404_for_other_user(async_client):
    tenant_id = await create_tenant()
    owner = await create_user(role=Role.USER, tenant_id=tenant_id)
    stranger = await create_user(role=Role.USER, tenant_id=tenant_id)
    session = await _create_chat_session(owner.id, tenant_id, "New Chat")

    response = await async_client.patch(
        f"/api/v1/ai-assistance/sessions/{session.id}",
        headers=await auth_headers(stranger),
        json={"title": "Not mine"},
    )

    assert response.status_code == 404


async def test_rename_session_hidden_404_for_other_tenant(async_client):
    owner_tenant = await create_tenant()
    other_tenant = await create_tenant()
    owner = await create_user(role=Role.USER, tenant_id=owner_tenant)
    cross_tenant_user = await create_user(role=Role.USER, tenant_id=other_tenant)
    session = await _create_chat_session(owner.id, owner_tenant, "New Chat")

    response = await async_client.patch(
        f"/api/v1/ai-assistance/sessions/{session.id}",
        headers=await auth_headers(cross_tenant_user),
        json={"title": "Cross tenant"},
    )

    assert response.status_code == 404


async def test_rename_session_rejects_blank_and_overlong_titles(async_client):
    tenant_id = await create_tenant()
    user = await create_user(role=Role.USER, tenant_id=tenant_id)
    headers = await auth_headers(user)
    session = await _create_chat_session(user.id, tenant_id, "New Chat")

    blank = await async_client.patch(
        f"/api/v1/ai-assistance/sessions/{session.id}",
        headers=headers,
        json={"title": ""},
    )
    too_long = await async_client.patch(
        f"/api/v1/ai-assistance/sessions/{session.id}",
        headers=headers,
        json={"title": "x" * 201},
    )

    assert blank.status_code == 422
    assert too_long.status_code == 422
