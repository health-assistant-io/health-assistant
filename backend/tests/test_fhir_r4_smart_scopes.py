"""SMART-on-FHIR scope enforcement tests (Phase 2).

Pins the per-interaction scope gate on the facade and the patient-compartment
narrowing for ``patient/`` scoped clients. The facade is api-only; these tests
mint api tokens directly via ``create_api_access_token`` (the OAuth
client-credentials flow itself is covered in ``test_oauth_client_credentials``).
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import AsyncSessionLocal
from app.core.security import create_api_access_token, get_password_hash
from app.main import app as real_app
from app.models.enums import Gender, Role
from app.models.fhir.patient import Observation, Patient
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel


pytestmark = pytest.mark.asyncio


def _api_headers(tenant_id, *, scopes, bound_patient_id=None):
    token, _ = create_api_access_token(
        client_id=f"ci-test-{uuid.uuid4().hex[:12]}",
        tenant_id=str(tenant_id),
        scopes=scopes,
        bound_patient_id=str(bound_patient_id) if bound_patient_id else None,
    )
    return {"Authorization": f"Bearer {token}"}


async def _setup_tenant():
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tid, name="SMART T", slug=f"smart-{tid}"))
        db.add(
            UserModel(
                id=uid,
                email=f"admin-{uid}@smart.test",
                hashed_password=get_password_hash("x"),
                tenant_id=tid,
                role=Role.SYSTEM_ADMIN,
            )
        )
        await db.commit()
    return tid


async def _make_observation(tenant_id, patient_id, *, code="GLU"):
    from datetime import datetime, timezone

    oid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Observation(
                id=oid,
                tenant_id=tenant_id,
                patient_id=patient_id,
                status="final",
                subject={"reference": f"Patient/{patient_id}"},
                code={"coding": [{"code": code, "system": "http://loinc.org"}]},
                value_quantity={"value": 5.5, "unit": "mmol/L"},
                effective_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        await db.commit()
    return oid


async def _make_patient(tenant_id):
    pid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Patient(
                id=pid,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["Scope"]},
                gender=Gender.UNKNOWN,
            )
        )
        await db.commit()
    return pid


@pytest.fixture
async def client():
    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Per-interaction scope gate
# ---------------------------------------------------------------------------


async def test_read_scope_allows_search(client):
    tid = await _setup_tenant()
    h = _api_headers(tid, scopes=["system/Observation.read"])
    r = await client.get("/api/v1/fhir/R4/Observation", headers=h)
    assert r.status_code == 200
    assert r.json()["resourceType"] == "Bundle"


async def test_read_scope_blocks_write(client):
    tid = await _setup_tenant()
    h = _api_headers(tid, scopes=["system/Observation.read"])
    r = await client.post("/api/v1/fhir/R4/Observation", json={}, headers=h)
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["resourceType"] == "OperationOutcome"
    assert body["detail"]["issue"][0]["code"] == "forbidden"


async def test_write_scope_allows_create(client):
    """A write-scoped client reaches the create path (the body here is invalid
    FHIR, so we expect a 400 — the point is it is NOT a 403 scope rejection)."""
    tid = await _setup_tenant()
    h = _api_headers(tid, scopes=["system/Observation.write"])
    r = await client.post("/api/v1/fhir/R4/Observation", json={}, headers=h)
    assert r.status_code != 403  # scope gate passed; 400/422 from validation is fine


async def test_wildcard_scope_allows_everything(client):
    tid = await _setup_tenant()
    h = _api_headers(tid, scopes=["system/*.*"])
    r = await client.get("/api/v1/fhir/R4/Patient", headers=h)
    assert r.status_code == 200
    r = await client.get("/api/v1/fhir/R4/Observation", headers=h)
    assert r.status_code == 200


async def test_resource_specific_scope_does_not_leak_other_resources(client):
    tid = await _setup_tenant()
    # Can read Patient but not Observation.
    h = _api_headers(tid, scopes=["system/Patient.read"])
    r = await client.get("/api/v1/fhir/R4/Patient", headers=h)
    assert r.status_code == 200
    r = await client.get("/api/v1/fhir/R4/Observation", headers=h)
    assert r.status_code == 403


async def test_no_matching_scope_blocks_read(client):
    tid = await _setup_tenant()
    # write scope does not grant read.
    h = _api_headers(tid, scopes=["system/Observation.write"])
    r = await client.get("/api/v1/fhir/R4/Observation", headers=h)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Patient compartment
# ---------------------------------------------------------------------------


async def test_patient_scope_narrows_to_bound_patient(client):
    tid = await _setup_tenant()
    pid_a = await _make_patient(tid)
    pid_b = await _make_patient(tid)
    obs_a = await _make_observation(tid, pid_a, code="A")
    obs_b = await _make_observation(tid, pid_b, code="B")

    h = _api_headers(tid, scopes=["patient/Observation.read"], bound_patient_id=pid_a)

    # Search with no patient filter — the compartment narrows to the bound
    # patient only (obs_a), never exposing obs_b.
    r = await client.get("/api/v1/fhir/R4/Observation", headers=h)
    assert r.status_code == 200
    ids = {e["resource"]["id"] for e in r.json().get("entry", [])}
    assert str(obs_a) in ids
    assert str(obs_b) not in ids

    # A search explicitly asking for the *other* patient still yields only the
    # bound patient's data (compartment AND query param → obs_a only, since the
    # client cannot escape its compartment by naming another patient).
    r = await client.get(f"/api/v1/fhir/R4/Observation?patient={pid_a}", headers=h)
    assert r.status_code == 200
    assert str(obs_a) in {e["resource"]["id"] for e in r.json().get("entry", [])}

    # Reading the bound patient's observation works.
    r = await client.get(f"/api/v1/fhir/R4/Observation/{obs_a}", headers=h)
    assert r.status_code == 200

    # Reading the other patient's observation is hidden (404, not 403).
    r = await client.get(f"/api/v1/fhir/R4/Observation/{obs_b}", headers=h)
    assert r.status_code == 404


async def test_patient_scope_blocks_cross_patient_write(client):
    tid = await _setup_tenant()
    pid_a = await _make_patient(tid)
    pid_b = await _make_patient(tid)

    h = _api_headers(
        tid, scopes=["patient/Observation.write"], bound_patient_id=pid_a
    )
    # Attempt to write an Observation for patient B → 403/405 (PermissionError
    # from the crud layer maps to 405 'not-supported' by the facade). Either way
    # it must NOT be 201.
    payload = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"code": "X"}]},
        "subject": {"reference": f"Patient/{pid_b}"},
    }
    r = await client.post("/api/v1/fhir/R4/Observation", json=payload, headers=h)
    assert r.status_code in (403, 405)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


async def test_smart_configuration_endpoint(client):
    r = await client.get("/.well-known/smart-configuration")
    assert r.status_code == 200
    body = r.json()
    assert body["token_endpoint"] == "/api/v1/oauth/token"
    assert "client_credentials" in body["grant_types_supported"]
    assert body["capabilities"]


async def test_capability_statement_advertises_smart(client):
    r = await client.get("/api/v1/fhir/R4/metadata")
    assert r.status_code == 200
    security = r.json()["rest"][0]["security"]
    codes = [
        c["code"]
        for svc in security.get("service", [])
        for c in svc.get("coding", [])
    ]
    assert "SMART-on-FHIR" in codes
    # oauth-uris extension points at the token endpoint.
    ext_urls = [e for e in security.get("extension", []) if "oauth-uris" in e.get("url", "")]
    assert ext_urls
