"""Unified instance search tests — Phase 2.

Covers the registry-driven ``search_instances`` dispatcher (type filter,
tenant scope, patient scope, unknown-type tolerance) and the
``GET /instances/search`` HTTP surface.

The security cases (patient-access 403, USER tenant-wide 403, cross-tenant
404) are the ones that gate this phase's merge — see plan §4.
"""

import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.document_model import DocumentModel
from app.models.enums import Gender
from app.models.fhir.medication import Medication
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.instance_search_service import search_instances


async def _tenant():
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        await db.commit()
    return tenant_id


async def _seed_user(tenant_id, role="USER"):
    """Create a real User row so patient.user_id / document.owner_id FKs hold."""
    uid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            UserModel(
                id=uid,
                tenant_id=tenant_id,
                email=f"user-{uid.hex[:8]}@test.local",
                role=role,
                hashed_password="x",
            )
        )
        await db.commit()
    return uid


def _headers(tenant_id: uuid.UUID, role: str, user_id: uuid.UUID | None = None):
    token = create_access_token(
        {
            "sub": f"{role.lower()}@test.local",
            "user_id": str(user_id or uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": role,
        }
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_patient(tenant_id, user_id=None):
    pid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=pid,
                tenant_id=tenant_id,
                user_id=user_id,
                name={"given": ["Test"], "family": "Patient"},
                gender=Gender.MALE,
            )
        )
        await db.commit()
    return pid


async def _seed_medication(tenant_id, patient_id, text):
    async with AsyncSessionLocal() as db:
        db.add(
            Medication(
                tenant_id=tenant_id,
                patient_id=patient_id,
                code={"text": text},
                subject={"reference": f"Patient/{patient_id}"},
            )
        )
        await db.commit()


async def _seed_document(tenant_id, patient_id, filename, owner_id):
    async with AsyncSessionLocal() as db:
        db.add(
            DocumentModel(
                tenant_id=tenant_id,
                patient_id=patient_id,
                owner_id=owner_id,
                filename=filename,
                file_path=f"/tmp/{filename}",
            )
        )
        await db.commit()


# ---------------------------------------------------------------------------
# search_instances dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_finds_medication_by_text():
    token = f"unique-{uuid.uuid4().hex[:6]}"
    tenant_id = await _tenant()
    patient_id = await _seed_patient(tenant_id)
    await _seed_medication(tenant_id, patient_id, f"{token} Syrup")
    async with AsyncSessionLocal() as db:
        hits = await search_instances(db, tenant_id, patient_id, token, limit_per_type=5)
    meds = [h for h in hits if h["type"] == "medication"]
    assert len(meds) == 1
    assert token in meds[0]["label"]
    assert meds[0]["id"]


@pytest.mark.asyncio
async def test_dispatcher_tenant_scoped():
    """A row in another tenant is never surfaced."""
    token = f"secret-{uuid.uuid4().hex[:6]}"
    caller_tenant = await _tenant()
    other_tenant = await _tenant()
    other_patient = await _seed_patient(other_tenant)
    await _seed_medication(other_tenant, other_patient, f"{token} Pill")
    async with AsyncSessionLocal() as db:
        hits = await search_instances(
            db, caller_tenant, None, token, limit_per_type=5
        )
    assert hits == []


@pytest.mark.asyncio
async def test_dispatcher_patient_filter():
    """patient_id scope returns only that patient's rows; None returns all in tenant."""
    token = f"scoped-{uuid.uuid4().hex[:6]}"
    tenant_id = await _tenant()
    p1 = await _seed_patient(tenant_id)
    p2 = await _seed_patient(tenant_id)
    await _seed_medication(tenant_id, p1, f"{token} A")
    await _seed_medication(tenant_id, p2, f"{token} B")

    async with AsyncSessionLocal() as db:
        scoped = await search_instances(db, tenant_id, p1, token)
        all_tenant = await search_instances(db, tenant_id, None, token)
    assert len([h for h in scoped if h["type"] == "medication"]) == 1
    assert len([h for h in all_tenant if h["type"] == "medication"]) == 2


@pytest.mark.asyncio
async def test_dispatcher_type_filter_restricts():
    token = f"mixed-{uuid.uuid4().hex[:6]}"
    tenant_id = await _tenant()
    owner = await _seed_user(tenant_id)
    patient_id = await _seed_patient(tenant_id)
    await _seed_medication(tenant_id, patient_id, f"{token} Med")
    await _seed_document(tenant_id, patient_id, f"{token}.pdf", owner)
    async with AsyncSessionLocal() as db:
        hits = await search_instances(
            db, tenant_id, patient_id, token, types=["medication"]
        )
    assert {h["type"] for h in hits} == {"medication"}


@pytest.mark.asyncio
async def test_dispatcher_unknown_type_ignored():
    tenant_id = await _tenant()
    async with AsyncSessionLocal() as db:
        hits = await search_instances(
            db, tenant_id, None, "anything", types=["nonexistent"]
        )
    assert hits == []


