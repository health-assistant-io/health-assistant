"""API integration tests for /concepts and /concept-edges endpoints.

Exercises the full HTTP stack (auth, routing, validation, RBAC, tenancy)
using the ``async_client`` and ``system_admin_headers`` fixtures.
"""

import uuid

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def user_headers(async_client):
    """JWT headers for a regular USER role (read-only on concepts)."""
    from app.core.database import AsyncSessionLocal
    from app.core.security import create_access_token
    from app.models.tenant_model import TenantModel

    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            TenantModel(id=tenant_id, name="User Tenant", slug=f"user-{tenant_id}")
        )
        await session.commit()

    token = create_access_token(
        {
            "sub": "user@test.local",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": "USER",
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_create_and_get_concept(async_client, system_admin_headers):
    """SYSTEM_ADMIN can create a global concept and fetch it back."""
    slug = f"api-test-{uuid.uuid4().hex[:8]}"
    resp = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": slug,
            "name": "API Test Concept",
            "kind": "specialty",
        },
        headers=system_admin_headers,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["slug"] == slug
    assert created["primary_kind"] == "specialty"
    assert "specialty" in created["kinds"]
    assert created["tenant_id"] is None
    concept_id = created["id"]

    resp2 = await async_client.get(
        f"/api/v1/concepts/{concept_id}", headers=system_admin_headers
    )
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "API Test Concept"


@pytest.mark.asyncio
async def test_list_concepts_by_kind(async_client, system_admin_headers):
    """GET /concepts?kind=... filters by domain."""
    slug = f"list-test-{uuid.uuid4().hex[:8]}"
    await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": slug,
            "name": "List Test",
            "kind": "disease",
        },
        headers=system_admin_headers,
    )

    resp = await async_client.get(
        "/api/v1/concepts?kind=disease", headers=system_admin_headers
    )
    assert resp.status_code == 200
    results = resp.json()
    assert any(c["slug"] == slug for c in results)


@pytest.mark.asyncio
async def test_search_concepts_endpoint(async_client, system_admin_headers):
    """GET /concepts/search?q=... returns ranked trigram matches."""
    slug = f"search-{uuid.uuid4().hex[:8]}"
    await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": slug,
            "name": "SearchableSpecialty",
            "kind": "specialty",
            "aliases": ["findme"],
        },
        headers=system_admin_headers,
    )

    resp = await async_client.get(
        f"/api/v1/concepts/search?q=Searchable", headers=system_admin_headers
    )
    assert resp.status_code == 200
    assert any(c["slug"] == slug for c in resp.json())


@pytest.mark.asyncio
async def test_update_concept(async_client, system_admin_headers):
    """PUT /concepts/{id} updates mutable fields."""
    slug = f"upd-{uuid.uuid4().hex[:8]}"
    create = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": slug,
            "name": "Before",
            "kind": "specialty",
        },
        headers=system_admin_headers,
    )
    cid = create.json()["id"]

    resp = await async_client.put(
        f"/api/v1/concepts/{cid}",
        json={
            "name": "After",
            "description": "Updated",
            "color": "#ff0000",
        },
        headers=system_admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "After"
    assert resp.json()["description"] == "Updated"
    assert resp.json()["color"] == "#ff0000"


@pytest.mark.asyncio
async def test_delete_concept(async_client, system_admin_headers):
    """DELETE /concepts/{id} soft-deletes (or retires)."""
    slug = f"del-{uuid.uuid4().hex[:8]}"
    create = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": slug,
            "name": "Delete Me",
            "kind": "factor",
        },
        headers=system_admin_headers,
    )
    cid = create.json()["id"]

    resp = await async_client.delete(
        f"/api/v1/concepts/{cid}", headers=system_admin_headers
    )
    assert resp.status_code == 204

    resp2 = await async_client.get(
        f"/api/v1/concepts/{cid}", headers=system_admin_headers
    )
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_rbac_user_cannot_create(async_client, user_headers):
    """USER role gets 403 on POST /concepts."""
    resp = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": "forbidden",
            "name": "Forbidden",
            "kind": "disease",
        },
        headers=user_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_rbac_user_can_read(async_client, system_admin_headers, user_headers):
    """USER role can get concepts (read-only)."""
    slug = f"readonly-{uuid.uuid4().hex[:8]}"
    create = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": slug,
            "name": "Readable",
            "kind": "specialty",
        },
        headers=system_admin_headers,
    )
    cid = create.json()["id"]

    resp = await async_client.get(f"/api/v1/concepts/{cid}", headers=user_headers)
    assert resp.status_code == 200
    assert resp.json()["slug"] == slug


@pytest.mark.asyncio
async def test_create_and_list_edge(async_client, system_admin_headers):
    """POST /concept-edges creates an edge; GET lists it back."""
    s1 = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": f"edge-s1-{uuid.uuid4().hex[:8]}",
            "name": "S1",
            "kind": "specialty",
        },
        headers=system_admin_headers,
    )
    s2 = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": f"edge-s2-{uuid.uuid4().hex[:8]}",
            "name": "S2",
            "kind": "body_system",
        },
        headers=system_admin_headers,
    )
    sid, did = s1.json()["id"], s2.json()["id"]

    resp = await async_client.post(
        "/api/v1/concept-edges",
        json={
            "src_type": "concept",
            "src_id": sid,
            "dst_type": "concept",
            "dst_id": did,
            "relation": "EXAMINES",
            "source": "seed",
        },
        headers=system_admin_headers,
    )
    assert resp.status_code == 201, resp.text

    resp2 = await async_client.get(
        f"/api/v1/concept-edges?src_id={sid}", headers=system_admin_headers
    )
    assert resp2.status_code == 200
    assert len(resp2.json()) >= 1


@pytest.mark.asyncio
async def test_get_neighbors_endpoint(async_client, system_admin_headers):
    """GET /concepts/{id}/neighbors returns one-hop graph traversal."""
    s1 = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": f"nb-s1-{uuid.uuid4().hex[:8]}",
            "name": "NbSpecialty",
            "kind": "specialty",
        },
        headers=system_admin_headers,
    )
    s2 = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": f"nb-s2-{uuid.uuid4().hex[:8]}",
            "name": "NbSystem",
            "kind": "body_system",
        },
        headers=system_admin_headers,
    )
    sid, did = s1.json()["id"], s2.json()["id"]

    await async_client.post(
        "/api/v1/concept-edges",
        json={
            "src_type": "concept",
            "src_id": sid,
            "dst_type": "concept",
            "dst_id": did,
            "relation": "EXAMINES",
        },
        headers=system_admin_headers,
    )

    resp = await async_client.get(
        f"/api/v1/concepts/{sid}/neighbors", headers=system_admin_headers
    )
    assert resp.status_code == 200
    neighbors = resp.json()
    assert len(neighbors) >= 1
    assert any(n["endpoint"]["id"] == did for n in neighbors if n["endpoint"])


@pytest.mark.asyncio
async def test_invalid_kind_rejected(async_client, system_admin_headers):
    """Invalid kind value returns 400."""
    resp = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": "bad",
            "name": "Bad",
            "kind": "nonexistent_kind",
        },
        headers=system_admin_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_tenant_scoped_concept(async_client, system_admin_headers):
    """tenant_scoped=True creates a concept under the caller's tenant."""
    slug = f"tenant-{uuid.uuid4().hex[:8]}"
    resp = await async_client.post(
        "/api/v1/concepts",
        json={
            "slug": slug,
            "name": "Tenant Concept",
            "kind": "disease",
            "tenant_scoped": True,
        },
        headers=system_admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["tenant_id"] is not None
