"""Catalog audit log — Phase B.

Every catalog CRUD operation must append an append-only ``CatalogAuditLog`` row
(who / what / operation / scope-change / snapshot). Exercises the meta-layer
write routes for ``medication`` (the simplest FHIR-gated catalog) end-to-end:

- create  -> audit row (operation=create, to_scope=tenant, item_name captured).
- update  -> audit row (operation=update).
- delete  -> audit row (operation=delete, item_name snapshot preserved).
- promote -> audit row (operation=promote, from_scope + to_scope captured).
- GET /catalogs/{type}/{id}/history returns the trail, newest-first.
- audit recording is best-effort: a failure never aborts the parent write
  (verified by patching the recorder to raise and asserting the create still
  succeeds).
"""

import uuid
from typing import Dict, Tuple

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.catalog_audit_model import CatalogAuditLog
from app.models.tenant_model import TenantModel


async def _tenant_and_headers(
    role: str = "ADMIN",
) -> Tuple[uuid.UUID, Dict[str, str], uuid.UUID]:
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Audit", slug=f"audit-{tenant_id}"))
        await db.commit()
    token = create_access_token(
        {
            "sub": f"{role.lower()}@audit.test",
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "role": role,
        }
    )
    return tenant_id, {"Authorization": f"Bearer {token}"}, user_id


async def _audit_rows(catalog_type: str, item_id: str) -> list[CatalogAuditLog]:
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(CatalogAuditLog)
            .where(
                CatalogAuditLog.catalog_type == catalog_type,
                CatalogAuditLog.item_id == item_id,
            )
            .order_by(CatalogAuditLog.created_at.asc())
        )
        return list(res.scalars().all())


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_appends_audit_row(async_client):
    _, headers, user_id = await _tenant_and_headers("ADMIN")
    resp = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": "Audited Drug"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    item_id = resp.json()["id"]

    rows = await _audit_rows("medication", item_id)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.operation == "create"
    assert row.item_name == "Audited Drug"
    assert row.to_scope == "tenant"
    assert row.from_scope is None
    assert str(row.user_id) == str(user_id)
    assert row.user_email == "admin@audit.test"


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_appends_audit_row(async_client):
    _, headers, _ = await _tenant_and_headers("ADMIN")
    create = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": "Pre Update"},
        headers=headers,
    )
    item_id = create.json()["id"]

    update = await async_client.put(
        f"/api/v1/catalogs/medication/{item_id}",
        json={"name": "Post Update"},
        headers=headers,
    )
    assert update.status_code == 200, update.text

    rows = await _audit_rows("medication", item_id)
    ops = [r.operation for r in rows]
    assert ops == ["create", "update"], ops
    update_row = rows[-1]
    assert update_row.item_name == "Post Update"


# ---------------------------------------------------------------------------
# delete (snapshot preserved)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_appends_audit_row_with_snapshot(async_client):
    _, headers, _ = await _tenant_and_headers("ADMIN")
    create = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": "Doomed Drug"},
        headers=headers,
    )
    item_id = create.json()["id"]

    delete = await async_client.delete(
        f"/api/v1/catalogs/medication/{item_id}", headers=headers
    )
    assert delete.status_code == 200, delete.text

    rows = await _audit_rows("medication", item_id)
    ops = [r.operation for r in rows]
    assert ops == ["create", "delete"], ops
    delete_row = rows[-1]
    # Snapshot of the deleted item's name is preserved even though the row is gone.
    assert delete_row.item_name == "Doomed Drug"


# ---------------------------------------------------------------------------
# promote (scope change recorded)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_appends_audit_row_with_scope_change(async_client):
    _, headers, _ = await _tenant_and_headers("SYSTEM_ADMIN")
    create = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": "Promote Me"},
        headers=headers,
    )
    # SYSTEM_ADMIN create -> system scope.
    assert create.json()["scope"] == "system"
    item_id = create.json()["id"]

    # Demote system -> tenant (SYSTEM_ADMIN only).
    demote = await async_client.post(
        f"/api/v1/catalogs/medication/{item_id}/promote",
        json={"scope": "tenant"},
        headers=headers,
    )
    assert demote.status_code == 200, demote.text

    rows = await _audit_rows("medication", item_id)
    ops = [r.operation for r in rows]
    assert ops == ["create", "promote"], ops
    promote_row = rows[-1]
    assert promote_row.from_scope == "system"
    assert promote_row.to_scope == "tenant"


# ---------------------------------------------------------------------------
# history endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_endpoint_returns_trail_newest_first(async_client):
    _, headers, _ = await _tenant_and_headers("ADMIN")
    create = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": "History Drug"},
        headers=headers,
    )
    item_id = create.json()["id"]
    await async_client.put(
        f"/api/v1/catalogs/medication/{item_id}",
        json={"name": "History Drug v2"},
        headers=headers,
    )

    hist = await async_client.get(
        f"/api/v1/catalogs/medication/{item_id}/history", headers=headers
    )
    assert hist.status_code == 200, hist.text
    trail = hist.json()["items"]
    assert len(trail) == 2, trail
    # Newest first.
    assert trail[0]["operation"] == "update"
    assert trail[1]["operation"] == "create"
    assert trail[0]["item_name"] == "History Drug v2"


@pytest.mark.asyncio
async def test_history_is_tenant_scoped(async_client):
    """A cross-tenant caller must not see another tenant's audit trail."""
    _, owner_headers, _ = await _tenant_and_headers("ADMIN")
    _, other_headers, _ = await _tenant_and_headers("ADMIN")
    create = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": "Private Drug"},
        headers=owner_headers,
    )
    item_id = create.json()["id"]

    hist = await async_client.get(
        f"/api/v1/catalogs/medication/{item_id}/history", headers=other_headers
    )
    # The item itself is tenant-scoped (invisible cross-tenant) -> 404.
    assert hist.status_code == 404, hist.text


# ---------------------------------------------------------------------------
# best-effort: audit failure never aborts the parent write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_failure_does_not_abort_create(async_client, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("audit DB is down")

    monkeypatch.setattr(
        "app.services.catalog_audit_service.record_from_obj", _boom
    )

    _, headers, _ = await _tenant_and_headers("ADMIN")
    resp = await async_client.post(
        "/api/v1/catalogs/medication",
        json={"name": "Audit-Resistant Drug"},
        headers=headers,
    )
    # The catalog write must succeed even though the audit recorder blew up.
    assert resp.status_code == 201, resp.text
