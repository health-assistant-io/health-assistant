"""The four patient-record resolvers (plan 5 W3).

Each resolver validates the user-approved ``final_payload`` against the kind's
contract schema (the caller does that), enforces the proposal's patient scope,
and routes through the same canonical service the UI uses. These tests pin the
writes end-to-end plus the trust boundary: a payload that retargets the write to
another patient is rejected before any DB write.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.extensions.sdk.proposals import (
    PATIENT_RECORD_RESOLVERS,
    resolve_add_biomarker_to_examination,
    resolve_add_medication,
    resolve_create_event,
    resolve_create_examination,
)
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import ClinicalEvent, ClinicalEventType
from app.models.concept_model import Concept
from app.models.enums import (
    CatalogScope,
    ClinicalEventStatus,
    ConceptKind,
    ConceptStatus,
    Role,
)
from app.models.examination_model import ExaminationModel
from app.models.fhir.medication import Medication
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.schemas.user import TokenData
from app.services.proposals.proposal_resolvers import ProposalApplyError, assert_payload_scope


@pytest_asyncio.fixture
async def clinical_scope():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    other_patient_id = uuid.uuid4()
    concept_id = uuid.uuid4()
    event_type_id = uuid.uuid4()
    biomarker_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="Rx T.", slug=f"rx-{tenant_id.hex[:8]}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"rx-{user_id.hex[:6]}@test.local",
                password_hash="x",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        for pid, given in ((patient_id, ["One"]), (other_patient_id, ["Two"])):
            db.add(
                Patient(
                    id=pid,
                    tenant_id=tenant_id,
                    name={"family": "Test", "given": given},
                    gender="UNKNOWN",
                )
            )
        db.add(
            Concept(
                id=concept_id,
                slug=f"event-category-{concept_id.hex[:8]}",
                name="General",
                primary_kind=ConceptKind.EVENT_CATEGORY,
                status=ConceptStatus.ACTIVE,
                scope=CatalogScope.SYSTEM,
                display_order=0,
                aliases=[],
            )
        )
        await db.flush()
        db.add(
            ClinicalEventType(
                id=event_type_id,
                name="Pregnancy",
                slug=f"pregnancy-{event_type_id.hex[:8]}",
                category_concept_id=concept_id,
                tenant_id=tenant_id,
            )
        )
        db.add(
            BiomarkerDefinition(
                id=biomarker_id,
                slug=f"glucose-{biomarker_id.hex[:8]}",
                name="Glucose",
                coding_system="loinc",
                aliases=[],
                is_telemetry=False,
                scope=CatalogScope.SYSTEM,
            )
        )
        await db.commit()

    actor = TokenData(
        user_id=user_id,
        sub=f"rx-{user_id.hex[:6]}@test.local",
        role=Role.ADMIN.value,
        tenant_id=tenant_id,
    )
    try:
        yield {
            "tenant_id": tenant_id,
            "patient_id": patient_id,
            "other_patient_id": other_patient_id,
            "event_type_id": event_type_id,
            "biomarker_id": biomarker_id,
            "actor": actor,
        }
    finally:
        async with AsyncSessionLocal() as db:
            from app.models.concept_model import ConceptEdge

            await db.execute(
                ClinicalEvent.__table__.delete().where(
                    ClinicalEvent.__table__.c.tenant_id == tenant_id
                )
            )
            await db.execute(
                ExaminationModel.__table__.delete().where(
                    ExaminationModel.__table__.c.tenant_id == tenant_id
                )
            )
            await db.execute(
                Medication.__table__.delete().where(Medication.__table__.c.tenant_id == tenant_id)
            )
            await db.execute(
                ConceptEdge.__table__.delete().where(ConceptEdge.__table__.c.tenant_id == tenant_id)
            )
            await db.execute(
                ClinicalEventType.__table__.delete().where(
                    ClinicalEventType.__table__.c.id == event_type_id
                )
            )
            await db.execute(
                BiomarkerDefinition.__table__.delete().where(
                    BiomarkerDefinition.__table__.c.id == biomarker_id
                )
            )
            await db.execute(Concept.__table__.delete().where(Concept.__table__.c.id == concept_id))
            await db.execute(
                Patient.__table__.delete().where(Patient.__table__.c.tenant_id == tenant_id)
            )
            await db.execute(
                UserModel.__table__.delete().where(UserModel.__table__.c.id == user_id)
            )
            await db.execute(
                TenantModel.__table__.delete().where(TenantModel.__table__.c.id == tenant_id)
            )
            await db.commit()


def test_all_four_kinds_have_a_resolver():
    assert set(PATIENT_RECORD_RESOLVERS) == {
        "create_event",
        "create_examination",
        "add_medication",
        "add_biomarker_to_examination",
    }


@pytest.mark.asyncio
async def test_resolve_create_event_writes_a_clinical_event(clinical_scope):
    async with AsyncSessionLocal() as db:
        write = await resolve_create_event(
            db,
            clinical_scope["actor"],
            {
                "patient_id": str(clinical_scope["patient_id"]),
                "type_id": str(clinical_scope["event_type_id"]),
                "title": "Pregnancy",
                "status": ClinicalEventStatus.ACTIVE.value,
                "description": "confirmed",
                "onset_date": (date.today() - timedelta(days=3)).isoformat(),
            },
        )
        event = await db.get(ClinicalEvent, write.entity_id)

    assert event is not None
    assert event.tenant_id == clinical_scope["tenant_id"]
    assert event.patient_id == clinical_scope["patient_id"]
    assert event.title == "Pregnancy"


@pytest.mark.asyncio
async def test_resolve_create_examination_writes_an_examination(clinical_scope):
    async with AsyncSessionLocal() as db:
        write = await resolve_create_examination(
            db,
            clinical_scope["actor"],
            {
                "patient_id": str(clinical_scope["patient_id"]),
                "examination_date": date.today().isoformat(),
                "notes": "Annual check-up",
                "category": "General",
            },
        )
        exam = await db.get(ExaminationModel, write.entity_id)

    assert exam is not None
    assert exam.tenant_id == clinical_scope["tenant_id"]
    assert exam.patient_id == clinical_scope["patient_id"]
    assert exam.notes == "Annual check-up"


@pytest.mark.asyncio
async def test_resolve_add_medication_writes_a_prescription(clinical_scope):
    async with AsyncSessionLocal() as db:
        write = await resolve_add_medication(
            db,
            clinical_scope["actor"],
            {
                "patient_id": str(clinical_scope["patient_id"]),
                "status": "active",
                "code": {"text": "Metformin"},
                "dosage": "500 mg",
                "frequency": {"type": "daily", "frequency": 1, "period": 1, "period_unit": "day"},
            },
        )
        med = await db.get(Medication, write.entity_id)

    assert med is not None
    assert med.tenant_id == clinical_scope["tenant_id"]
    assert med.patient_id == clinical_scope["patient_id"]
    assert med.code["text"] == "Metformin"


@pytest.mark.asyncio
async def test_resolve_add_biomarker_writes_an_observation(clinical_scope):
    async with AsyncSessionLocal() as db:
        write = await resolve_add_biomarker_to_examination(
            db,
            clinical_scope["actor"],
            {
                "patient_id": str(clinical_scope["patient_id"]),
                "biomarker_id": str(clinical_scope["biomarker_id"]),
                "status": "final",
                "code": {
                    "coding": [
                        {"system": "http://loinc.org", "code": "2345-7", "display": "Glucose"}
                    ],
                    "text": "Glucose",
                },
                "value_quantity": {"value": 95.0, "unit": "mg/dL"},
            },
        )
        from app.models.fhir.patient import Observation

        observation = await db.get(Observation, write.entity_id)

    assert observation is not None
    assert observation.tenant_id == clinical_scope["tenant_id"]
    assert observation.patient_id == clinical_scope["patient_id"]
    assert observation.biomarker_id == clinical_scope["biomarker_id"]


@pytest.mark.asyncio
async def test_cross_patient_payload_is_rejected_before_any_write(clinical_scope):
    """The scope gate is a pure check — no service is called, no row lands."""
    from app.models.proposal import Proposal

    proposal = Proposal(
        id=uuid.uuid4(),
        tenant_id=clinical_scope["tenant_id"],
        source="extension",
        kind_id=uuid.uuid4(),
        status="proposed",
        payload={},
        payload_schema_version=1,
        patient_id=clinical_scope["patient_id"],
    )
    with pytest.raises(ProposalApplyError):
        assert_payload_scope(proposal, {"patient_id": str(clinical_scope["other_patient_id"])})

    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(Medication).where(
                        Medication.patient_id == clinical_scope["other_patient_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows == []


@pytest.mark.asyncio
async def test_patient_record_resolver_requires_a_patient_scope(clinical_scope):
    async with AsyncSessionLocal() as db:
        with pytest.raises(ValueError, match="patient_id"):
            await resolve_create_event(
                db,
                clinical_scope["actor"],
                {"title": "No patient"},
            )
