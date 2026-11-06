"""Data-lifecycle API tests: RBAC, dry-run, typed errors, operator knobs."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.enums import ErasureStatus, Role
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from tests._auth_helpers import auth_headers, create_user


async def _add_patient(tenant_id: uuid.UUID, user_id: uuid.UUID | None = None) -> uuid.UUID:
    pid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=pid,
                tenant_id=tenant_id,
                user_id=user_id,
                name=[{"family": "Api"}],
                gender="OTHER",
            )
        )
        await db.commit()
    return pid


@pytest.mark.asyncio
async def test_user_dry_runs_their_own_patient(async_client):
    user = await create_user(role=Role.USER)
    headers = await auth_headers(user)
    patient_id = await _add_patient(user.tenant_id, user.id)

    response = await async_client.post(
        "/api/v1/data-lifecycle/erasures",
        json={"subject_type": "patient", "subject_id": str(patient_id), "dry_run": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == ErasureStatus.DRY_RUN_DONE.value
    assert body["report"]["counts"]["fhir_patients"] == 1
    assert body["report"]["subject"] == {"type": "patient", "id": str(patient_id)}
    assert body["report"]["export_offer"]["available"] is True

    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(select(Patient).where(Patient.id == patient_id))
        ).scalar_one_or_none() is not None, "a dry run must not delete"


@pytest.mark.asyncio
async def test_user_cannot_erase_another_users_patient(async_client):
    owner = await create_user(role=Role.USER)
    intruder = await create_user(role=Role.USER, tenant_id=owner.tenant_id)
    headers = await auth_headers(intruder)
    patient_id = await _add_patient(owner.tenant_id, owner.id)

    response = await async_client.post(
        "/api/v1/data-lifecycle/erasures",
        json={"subject_type": "patient", "subject_id": str(patient_id), "dry_run": True},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "erasure_forbidden"


@pytest.mark.asyncio
async def test_cross_tenant_subject_is_hidden_404(async_client):
    owner = await create_user(role=Role.USER)
    other = await create_user(role=Role.USER)
    headers = await auth_headers(other)
    patient_id = await _add_patient(owner.tenant_id, owner.id)

    response = await async_client.post(
        "/api/v1/data-lifecycle/erasures",
        json={"subject_type": "patient", "subject_id": str(patient_id), "dry_run": True},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "erasure_subject_not_found"


@pytest.mark.asyncio
async def test_admin_delete_patient_endpoint_runs_the_walker(async_client):
    admin = await create_user(role=Role.ADMIN)
    headers = await auth_headers(admin)
    patient_id = await _add_patient(admin.tenant_id)

    response = await async_client.delete(f"/api/v1/patients/{patient_id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["erasure_id"]

    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(select(Patient).where(Patient.id == patient_id))
        ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_self_account_delete(async_client):
    user = await create_user(role=Role.USER)
    headers = await auth_headers(user)

    response = await async_client.delete("/api/v1/users/me", headers=headers)
    assert response.status_code == 200, response.text
    async with AsyncSessionLocal() as db:
        assert (
            await db.execute(select(UserModel).where(UserModel.id == user.id))
        ).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_system_admin_can_dry_run_a_tenant_erasure(async_client):
    tenant = TenantModel(name="Api tenant", slug=f"api-{uuid.uuid4().hex[:10]}")
    async with AsyncSessionLocal() as db:
        db.add(tenant)
        await db.commit()
    sysadmin = await create_user(role=Role.SYSTEM_ADMIN)
    headers = await auth_headers(sysadmin)

    response = await async_client.post(
        "/api/v1/data-lifecycle/erasures",
        json={"subject_type": "tenant", "subject_id": str(tenant.id), "dry_run": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["report"]["subject"] == {
        "type": "tenant",
        "id": str(tenant.id),
    }
    async with AsyncSessionLocal() as db:
        refreshed = (
            await db.execute(select(TenantModel).where(TenantModel.id == tenant.id))
        ).scalar_one_or_none()
        assert refreshed is not None
        await db.execute(
            TenantModel.__table__.delete().where(TenantModel.__table__.c.id == tenant.id)
        )
        await db.commit()


@pytest.mark.asyncio
async def test_retention_policy_reads_for_admin_edits_only_for_system_admin(async_client):
    admin = await create_user(role=Role.ADMIN)
    admin_headers = await auth_headers(admin)
    listing = await async_client.get(
        "/api/v1/data-lifecycle/retention-policies", headers=admin_headers
    )
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert any(row["scope"] == "class" for row in rows)
    target = next(row for row in rows if row["scope_value"] == "ai_call_logs")

    denied = await async_client.patch(
        f"/api/v1/data-lifecycle/retention-policies/{target['id']}",
        json={"days": 42},
        headers=admin_headers,
    )
    assert denied.status_code == 403

    sysadmin = await create_user(role=Role.SYSTEM_ADMIN)
    sys_headers = await auth_headers(sysadmin)
    edited = await async_client.patch(
        f"/api/v1/data-lifecycle/retention-policies/{target['id']}",
        json={"days": 42},
        headers=sys_headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["days"] == 42

    restored = await async_client.patch(
        f"/api/v1/data-lifecycle/retention-policies/{target['id']}",
        json={"days": target["days"]},
        headers=sys_headers,
    )
    assert restored.status_code == 200
    assert restored.json()["days"] == target["days"]
