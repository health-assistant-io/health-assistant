"""Phase 6 tests — FHIR R4 ``CodeSystem`` + ``ValueSet`` facade resources.

Covers:
- ``GET /fhir/R4/CodeSystem/ha-diseases`` returns a valid FHIR CodeSystem
  publishing the disease concepts with ICD-10 codes.
- ``GET /fhir/R4/ValueSet/ha-diseases`` returns a valid FHIR ValueSet whose
  compose includes the disease concepts.
- Both resources validate against ``fhir.resources`` (the facade's gate).
- ``GET /fhir/R4/CodeSystem`` returns a searchset Bundle listing CodeSystems.
- Unknown id returns 404 OperationOutcome.
- The CapabilityStatement advertises CodeSystem + ValueSet.
"""

import uuid

import pytest

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.tenant_model import TenantModel
from app.services.fhir_helpers import parse_fhir_resource
from app.services.seed_service import SeedService


async def _tenant_and_headers(role="ADMIN"):
    tenant_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"cs-{tenant_id}"))
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


async def _seed_diseases():
    svc = SeedService()
    await svc.seed_concepts()
    await svc.seed_diseases()


# ---------------------------------------------------------------------------
# CodeSystem read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_codesystem_read_returns_valid_fhir(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    await _seed_diseases()

    resp = await async_client.get(
        "/api/v1/fhir/R4/CodeSystem/ha-diseases", headers=headers
    )
    assert resp.status_code == 200, resp.text
    cs = resp.json()
    assert cs["resourceType"] == "CodeSystem"
    assert cs["id"] == "ha-diseases"
    assert cs["status"] == "active"
    assert cs["content"] == "complete"
    assert "concept" in cs and len(cs["concept"]) > 0
    # Validates against the fhir.resources model (the facade's gate).
    parse_fhir_resource("CodeSystem", cs)


@pytest.mark.asyncio
async def test_codesystem_contains_disease_icd10_codes(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    await _seed_diseases()

    resp = await async_client.get(
        "/api/v1/fhir/R4/CodeSystem/ha-diseases", headers=headers
    )
    assert resp.status_code == 200
    codes = {c["code"] for c in resp.json()["concept"]}
    # A few well-known ICD-10 codes from the seed.
    assert "E11.9" in codes, "type-2-diabetes (E11.9) missing"
    assert "I10" in codes, "hypertension (I10) missing"
    assert "B05.9" in codes, "measles (B05.9) missing"
    assert resp.json()["count"] == len(resp.json()["concept"])


@pytest.mark.asyncio
async def test_codesystem_unknown_id_returns_404(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    resp = await async_client.get(
        "/api/v1/fhir/R4/CodeSystem/does-not-exist", headers=headers
    )
    assert resp.status_code == 404
    assert resp.json()["resourceType"] == "OperationOutcome"


# ---------------------------------------------------------------------------
# ValueSet read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valueset_read_returns_valid_fhir(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    await _seed_diseases()

    resp = await async_client.get(
        "/api/v1/fhir/R4/ValueSet/ha-diseases", headers=headers
    )
    assert resp.status_code == 200, resp.text
    vs = resp.json()
    assert vs["resourceType"] == "ValueSet"
    assert vs["id"] == "ha-diseases"
    assert vs["status"] == "active"
    compose = vs["compose"]["include"][0]
    assert compose["system"] == "http://hl7.org/fhir/sid/icd-10"
    assert len(compose["concept"]) > 0
    parse_fhir_resource("ValueSet", vs)


@pytest.mark.asyncio
async def test_valueset_includes_disease_codes(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    await _seed_diseases()

    resp = await async_client.get(
        "/api/v1/fhir/R4/ValueSet/ha-diseases", headers=headers
    )
    assert resp.status_code == 200
    concepts = resp.json()["compose"]["include"][0]["concept"]
    codes = {c["code"] for c in concepts}
    assert "E11.9" in codes
    assert "J11.1" in codes  # influenza


@pytest.mark.asyncio
async def test_valueset_unknown_id_returns_404(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    resp = await async_client.get("/api/v1/fhir/R4/ValueSet/nope", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Search (Bundle)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_codesystem_search_returns_bundle(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    await _seed_diseases()

    resp = await async_client.get("/api/v1/fhir/R4/CodeSystem", headers=headers)
    assert resp.status_code == 200, resp.text
    bundle = resp.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["total"] >= 1
    types = {e["resource"]["resourceType"] for e in bundle["entry"]}
    assert "CodeSystem" in types


@pytest.mark.asyncio
async def test_valueset_search_returns_bundle(async_client):
    _, headers = await _tenant_and_headers("ADMIN")
    await _seed_diseases()

    resp = await async_client.get("/api/v1/fhir/R4/ValueSet", headers=headers)
    assert resp.status_code == 200
    bundle = resp.json()
    assert bundle["type"] == "searchset"
    assert bundle["total"] >= 1
    assert any(e["resource"]["resourceType"] == "ValueSet" for e in bundle["entry"])


# ---------------------------------------------------------------------------
# CapabilityStatement advertises the new resources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_statement_advertises_terminology(async_client):
    resp = await async_client.get("/api/v1/fhir/R4/metadata")
    assert resp.status_code == 200
    cs = resp.json()
    advertised = {r["type"] for r in cs["rest"][0]["resource"]}
    assert "CodeSystem" in advertised
    assert "ValueSet" in advertised


# ---------------------------------------------------------------------------
# Write operations are not supported (read-only terminology)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_codesystem_create_not_supported(async_client):
    _, headers = await _tenant_and_headers("SYSTEM_ADMIN")
    resp = await async_client.post(
        "/api/v1/fhir/R4/CodeSystem",
        json={"resourceType": "CodeSystem", "status": "active", "content": "complete"},
        headers=headers,
    )
    assert resp.status_code == 405