@pytest.mark.asyncio
async def test_dispatcher_hits_multiple_types():
    token = f"multi-{uuid.uuid4().hex[:6]}"
    tenant_id = await _tenant()
    owner = await _seed_user(tenant_id)
    patient_id = await _seed_patient(tenant_id)
    await _seed_medication(tenant_id, patient_id, f"{token} Med")
    await _seed_document(tenant_id, patient_id, f"{token}.pdf", owner)
    async with AsyncSessionLocal() as db:
        hits = await search_instances(db, tenant_id, patient_id, token)
    types_hit = {h["type"] for h in hits}
    assert {"medication", "document"} <= types_hit


# ---------------------------------------------------------------------------
# GET /instances/search — security chokepoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_admin_with_patient_scope_200(async_client):
    token = f"ep-{uuid.uuid4().hex[:6]}"
    tenant_id = await _tenant()
    patient_id = await _seed_patient(tenant_id)
    await _seed_medication(tenant_id, patient_id, f"{token} Med")
    resp = await async_client.get(
        f"/api/v1/instances/search?q={token}&patient_id={patient_id}",
        headers=_headers(tenant_id, "ADMIN"),
    )
    assert resp.status_code == 200, resp.text
    types_hit = {h["type"] for h in resp.json()["results"]}
    assert "medication" in types_hit


@pytest.mark.asyncio
async def test_endpoint_admin_tenant_wide_200(async_client):
    """ADMIN/SYSTEM_ADMIN may browse tenant-wide (no patient_id)."""
    token = f"tw-{uuid.uuid4().hex[:6]}"
    tenant_id = await _tenant()
    patient_id = await _seed_patient(tenant_id)
    await _seed_medication(tenant_id, patient_id, f"{token} Med")
    resp = await async_client.get(
        f"/api/v1/instances/search?q={token}",
        headers=_headers(tenant_id, "ADMIN"),
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_endpoint_user_tenant_wide_403(async_client):
    """A USER must NOT browse tenant-wide (defense-in-depth against enumeration)."""
    tenant_id = await _tenant()
    resp = await async_client.get(
        "/api/v1/instances/search?q=something",
        headers=_headers(tenant_id, "USER"),
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_endpoint_user_own_patient_200(async_client):
    """A USER linked to the patient (matching user_id) may search it."""
    token = f"own-{uuid.uuid4().hex[:6]}"
    tenant_id = await _tenant()
    user_id = await _seed_user(tenant_id)
    patient_id = await _seed_patient(tenant_id, user_id=user_id)
    await _seed_medication(tenant_id, patient_id, f"{token} Med")
    resp = await async_client.get(
        f"/api/v1/instances/search?q={token}&patient_id={patient_id}",
        headers=_headers(tenant_id, "USER", user_id=user_id),
    )
    assert resp.status_code == 200, resp.text
    assert any(h["type"] == "medication" for h in resp.json()["results"])


@pytest.mark.asyncio
async def test_endpoint_user_other_patient_403(async_client):
    """A USER not linked to the patient is denied (403)."""
    tenant_id = await _tenant()
    patient_owner = await _seed_user(tenant_id)
    requester = await _seed_user(tenant_id)
    patient_id = await _seed_patient(tenant_id, user_id=patient_owner)
    resp = await async_client.get(
        f"/api/v1/instances/search?q=test&patient_id={patient_id}",
        headers=_headers(tenant_id, "USER", user_id=requester),
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_endpoint_cross_tenant_patient_404(async_client):
    """A patient_id from another tenant surfaces as 404 (no leak)."""
    tenant_a = await _tenant()
    tenant_b = await _tenant()
    patient_b = await _seed_patient(tenant_b)
    resp = await async_client.get(
        f"/api/v1/instances/search?q=test&patient_id={patient_b}",
        headers=_headers(tenant_a, "ADMIN"),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_endpoint_short_query_422(async_client):
    tenant_id = await _tenant()
    patient_id = await _seed_patient(tenant_id)
    resp = await async_client.get(
        f"/api/v1/instances/search?q=a&patient_id={patient_id}",
        headers=_headers(tenant_id, "ADMIN"),
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_endpoint_types_filter_restricts(async_client):
    token = f"tf-{uuid.uuid4().hex[:6]}"
    tenant_id = await _tenant()
    owner = await _seed_user(tenant_id)
    patient_id = await _seed_patient(tenant_id)
    await _seed_medication(tenant_id, patient_id, f"{token} Med")
    await _seed_document(tenant_id, patient_id, f"{token}.pdf", owner)
    resp = await async_client.get(
        f"/api/v1/instances/search?q={token}&patient_id={patient_id}&types=medication",
        headers=_headers(tenant_id, "ADMIN"),
    )
    assert resp.status_code == 200, resp.text
    types_hit = {h["type"] for h in resp.json()["results"]}
    assert types_hit == {"medication"}
