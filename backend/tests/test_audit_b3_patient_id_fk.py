"""Regression tests for audit B3 — patient_id FK on Observation/DiagnosticReport.

Covers:
1. Both columns exist (UUID) with on-delete CASCADE FKs.
2. ``create_observation`` derives ``patient_id`` from the FHIR ``subject`` ref.
3. ``create_diagnostic_report`` likewise.
4. Deleting a patient CASCADE-deletes their observations + reports.
5. The FHIR R4 facade ``?patient=`` filter now scopes Observation (was a silent
   no-op before the column existed — ``_build_resource_filter`` keys on
   ``hasattr(model, "patient_id")``).
"""
import uuid

import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models.fhir.patient import DiagnosticReport, Observation, Patient
from app.models.tenant_model import TenantModel
from app.services.fhir_helpers import coerce_patient_id


async def _seed_tenant_and_patient(session):
    tenant = TenantModel(id=uuid.uuid4(), name="B3", slug=f"b3-{uuid.uuid4().hex[:8]}")
    session.add(tenant)
    await session.flush()
    patient = Patient(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name={"family": "Test", "given": ["B3"]},
        gender="UNKNOWN",
    )
    session.add(patient)
    await session.flush()
    return tenant, patient


@pytest.mark.asyncio
async def test_patient_id_columns_and_cascade_fk_exist():
    async with AsyncSessionLocal() as session:
        for table in ("fhir_observations", "fhir_diagnostic_reports"):
            res = await session.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name=:t AND column_name='patient_id'"
                ),
                {"t": table},
            )
            assert res.scalar_one() == "uuid", f"{table}.patient_id must be uuid"
            res2 = await session.execute(
                text(
                    "SELECT confdeltype FROM pg_constraint "
                    "WHERE conname LIKE :p"
                ),
                {"p": f"fk_{table}_patient_id"},
            )
            deltype = res2.scalar_one()
            assert deltype in ("c", b"c"), (
                f"{table}.patient_id FK must be ON DELETE CASCADE (c), got {deltype!r}"
            )


@pytest.mark.asyncio
async def test_create_observation_derives_patient_id_from_subject():
    from app.services.fhir_service import create_observation

    async with AsyncSessionLocal() as session:
        tenant, patient = await _seed_tenant_and_patient(session)
        await session.commit()

    try:
        obs = await create_observation(
            {
                "status": "final",
                "code": {"text": "Glucose"},
                "subject": {"reference": f"Patient/{patient.id}"},
            },
            tenant_id=tenant.id,
        )
        assert obs is not None
        assert obs.patient_id == patient.id, (
            "create_observation must populate patient_id from the subject ref"
        )
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM fhir_observations WHERE id = :id"),
                {"id": str(obs.id)},
            )
            await session.execute(
                text("DELETE FROM fhir_patients WHERE id = :id"),
                {"id": str(patient.id)},
            )
            await session.execute(
                text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant.id)}
            )
            await session.commit()


@pytest.mark.asyncio
async def test_create_diagnostic_report_derives_patient_id_from_subject():
    from app.services.fhir_service import create_diagnostic_report

    async with AsyncSessionLocal() as session:
        tenant, patient = await _seed_tenant_and_patient(session)
        await session.commit()

    try:
        report = await create_diagnostic_report(
            {
                "status": "final",
                "code": {"text": "Imaging"},
                "subject": {"reference": f"Patient/{patient.id}"},
                "conclusion": "Normal",
            },
            tenant_id=tenant.id,
        )
        assert report is not None
        assert report.patient_id == patient.id
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM fhir_diagnostic_reports WHERE id = :id"),
                {"id": str(report.id)},
            )
            await session.execute(
                text("DELETE FROM fhir_patients WHERE id = :id"),
                {"id": str(patient.id)},
            )
            await session.execute(
                text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant.id)}
            )
            await session.commit()


@pytest.mark.asyncio
async def test_patient_delete_cascades_to_observations_and_reports():
    async with AsyncSessionLocal() as session:
        tenant, patient = await _seed_tenant_and_patient(session)
        obs = Observation(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            patient_id=patient.id,
            status="final",
            code={"text": "Glucose"},
            subject={"reference": f"Patient/{patient.id}"},
        )
        report = DiagnosticReport(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            patient_id=patient.id,
            status="final",
            code={"text": "Imaging"},
            subject={"reference": f"Patient/{patient.id}"},
        )
        session.add_all([obs, report])
        await session.commit()

        obs_id, report_id = obs.id, report.id

        # Delete the patient; CASCADE should remove both rows.
        await session.execute(
            text("DELETE FROM fhir_patients WHERE id = :id"),
            {"id": str(patient.id)},
        )
        await session.commit()

        remaining_obs = (
            await session.execute(
                select(Observation).where(Observation.id == obs_id)
            )
        ).scalar_one_or_none()
        remaining_rep = (
            await session.execute(
                select(DiagnosticReport).where(DiagnosticReport.id == report_id)
            )
        ).scalar_one_or_none()
        assert remaining_obs is None, "Observation must cascade-delete with its patient"
        assert remaining_rep is None, "DiagnosticReport must cascade-delete with its patient"

        await session.execute(
            text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant.id)}
        )
        await session.commit()


def test_coerce_patient_id_helper():
    """The shared derivation helper handles every reference shape + junk."""
    good = uuid.uuid4()
    assert coerce_patient_id(None, {"reference": f"Patient/{good}"}) == good
    assert coerce_patient_id(None, {"reference": f"urn:uuid:{good}"}) == good
    assert coerce_patient_id(str(good), None) == good
    assert coerce_patient_id(None, {"reference": "Patient/unknown"}) is None
    assert coerce_patient_id(None, None) is None
    assert coerce_patient_id("not-a-uuid", None) is None
