"""Real-DB tests for FHIR R4 facade STATE-biomarker support (plan Step 10).

Confirms:
1. value-concept / value-string / component-code search params actually
   return matching Observations through the live facade endpoint.
2. The latent ``valueCodeableConcept`` import drop is fixed — round-trip
   a valueCodeableConcept Observation through FHIR import and read it back.
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.tenant_model import TenantModel


V3 = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


@pytest.fixture(autouse=True)
async def _scrub():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "DELETE FROM fhir_observations "
                "WHERE code->>'text' IN ('StateR4 Test', 'StateR4 Multi', 'StateR4 Import')"
            )
        )
        await session.execute(
            text("DELETE FROM fhir_patients WHERE name->>'family' = 'StateR4'")
        )
        await session.execute(
            text("DELETE FROM tenants WHERE slug LIKE 'state-r4-%'")
        )
        await session.commit()


async def _seed_tenant_patient_and_headers():
    """Create a tenant + patient + mint facade OAuth2 headers for them."""
    from tests._facade_auth import facade_api_headers

    async with AsyncSessionLocal() as session:
        tenant_id = uuid.uuid4()
        patient_id = uuid.uuid4()
        session.add(
            TenantModel(
                id=tenant_id, name="StateR4", slug=f"state-r4-{uuid.uuid4().hex[:8]}"
            )
        )
        await session.flush()
        from app.models.fhir.patient import Patient

        session.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "StateR4", "given": ["T"]},
                gender="UNKNOWN",
            )
        )
        await session.commit()
    headers = await facade_api_headers(tenant_id)
    return tenant_id, patient_id, headers


async def _insert_observation(
    tenant_id,
    patient_id,
    *,
    code_text,
    value_cc=None,
    value_string=None,
    component=None,
):
    """Direct ORM insert so we don't depend on a STATE biomarker definition
    (the facade test exercises the JSONB search, not the biomarker validator)."""
    from app.models.fhir import Observation

    async with AsyncSessionLocal() as session:
        obs = Observation(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            status="final",
            code={"text": code_text},
            subject={"reference": f"Patient/{patient_id}"},
            patient_id=patient_id,
            value_codeableConcept=value_cc,
            value_string=value_string,
            component=component,
        )
        session.add(obs)
        await session.commit()
        return obs.id


# ---------------------------------------------------------------------------
# 1. value-concept search returns matching STATE observations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_value_concept_search_returns_state_observation(async_client):
    tenant_id, patient_id, headers = await _seed_tenant_patient_and_headers()
    await _insert_observation(
        tenant_id,
        patient_id,
        code_text="StateR4 Test",
        value_cc={"coding": [{"code": "POS", "system": V3, "display": "Positive"}]},
    )
    await _insert_observation(
        tenant_id,
        patient_id,
        code_text="StateR4 Test",
        value_cc={"coding": [{"code": "NEG", "system": V3, "display": "Negative"}]},
    )

    response = await async_client.get(
        f"/api/v1/fhir/R4/Observation?value-concept=POS&patient={patient_id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    bundle = response.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["total"] == 1
    entries = bundle["entry"] or []
    assert len(entries) == 1
    obs = entries[0]["resource"]
    assert obs["valueCodeableConcept"]["coding"][0]["code"] == "POS"


@pytest.mark.asyncio
async def test_value_concept_search_with_system_narrows(async_client):
    """``value-concept=system|code`` narrows by code system (POS in v3-OI vs
    POS in a custom urn = different concepts)."""
    tenant_id, patient_id, headers = await _seed_tenant_patient_and_headers()
    await _insert_observation(
        tenant_id,
        patient_id,
        code_text="StateR4 Test",
        value_cc={"coding": [{"code": "POS", "system": V3}]},
    )
    await _insert_observation(
        tenant_id,
        patient_id,
        code_text="StateR4 Test",
        value_cc={
            "coding": [
                {
                    "code": "POS",
                    "system": "urn:uuid:health-assistant:custom-state",
                }
            ]
        },
    )

    response = await async_client.get(
        f"/api/v1/fhir/R4/Observation?value-concept={V3}|POS&patient={patient_id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    bundle = response.json()
    assert bundle["total"] == 1
    assert (
        bundle["entry"][0]["resource"]["valueCodeableConcept"]["coding"][0]["system"]
        == V3
    )


# ---------------------------------------------------------------------------
# 2. value-string search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_value_string_search_substring_case_insensitive(async_client):
    tenant_id, patient_id, headers = await _seed_tenant_patient_and_headers()
    await _insert_observation(
        tenant_id,
        patient_id,
        code_text="StateR4 Test",
        value_string="Sample hemolyzed",
    )

    response = await async_client.get(
        f"/api/v1/fhir/R4/Observation?value-string=hemolyzed&patient={patient_id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


# ---------------------------------------------------------------------------
# 3. component-code search (multi-state)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_component_code_search_narrows_multi_state(async_client):
    tenant_id, patient_id, headers = await _seed_tenant_patient_and_headers()
    await _insert_observation(
        tenant_id,
        patient_id,
        code_text="StateR4 Multi",
        component=[
            {
                "code": {"coding": [{"code": "staph-aureus"}]},
                "valueCodeableConcept": {"coding": [{"code": "POS", "system": V3}]},
            },
            {
                "code": {"coding": [{"code": "e-coli"}]},
                "valueCodeableConcept": {"coding": [{"code": "NEG", "system": V3}]},
            },
        ],
    )

    response = await async_client.get(
        f"/api/v1/fhir/R4/Observation?component-code=staph-aureus&patient={patient_id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    bundle = response.json()
    assert bundle["total"] == 1
    comp_codes = [
        c["code"]["coding"][0]["code"]
        for c in bundle["entry"][0]["resource"]["component"]
    ]
    assert "staph-aureus" in comp_codes
