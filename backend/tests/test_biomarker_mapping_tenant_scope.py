"""Regression: map_observations_to_biomarkers must not link an observation to a
biomarker owned by a *different* tenant. The code/name/slug lookups are
tenant-scoped (obs tenant OR global NULL); a miss auto-creates in the obs's own
tenant. Without the scope, a sync in tenant B linked to tenant A's biomarker for
the same LOINC code, and the tenant-scoped catalog/details lookup 404'd even
though the row existed (the mobile-app HR sync case).
"""
import datetime
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.patient import Observation, Patient
from app.models.tenant_model import TenantModel
from app.services.fhir_service import map_observations_to_biomarkers

LOINC = "8867-4"  # Heart Rate


@pytest_asyncio.fixture
async def two_tenants_with_hr_biomarker():
    """Tenant A owns a Heart Rate biomarker; tenant B has a patient but no HR biomarker."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    patient_b = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_a, name="Tenant A", slug=f"a-{tenant_a.hex[:8]}"))
        db.add(TenantModel(id=tenant_b, name="Tenant B", slug=f"b-{tenant_b.hex[:8]}"))
        await db.flush()
        # Tenant A already has a Heart Rate definition.
        a_hr = BiomarkerDefinition(
            slug="heart-rate",
            coding_system="loinc",
            code=LOINC,
            name="Heart Rate",
            tenant_id=tenant_a,
        )
        db.add(a_hr)
        db.add(Patient(id=patient_b, tenant_id=tenant_b, name={"family": "B", "given": ["User"]}, gender="UNKNOWN"))
        await db.commit()
        return {"tenant_a": tenant_a, "tenant_b": tenant_b, "patient_b": patient_b, "a_hr_id": a_hr.id}


@pytest.mark.asyncio
async def test_does_not_cross_link_to_another_tenants_biomarker(two_tenants_with_hr_biomarker):
    ctx = two_tenants_with_hr_biomarker
    obs = Observation(
        tenant_id=ctx["tenant_b"],
        patient_id=ctx["patient_b"],
        code={"coding": [{"system": "http://loinc.org", "code": LOINC}], "text": "Heart Rate"},
        subject={"reference": f"Patient/{ctx['patient_b']}"},
        value_quantity={"value": 72.0, "unit": "bpm"},
        effective_datetime=datetime.datetime.now(datetime.timezone.utc),
        status="final",
        biomarker_id=None,
    )

    async with AsyncSessionLocal() as db:
        db.add(obs)
        await db.flush()
        await map_observations_to_biomarkers(db, [obs])
        await db.commit()

        # Must NOT link to tenant A's biomarker.
        assert obs.biomarker_id != ctx["a_hr_id"]
        # Must have linked to something in tenant B (auto-created).
        assert obs.biomarker_id is not None
        linked = (
            await db.execute(
                select(BiomarkerDefinition).where(BiomarkerDefinition.id == obs.biomarker_id)
            )
        ).scalar_one()
        assert linked.tenant_id == ctx["tenant_b"]


@pytest.mark.asyncio
async def test_auto_created_vital_defaults_to_telemetry(two_tenants_with_hr_biomarker):
    ctx = two_tenants_with_hr_biomarker
    obs = Observation(
        tenant_id=ctx["tenant_b"],
        patient_id=ctx["patient_b"],
        code={"coding": [{"system": "http://loinc.org", "code": LOINC}], "text": "Heart Rate"},
        subject={"reference": f"Patient/{ctx['patient_b']}"},
        value_quantity={"value": 72.0, "unit": "bpm"},
        effective_datetime=datetime.datetime.now(datetime.timezone.utc),
        status="final",
        biomarker_id=None,
    )

    async with AsyncSessionLocal() as db:
        db.add(obs)
        await db.flush()
        await map_observations_to_biomarkers(db, [obs])
        await db.commit()
        linked = (
            await db.execute(
                select(BiomarkerDefinition).where(BiomarkerDefinition.id == obs.biomarker_id)
            )
        ).scalar_one()
        assert linked.is_telemetry is True  # HR is a high-frequency wearable vital
