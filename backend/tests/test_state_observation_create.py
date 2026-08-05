"""Integration test for the create_observation value-shape contract
(plan Step 5 wiring).

Real-DB end-to-end: creates a STATE biomarker (with allowed_states), then
exercises ``create_observation`` against it with valid and invalid value[x]
shapes. Verifies the validator is actually wired in (not just unit-tested).
"""
import uuid

import pytest
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import (
    BiomarkerAllowedState,
    BiomarkerDefinition,
    BiomarkerState,
)
from app.models.enums import BiomarkerValueType, CatalogScope, CodingSystem
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.services.observation_value_validator import InvalidObservationValue
from app.services.fhir_service import create_observation


V3 = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


@pytest.fixture(autouse=True)
async def _scrub_test_rows():
    """Scrub every row created by these tests before AND after each test.

    Uses the ``__test_`` slug prefix convention so the cleanup is idempotent
    even if a prior test crashed mid-way (the ``biomarker_states`` unique
    ``(code, system)`` constraint would otherwise block re-runs)."""
    await _cleanup_all()
    yield
    await _cleanup_all()


async def _cleanup_all():
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "DELETE FROM fhir_observations "
                "WHERE code->>'text' IN ('SARS-CoV-2 PCR', 'Wound culture', 'Glucose')"
            )
        )
        await session.execute(
            text(
                "DELETE FROM biomarker_allowed_states WHERE biomarker_id IN "
                "(SELECT id FROM biomarker_definitions WHERE slug LIKE '__test_%')"
            )
        )
        await session.execute(
            text("DELETE FROM biomarker_definitions WHERE slug LIKE '__test_%'")
        )
        await session.execute(
            text("DELETE FROM biomarker_states WHERE slug LIKE '__test_%'")
        )
        await session.execute(
            text("DELETE FROM fhir_patients WHERE name->>'family' = 'State'")
        )
        await session.execute(
            text("DELETE FROM tenants WHERE slug LIKE 'state-%'")
        )
        await session.commit()


