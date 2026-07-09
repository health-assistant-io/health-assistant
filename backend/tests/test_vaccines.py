"""Vaccine catalog + patient immunization tests — Phase 5.

Covers: catalog CRUD + RBAC, patient-instance CRUD + patient-access scoping,
FHIR projections (VaccineCatalog→Medication, PatientImmunization→Immunization
both pass ``assert_valid_fhir``), the FHIR R4 ``/fhir/R4/Immunization`` facade
search, and the CatalogRegistry registration (vaccines appear in /catalogs).
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.fhir.patient import Patient
from app.models.fhir.vaccine import PatientImmunization, VaccineCatalog
from app.models.tenant_model import TenantModel
from app.services.fhir_helpers import assert_valid_fhir


async def _tenant_and_headers(role="ADMIN"):
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="V", slug=f"v-{tenant_id}"))
        await db.commit()
    token = create_access_token(
        {
            "sub": f"{role.lower()}@test.local",
            "user_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "role": role,
        }
    )
    return tenant_id, {"Authorization": f"Bearer {token}"}


async def _make_patient(tenant_id):
    async with AsyncSessionLocal() as db:
        p = Patient(
            tenant_id=tenant_id,
            name={"family": "Test", "given": ["P"]},
            gender="UNKNOWN",
        )
        db.add(p)
        await db.commit()
        await db.refresh(p)
    return p.id


# ---------------------------------------------------------------------------
# FHIR projections (model-level)
# ---------------------------------------------------------------------------


def test_vaccine_catalog_to_fhir_validates():
    v = VaccineCatalog(slug="mmr", name="MMR", code="03", coding_system="cvx")
    fhir = assert_valid_fhir(v)
    assert fhir["resourceType"] == "Medication"
    assert fhir["code"]["text"] == "MMR"
    assert fhir["code"]["coding"][0]["code"] == "03"


def test_patient_immunization_to_fhir_validates():
    pid = uuid.uuid4()
    imm = PatientImmunization(
        patient_id=pid,
        vaccine_code={
            "text": "MMR",
            "coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "03"}],
        },
        administered_at=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
        dose_number="1",
        lot_number="L123",
        status="completed",
    )
    fhir = assert_valid_fhir(imm)
    assert fhir["resourceType"] == "Immunization"
    assert fhir["status"] == "completed"
    assert fhir["patient"]["reference"] == f"Patient/{pid}"
    assert fhir["occurrenceDateTime"].endswith("Z")
    assert fhir["lotNumber"] == "L123"
    assert fhir["protocolApplied"][0]["doseNumberPositiveInt"] == 1


# ---------------------------------------------------------------------------
# Catalog CRUD (domain endpoints)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vaccine_catalog_crud(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    suffix = uuid.uuid4().hex[:8]
    create = await async_client.post(
        "/api/v1/vaccines/catalog",
        json={"slug": f"vc-{suffix}", "name": f"Vaccine {suffix}", "code": "99"},
        headers=headers,
    )
    assert create.status_code == 200, create.text
    vid = create.json()["id"]

    get = await async_client.get(f"/api/v1/vaccines/catalog/{vid}", headers=headers)
    assert get.status_code == 200
    assert get.json()["name"] == f"Vaccine {suffix}"

    put = await async_client.put(
        f"/api/v1/vaccines/catalog/{vid}",
        json={"description": "updated"},
        headers=headers,
    )
    assert put.status_code == 200
    assert put.json()["description"] == "updated"

    delete = await async_client.delete(
        f"/api/v1/vaccines/catalog/{vid}", headers=headers
    )
    assert delete.status_code == 200


@pytest.mark.asyncio
async def test_vaccine_catalog_user_creates_user_scope(async_client):
    """Phase A: a USER may create a vaccine catalog entry; it lands in
    user-scope (visible to the tenant, owned by the creator)."""
    _, headers = await _tenant_and_headers("USER")
    resp = await async_client.post(
        "/api/v1/vaccines/catalog",
        json={"slug": "u", "name": "U"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_vaccine_catalog_admin_cannot_delete_global(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    async with AsyncSessionLocal() as db:
        v = VaccineCatalog(
            slug=f"g-{uuid.uuid4().hex[:6]}", name="Global V", tenant_id=None
        )
        db.add(v)
        await db.commit()
        await db.refresh(v)
        gid = str(v.id)
    resp = await async_client.delete(f"/api/v1/vaccines/catalog/{gid}", headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Patient immunization instances
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_immunization_crud(async_client):
    tenant_id, headers = await _tenant_and_headers("ADMIN")
    pid = await _make_patient(tenant_id)

    create = await async_client.post(
        f"/api/v1/vaccines/patient/{pid}",
        json={
            "vaccine_code": {
                "text": "MMR",
                "coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "03"}],
            },
            "administered_at": "2026-01-15T10:00:00Z",
            "dose_number": "1",
            "lot_number": "L123",
        },
        headers=headers,
    )
    assert create.status_code == 200, create.text
    iid = create.json()["id"]

    get = await async_client.get(f"/api/v1/vaccines/{iid}", headers=headers)
    assert get.status_code == 200
    assert get.json()["lot_number"] == "L123"

    listing = await async_client.get(f"/api/v1/vaccines/patient/{pid}", headers=headers)
    assert listing.status_code == 200
    assert any(i["id"] == iid for i in listing.json())

    delete = await async_client.delete(f"/api/v1/vaccines/{iid}", headers=headers)
    assert delete.status_code == 200


@pytest.mark.asyncio
async def test_patient_immunization_cross_patient_denied(async_client):
    tenant_id, headers = await _tenant_and_headers("USER")
    other_pid = await _make_patient(tenant_id)  # patient not linked to this USER
    resp = await async_client.get(
        f"/api/v1/vaccines/patient/{other_pid}", headers=headers
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# FHIR R4 facade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_immunization_facade_search(async_client):
    tenant_id, headers = await _tenant_and_headers("ADMIN")
    pid = await _make_patient(tenant_id)
    async with AsyncSessionLocal() as db:
        db.add(
            PatientImmunization(
                patient_id=pid,
                tenant_id=tenant_id,
                vaccine_code={"text": "Flu Shot"},
                administered_at=datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc),
                status="completed",
            )
        )
        await db.commit()

    resp = await async_client.get(
        f"/api/v1/fhir/R4/Immunization?patient={pid}", headers=headers
    )
    assert resp.status_code == 200, resp.text
    bundle = resp.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["total"] >= 1
    assert any(e["resource"]["resourceType"] == "Immunization" for e in bundle["entry"])


# ---------------------------------------------------------------------------
# CatalogRegistry integration
# ---------------------------------------------------------------------------


def test_vaccine_registered_in_catalog_registry():
    from app.catalogs import CatalogRegistry

    assert "vaccine" in CatalogRegistry.types()
    desc = CatalogRegistry.get("vaccine")
    assert desc.model.__name__ == "VaccineCatalog"
    assert desc.has_concept_link


@pytest.mark.asyncio
async def test_vaccine_in_catalogs_search(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        db.add(
            VaccineCatalog(
                slug=f"sch-{suffix}", name=f"ZebraVax {suffix}", tenant_id=None
            )
        )
        await db.commit()
    resp = await async_client.get(
        f"/api/v1/catalogs/search?q=ZebraVax%20{suffix}&types=vaccine",
        headers=headers,
    )
    assert resp.status_code == 200
    hits = resp.json()["results"]
    assert any(h["type"] == "vaccine" for h in hits)


# ---------------------------------------------------------------------------
# Migration: tables present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vaccine_tables_exist():
    from sqlalchemy import inspect as sa_inspect

    async with AsyncSessionLocal() as db:
        conn = await db.connection()
        names = await conn.run_sync(lambda c: set(sa_inspect(c).get_table_names()))
    assert "vaccine_catalog" in names
    assert "patient_immunizations" in names
