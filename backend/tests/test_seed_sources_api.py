"""The seed source admin API — the HTTP seam (ADR 0056 Phase 4).

Pinned behaviors:

* the full file-source flow works over HTTP: create → upload (multipart) →
  approve → plan preview (GET) → apply (plan-only default, ``apply: true``
  executes) → the list view carries the current digest + latest run;
* permissions: a tenant ADMIN runs their own tenant overlays; a USER is
  403 at the door; another tenant's source (and its versions) is a hidden
  404;
* the central route-gate requires ``seed_source:read|manage|approve`` —
  an ADMIN's approve of their own tenant's digest works, the same call
  against a system-scope source is a hidden 404;
* re-applying a version surfaces a fresh run (the explicit re-apply door).
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.seed_source_model import SeedImportRun, SeedSource, SeedSourceVersion
from app.models.user_model import Role
from tests._auth_helpers import auth_headers, create_tenant, create_user


def _json_item(key: str, name: str) -> bytes:
    return json.dumps(
        {
            "metadata": {"version": "1.0.0"},
            "items": [{"seed_key": f"medication:{key}", "name": name}],
        },
        sort_keys=True,
    ).encode("utf-8")


@pytest.fixture(autouse=True)
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "HA_UPLOAD_DIR", str(tmp_path / "uploads"))
    return tmp_path


@pytest_asyncio.fixture
async def user_headers() -> dict[str, str]:
    """Bearer headers for a plain USER (no admin surface access)."""
    user = await create_user(role=Role.USER)
    return await auth_headers(user)


@pytest_asyncio.fixture
async def tenant_admin_headers():
    tenant_id = await create_tenant()
    user = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    headers = await auth_headers(user)
    return {"headers": headers, "tenant_id": tenant_id, "user_id": user.id}


async def _create_source(client, headers, **payload):
    body = {"kind": "file", "priority": 100, **payload}
    return await client.post("/api/v1/admin/seed-sources", headers=headers, json=body)


async def test_file_source_flow_over_http(async_client: AsyncClient, system_admin_headers):
    headers = system_admin_headers
    add = await _create_source(client=async_client, headers=headers)
    assert add.status_code == 201, add.text
    source = add.json()
    assert source["kind"] == "file"
    assert source["scope"] == "system"
    assert source["enabled"] is True

    upload = await async_client.post(
        f"/api/v1/admin/seed-sources/{source['id']}/upload",
        headers=headers,
        files={
            "file": (
                "medications.json",
                _json_item("api-flow-med", "API Flow Med"),
                "application/json",
            )
        },
    )
    assert upload.status_code == 200, upload.text
    version = upload.json()
    assert version["status"] == "pending"

    approve = await async_client.post(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/approve", headers=headers
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "approved"

    plan = await async_client.get(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/plan", headers=headers
    )
    assert plan.status_code == 200, plan.text
    plan_doc = plan.json()
    assert plan_doc["pack"]["priority"] == 100
    entry = next(
        e
        for es in plan_doc["stages"].values()
        for e in es
        if e["seed_key"] == "medication:api-flow-med"
    )
    assert entry["action"] == "create"

    preview = await async_client.post(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/apply", headers=headers, json={}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["mode"] == "plan", "apply defaults to the read-only plan preview"

    applied = await async_client.post(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/apply",
        headers=headers,
        json={"apply": True},
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["mode"] == "apply"
    assert next(iter(body["statuses"].values())) == "ok"

    listed = await async_client.get("/api/v1/admin/seed-sources", headers=headers)
    assert listed.status_code == 200
    mine = next(s for s in listed.json() if s["id"] == source["id"])
    assert mine["current_version_id"] == version["id"]
    assert mine["versions"][0]["is_current"] is True
    assert mine["versions"][0]["latest_run"]["status"] == "ok"

    again = await async_client.post(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/apply",
        headers=headers,
        json={"apply": True},
    )
    assert again.status_code == 200, again.text
    assert next(iter(again.json()["statuses"].values())) == "ok", "the re-apply door runs"

    async with AsyncSessionLocal() as db:
        run_count = len(
            (
                await db.execute(
                    select(SeedImportRun).where(
                        SeedImportRun.source_version_id == uuid.UUID(version["id"]),
                        SeedImportRun.mode == "apply",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert run_count >= 2, "each explicit apply records its own run"

    await _cleanup_source(source["id"], version["id"], "medication:api-flow-med")


@pytest.mark.asyncio
async def test_runs_history_endpoints(async_client: AsyncClient, system_admin_headers):
    """The per-source runs surface (the Sources UI's history view): the list
    carries mode/status/timing/stats/digest; the detail adds the recorded
    plan + per-item outcomes; another tenant's runs are the hidden 404."""
    headers = system_admin_headers
    add = await _create_source(client=async_client, headers=headers)
    source = add.json()
    upload = await async_client.post(
        f"/api/v1/admin/seed-sources/{source['id']}/upload",
        headers=headers,
        files={
            "file": (
                "medications.json",
                _json_item("api-runs-med", "API Runs Med"),
                "application/json",
            )
        },
    )
    version = upload.json()
    await async_client.post(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/approve", headers=headers
    )
    await async_client.post(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/apply", headers=headers, json={}
    )
    apply_run = await async_client.post(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/apply",
        headers=headers,
        json={"apply": True},
    )
    assert apply_run.status_code == 200, apply_run.text

    listed = await async_client.get(
        f"/api/v1/admin/seed-sources/{source['id']}/runs", headers=headers
    )
    assert listed.status_code == 200, listed.text
    runs = listed.json()
    assert len(runs) >= 2
    assert [r["mode"] for r in runs] == ["apply", "plan"], "newest first"
    assert all(r["digest"] == version["digest"] for r in runs)
    assert runs[0]["status"] == "ok"
    assert runs[0]["stats"], "the run summary carries its stats"

    detail = await async_client.get(
        f"/api/v1/admin/seed-sources/{source['id']}/runs/{runs[0]['id']}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["plan"], "the run detail carries the recorded plan document"
    items = body["items"]
    assert items, "the run detail carries per-item outcomes"
    entry = next(i for i in items if i["seed_key"] == "medication:api-runs-med")
    assert entry["action"] in {"create", "update", "noop"}

    missing = await async_client.get(
        f"/api/v1/admin/seed-sources/{source['id']}/runs/{uuid.uuid4()}", headers=headers
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "RUN_NOT_FOUND"

    garbage = await async_client.get(
        f"/api/v1/admin/seed-sources/{source['id']}/runs/not-a-uuid", headers=headers
    )
    assert garbage.status_code == 404

    await _cleanup_source(source["id"], version["id"], "medication:api-runs-med")


@pytest.mark.asyncio
async def test_runs_are_scoped_per_actor(async_client: AsyncClient, tenant_admin_headers):
    """A tenant admin cannot read another source's run history (hidden 404),
    mirroring the rest of the lane's row-level scoping."""
    headers = tenant_admin_headers["headers"]
    add = await _create_source(client=async_client, headers=headers, scope="tenant", priority=200)
    assert add.status_code == 201, add.text
    source = add.json()

    foreign = await async_client.get(
        f"/api/v1/admin/seed-sources/{uuid.uuid4()}/runs", headers=headers
    )
    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "SOURCE_NOT_FOUND"

    await _cleanup_source(source["id"], None, None)


@pytest.mark.asyncio
async def test_upload_over_the_body_cap_is_a_typed_413(
    async_client: AsyncClient, system_admin_headers, monkeypatch
):
    """The upload door reads the body under the upload cap — an oversized
    body is refused with ``UPLOAD_TOO_LARGE``/413 before any parsing, never
    read whole into memory."""
    from app.core.config import get_settings
    from app.core.config import settings as app_settings

    # Patch every Settings instance get_settings() may hand out: an earlier
    # test may have cleared the lru_cache (test_mcp_client), leaving the
    # module-level `settings` object distinct from the cached one.
    monkeypatch.setattr(app_settings, "MAX_UPLOAD_SIZE", 1)  # 1 MB
    cached_settings = get_settings()
    if cached_settings is not app_settings:
        monkeypatch.setattr(cached_settings, "MAX_UPLOAD_SIZE", 1)
    headers = system_admin_headers
    add = await _create_source(client=async_client, headers=headers)
    assert add.status_code == 201
    source = add.json()

    oversized = b"x" * (1024 * 1024 + 1)
    upload = await async_client.post(
        f"/api/v1/admin/seed-sources/{source['id']}/upload",
        headers=headers,
        files={"file": ("medications.json", oversized, "application/json")},
    )
    assert upload.status_code == 413, upload.text
    assert upload.json()["detail"]["code"] == "UPLOAD_TOO_LARGE"

    versions = await async_client.get(
        f"/api/v1/admin/seed-sources/{source['id']}/versions", headers=headers
    )
    assert versions.json() == [], "the refused upload registered nothing"

    await _cleanup_source(source["id"], None, None)


async def _cleanup_source(source_id: str, version_id: str, seed_key: str):
    from app.models.fhir.medication import MedicationCatalog
    from app.models.seed_source_model import SeedItemProvenance

    async with AsyncSessionLocal() as db:
        if seed_key is not None:
            row = (
                await db.execute(
                    select(MedicationCatalog).where(MedicationCatalog.seed_key == seed_key)
                )
            ).scalar_one_or_none()
            if row is not None:
                prov = (
                    await db.execute(
                        select(SeedItemProvenance).where(
                            SeedItemProvenance.entity_type == "medication",
                            SeedItemProvenance.entity_id == row.id,
                        )
                    )
                ).scalar_one_or_none()
                if prov is not None:
                    await db.delete(prov)
                await db.delete(row)
                await db.commit()
        version = await db.get(SeedSourceVersion, uuid.UUID(version_id)) if version_id else None
        if version is not None:
            await db.delete(version)
            await db.commit()
        source = await db.get(SeedSource, uuid.UUID(source_id))
        if source is not None:
            await db.delete(source)
            await db.commit()


async def test_user_role_is_forbidden(async_client: AsyncClient, user_headers):
    response = await async_client.get("/api/v1/admin/seed-sources", headers=user_headers)
    assert response.status_code == 403


async def test_tenant_admin_runs_own_overlays_and_hidden_404_cross_tenant(
    async_client: AsyncClient, tenant_admin_headers
):
    headers = tenant_admin_headers["headers"]
    tenant_id = tenant_admin_headers["tenant_id"]

    add = await _create_source(
        client=async_client,
        headers=headers,
        priority=250,
        scope="tenant",
    )
    assert add.status_code == 201, add.text
    source = add.json()
    assert source["scope"] == "tenant"
    assert source["tenant_id"] == str(tenant_id)

    upload = await async_client.post(
        f"/api/v1/admin/seed-sources/{source['id']}/upload",
        headers=headers,
        files={
            "file": (
                "medications.json",
                _json_item("tenant-api-med", "Tenant API Med"),
                "application/json",
            )
        },
    )
    assert upload.status_code == 200, upload.text
    version = upload.json()
    assert version["pack_scope"] == "tenant"
    assert version["pack_priority"] == 250

    approve = await async_client.post(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/approve", headers=headers
    )
    assert approve.status_code == 200, approve.text
    applied = await async_client.post(
        f"/api/v1/admin/seed-sources/versions/{version['id']}/apply",
        headers=headers,
        json={"apply": True},
    )
    assert applied.status_code == 200, applied.text
    assert next(iter(applied.json()["statuses"].values())) == "ok"

    listed = await async_client.get("/api/v1/admin/seed-sources", headers=headers)
    mine = [s for s in listed.json() if s["id"] == source["id"]]
    assert mine, "the tenant admin sees their own overlay"
    assert all(
        s["scope"] == "tenant" and s["tenant_id"] == str(tenant_id) for s in listed.json()
    ), "a tenant admin never sees the system lane or other tenants"

    other_admin_tenant = await create_tenant()
    _other_user, other_headers = await _other_tenant_admin(other_admin_tenant)
    foreign = await async_client.post(
        "/api/v1/admin/seed-sources",
        headers=other_headers,
        json={"kind": "file", "priority": 250, "scope": "tenant"},
    )
    assert foreign.status_code == 201
    foreign_id = foreign.json()["id"]

    peek = await async_client.get(
        f"/api/v1/admin/seed-sources/{foreign_id}/versions", headers=headers
    )
    assert peek.status_code == 404, "cross-tenant reads are hidden 404s"
    retire = await async_client.post(
        f"/api/v1/admin/seed-sources/{foreign_id}/retire", headers=headers
    )
    assert retire.status_code == 404, "cross-tenant mutations are hidden 404s"

    await _cleanup_source(source["id"], version["id"], "medication:tenant-api-med")
    await _cleanup_source(foreign_id, None, None)


async def _other_tenant_admin(tenant_id):
    user = await create_user(role=Role.ADMIN, tenant_id=tenant_id)
    return user, await auth_headers(user)


async def test_system_scope_hidden_from_tenant_admins(
    async_client: AsyncClient, system_admin_headers, tenant_admin_headers
):
    sys_headers = system_admin_headers
    add = await _create_source(client=async_client, headers=sys_headers)
    assert add.status_code == 201
    source = add.json()

    peek = await async_client.get(
        f"/api/v1/admin/seed-sources/{source['id']}/versions",
        headers=tenant_admin_headers["headers"],
    )
    assert peek.status_code == 404

    await _cleanup_source(source["id"], None, None)
