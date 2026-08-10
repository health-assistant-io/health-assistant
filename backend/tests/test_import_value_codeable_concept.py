"""Regression test for the valueCodeableConcept FHIR import round-trip fix
(plan Step 10 + Step 13).

Pre-fix: ``import_service._upsert_observation`` silently dropped
``valueCodeableConcept`` on FHIR import — the converter produced
``value_codeable_concept`` (snake) but the upsert only read
``value_quantity`` / ``value_string``, never the CodeableConcept column.

Post-fix: the upsert persists ``value_codeable_concept`` (camelCase ORM
column) from the converter's ``value_codeable_concept`` (snake) key, and
the hard validator runs on the import path.
"""
import uuid

import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models.fhir.patient import Observation, Patient
from app.models.tenant_model import TenantModel
from app.services.fhir_converter import fhir_to_observation_orm
from app.services.import_service import ImportService


V3 = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


@pytest.fixture(autouse=True)
async def _scrub():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM fhir_observations WHERE code->>'text' = 'ImportRT Test'")
        )
        await session.execute(
            text("DELETE FROM fhir_patients WHERE name->>'family' = 'ImportRT'")
        )
        await session.execute(
            text("DELETE FROM tenants WHERE slug LIKE 'importrt-%'")
        )
        await session.commit()


async def _seed_tenant_patient():
    async with AsyncSessionLocal() as session:
        tenant = TenantModel(
            id=uuid.uuid4(), name="ImportRT", slug=f"importrt-{uuid.uuid4().hex[:8]}"
        )
        session.add(tenant)
        await session.flush()
        patient = Patient(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            name={"family": "ImportRT", "given": ["T"]},
            gender="UNKNOWN",
        )
        session.add(patient)
        await session.commit()
        return tenant.id, patient.id


@pytest.mark.asyncio
async def test_value_codeable_concept_round_trips_through_fhir_import():
    """A FHIR R4 Observation with valueCodeableConcept survives the import
    path and is readable with the value intact on the ORM row."""
    tenant_id, patient_id = await _seed_tenant_patient()

    # Build canonical FHIR R4 shape (as an external system would send).
    fhir_obs = {
        "resourceType": "Observation",
        "id": str(uuid.uuid4()),
        "status": "final",
        "code": {"text": "ImportRT Test", "coding": [{"code": "test-1"}]},
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": "2024-01-01T00:00:00Z",
        "valueCodeableConcept": {
            "coding": [
                {
                    "code": "POS",
                    "system": V3,
                    "display": "Positive",
                }
            ]
        },
    }

    # Convert FHIR → ORM shape (the import boundary).
    orm = fhir_to_observation_orm(fhir_obs)
    assert orm["value_codeable_concept"] is not None, (
        "fhir_to_observation_orm must preserve valueCodeableConcept"
    )

    # Persist via the import service.
    async with AsyncSessionLocal() as session:
        svc = ImportService(session)
        action, _ = await svc._upsert_observation(
            orm,
            old_id_str=fhir_obs["id"],
            tenant_id=tenant_id,
            id_remap={},
        )
        await session.commit()
    assert action == "created", f"Expected 'created', got {action!r}"

    # Read back and verify the valueCodeableConcept round-tripped.
    async with AsyncSessionLocal() as session:
        obs = (
            await session.execute(
                select(Observation).where(
                    Observation.tenant_id == tenant_id,
                    Observation.code["text"].astext == "ImportRT Test",
                )
            )
        ).scalar_one()
        assert obs.value_codeable_concept is not None, (
            "value_codeable_concept was dropped on import round-trip"
        )
        coding = obs.value_codeable_concept["coding"]
        assert coding[0]["code"] == "POS"
        assert coding[0]["system"] == V3
        assert coding[0]["display"] == "Positive"
        # The numeric slots should be absent/None for a CodeableConcept observation.
        assert obs.value_quantity is None