async def _seed_state_biomarker(session, *, multi=False):
    """Create a STATE biomarker accepting POS (abnormal) + NEG (normal).

    Looks up existing POS/NEG ``BiomarkerState`` rows first (the canonical
    seed catalog may already be loaded by other tests in the session); falls
    back to creating ad-hoc rows under the ``__test_`` slug namespace when
    they aren't present.
    """
    from sqlalchemy import select

    async def _get_or_create_state(code, system, display):
        existing = (
            await session.execute(
                select(BiomarkerState).where(
                    BiomarkerState.code == code,
                    BiomarkerState.system == system,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        state = BiomarkerState(
            slug=f"__test_{code.lower()}_{uuid.uuid4().hex[:6]}",
            code=code,
            system=system,
            display=display,
        )
        session.add(state)
        await session.flush()
        return state

    pos = await _get_or_create_state("POS", V3, "Positive")
    neg = await _get_or_create_state("NEG", V3, "Negative")
    bio = BiomarkerDefinition(
        slug=f"__test_state_bio_{uuid.uuid4().hex[:8]}",
        coding_system=CodingSystem.LOINC,
        name="Test State Biomarker",
        aliases=[],
        is_telemetry=False,
        value_type=BiomarkerValueType.STATE,
        supports_multi_state=multi,
        scope=CatalogScope.SYSTEM,
    )
    session.add(bio)
    await session.flush()
    session.add_all(
        [
            BiomarkerAllowedState(
                biomarker_id=bio.id, state_id=pos.id, is_normal=False, sort_order=0
            ),
            BiomarkerAllowedState(
                biomarker_id=bio.id, state_id=neg.id, is_normal=True, sort_order=1
            ),
        ]
    )
    await session.flush()
    return bio


async def _seed_tenant_and_patient(session):
    tenant = TenantModel(
        id=uuid.uuid4(), name="StateTest", slug=f"state-{uuid.uuid4().hex[:8]}"
    )
    session.add(tenant)
    await session.flush()
    patient = Patient(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name={"family": "State", "given": ["Test"]},
        gender="UNKNOWN",
    )
    session.add(patient)
    await session.flush()
    return tenant, patient


def _cc(code, system=V3, display=None):
    coding = [{"code": code, "system": system}]
    if display:
        coding[0]["display"] = display
    return {"coding": coding}


@pytest.mark.asyncio
async def test_create_observation_state_happy_path():
    """A STATE biomarker accepts a valueCodeableConcept with a coding from its
    allowed set; the row is persisted with raw_value NULL."""
    async with AsyncSessionLocal() as session:
        bio = await _seed_state_biomarker(session)
        tenant, patient = await _seed_tenant_and_patient(session)
        await session.commit()

    obs = await create_observation(
        {
            "status": "final",
            "code": {"text": "SARS-CoV-2 PCR"},
            "subject": {"reference": f"Patient/{patient.id}"},
            "biomarker_id": str(bio.id),
            "value_codeable_concept": _cc("POS", display="Positive"),
        },
        tenant_id=tenant.id,
    )
    assert obs is not None
    assert obs.value_codeableConcept == _cc("POS", display="Positive")
    assert obs.value_quantity is None
    assert obs.raw_value is None, "STATE observations must not carry raw_value"
    assert obs.normalized_value is None


@pytest.mark.asyncio
async def test_create_observation_state_rejects_value_quantity():
    """A STATE biomarker rejects value_quantity (contract violation)."""
    async with AsyncSessionLocal() as session:
        bio = await _seed_state_biomarker(session)
        tenant, patient = await _seed_tenant_and_patient(session)
        await session.commit()

    with pytest.raises(InvalidObservationValue) as exc:
        await create_observation(
            {
                "status": "final",
                "code": {"text": "SARS-CoV-2 PCR"},
                "subject": {"reference": f"Patient/{patient.id}"},
                "biomarker_id": str(bio.id),
                "value_quantity": {"value": 1.0, "unit": "x"},
            },
            tenant_id=tenant.id,
        )
    assert "STATE" in str(exc.value)


@pytest.mark.asyncio
async def test_create_observation_state_rejects_unknown_code():
    """A STATE biomarker rejects a valueCodeableConcept whose coding is outside
    its allowed set (the hard contract from Decision §5)."""
    async with AsyncSessionLocal() as session:
        bio = await _seed_state_biomarker(session)
        tenant, patient = await _seed_tenant_and_patient(session)
        await session.commit()

    with pytest.raises(InvalidObservationValue) as exc:
        await create_observation(
            {
                "status": "final",
                "code": {"text": "SARS-CoV-2 PCR"},
                "subject": {"reference": f"Patient/{patient.id}"},
                "biomarker_id": str(bio.id),
                "value_codeable_concept": _cc(
                    "WITHIN_LIMITS",
                    system="urn:uuid:health-assistant:custom-state",
                ),
            },
            tenant_id=tenant.id,
        )
    assert "allowed_states" in str(exc.value)


@pytest.mark.asyncio
async def test_create_observation_multi_state_requires_component():
    """A multi-state biomarker requires component[] with >=2 entries."""
    async with AsyncSessionLocal() as session:
        bio = await _seed_state_biomarker(session, multi=True)
        tenant, patient = await _seed_tenant_and_patient(session)
        await session.commit()

    # Top-level valueCodeableConcept is rejected on a multi-state biomarker.
    with pytest.raises(InvalidObservationValue):
        await create_observation(
            {
                "status": "final",
                "code": {"text": "Wound culture"},
                "subject": {"reference": f"Patient/{patient.id}"},
                "biomarker_id": str(bio.id),
                "value_codeable_concept": _cc("POS"),
            },
            tenant_id=tenant.id,
        )

    # The happy multi-state path: component[] with two entries.
    obs = await create_observation(
        {
            "status": "final",
            "code": {"text": "Wound culture"},
            "subject": {"reference": f"Patient/{patient.id}"},
            "biomarker_id": str(bio.id),
            "component": [
                {
                    "code": {"coding": [{"code": "staph-aureus"}]},
                    "valueCodeableConcept": _cc("POS"),
                },
                {
                    "code": {"coding": [{"code": "e-coli"}]},
                    "valueCodeableConcept": _cc("NEG"),
                },
            ],
        },
        tenant_id=tenant.id,
    )
    assert obs is not None
    assert obs.component is not None
    assert len(obs.component) == 2


@pytest.mark.asyncio
async def test_create_observation_quantity_still_works():
    """A QUANTITY biomarker (the legacy default) still accepts value_quantity
    and populates raw_value from it."""
    async with AsyncSessionLocal() as session:
        bio = BiomarkerDefinition(
            slug=f"__test_qty_bio_{uuid.uuid4().hex[:8]}",
            coding_system=CodingSystem.LOINC,
            name="Test Quantity",
            aliases=[],
            is_telemetry=False,
            value_type=BiomarkerValueType.QUANTITY,
            scope=CatalogScope.SYSTEM,
        )
        session.add(bio)
        tenant, patient = await _seed_tenant_and_patient(session)
        await session.commit()

    obs = await create_observation(
        {
            "status": "final",
            "code": {"text": "Glucose"},
            "subject": {"reference": f"Patient/{patient.id}"},
            "biomarker_id": str(bio.id),
            "value_quantity": {"value": 5.5, "unit": "mmol/L"},
        },
        tenant_id=tenant.id,
    )
    assert obs is not None
    assert obs.value_quantity == {"value": 5.5, "unit": "mmol/L"}
    assert obs.raw_value == 5.5
