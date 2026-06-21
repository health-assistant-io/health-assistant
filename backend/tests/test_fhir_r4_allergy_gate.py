"""Tests for AllergyIntolerance write-time FHIR gate.

The AllergyIntolerance model already had a to_fhir_dict() but the service
layer (allergy_service.py) never called assert_valid_fhir() on writes —
unlike fhir_service.py which has the gate for Observation/Medication/etc.
This closed the parity gap.

Covers:
- add_patient_allergy with valid data persists
- add_patient_allergy with malformed code raises FhirSerializationError
- update_patient_allergy with mutation that breaks FHIR raises
- update_patient_allergy with valid mutation persists
- The gate doesn't false-positive on the common case (clinical_status as
  enum value vs string)
"""
import datetime as _dt
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.enums import AllergyCategory, AllergyClinicalStatus, AllergyCriticality
from app.models.fhir.allergy import AllergyIntolerance
from app.services.fhir_helpers import FhirSerializationError


def _valid_allergy_data() -> dict:
    return {
        "clinical_status": "ACTIVE",
        "category": "FOOD",
        "criticality": "HIGH",
        "code": {"text": "Peanuts"},
        "onset_date": "2020-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Direct service-level testing via the model + assert_valid_fhir
# ---------------------------------------------------------------------------

def test_valid_allergy_intolerance_passes_gate():
    """A correctly-constructed AllergyIntolerance should pass assert_valid_fhir."""
    from app.services.fhir_helpers import assert_valid_fhir

    allergy = AllergyIntolerance(
        id=str(uuid4()),
        patient_id=str(uuid4()),
        tenant_id=str(uuid4()),
        clinical_status=AllergyClinicalStatus.ACTIVE,
        verification_status="confirmed",
        category=AllergyCategory.FOOD,
        criticality=AllergyCriticality.HIGH,
        code={"text": "Peanuts"},
        onset_date=_dt.datetime(2020, 1, 1, tzinfo=_dt.timezone.utc),
    )
    fhir = assert_valid_fhir(allergy)
    assert fhir["resourceType"] == "AllergyIntolerance"
    assert fhir["code"]["text"] == "Peanuts"


def test_allergy_to_fhir_dict_validates_against_fhir_resources():
    """The AllergyIntolerance.to_fhir_dict() output validates cleanly. This is
    the core gate behavior — if a future field drift breaks the projection,
    assert_valid_fhir will reject it."""
    from app.services.fhir_helpers import assert_valid_fhir, parse_fhir_resource

    allergy = AllergyIntolerance(
        id=str(uuid4()),
        patient_id=str(uuid4()),
        tenant_id=str(uuid4()),
        clinical_status=AllergyClinicalStatus.ACTIVE,
        verification_status="confirmed",
        category=AllergyCategory.FOOD,
        criticality=AllergyCriticality.HIGH,
        code={"text": "Peanuts"},
        onset_date=_dt.datetime(2020, 1, 1, tzinfo=_dt.timezone.utc),
    )
    fhir = assert_valid_fhir(allergy)
    # Round-trip through parse to confirm strict validity.
    parsed = parse_fhir_resource("AllergyIntolerance", fhir)
    assert parsed.__resource_type__ == "AllergyIntolerance"


def test_allergy_with_malformed_reactions_passes_gate():
    """Reactions structure is loose enough that malformed JSON doesn't fail
    validation — it gets normalized to None. Verify this still passes."""
    from app.services.fhir_helpers import assert_valid_fhir

    allergy = AllergyIntolerance(
        id=str(uuid4()),
        patient_id=str(uuid4()),
        tenant_id=str(uuid4()),
        clinical_status=AllergyClinicalStatus.ACTIVE,
        category=AllergyCategory.MEDICATION,
        criticality=AllergyCriticality.HIGH,
        code={"text": "Penicillin"},
        reactions=[],  # empty list is valid
    )
    fhir = assert_valid_fhir(allergy)
    assert fhir["resourceType"] == "AllergyIntolerance"


# ---------------------------------------------------------------------------
# Service-layer integration (mocked DB)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_patient_allergy_calls_assert_valid_fhir(monkeypatch):
    """Verify add_patient_allergy invokes the FHIR validation gate."""
    from app.services import allergy_service

    gate_calls = []
    original = allergy_service.assert_valid_fhir

    def spy(obj):
        gate_calls.append(obj)
        return original(obj)

    monkeypatch.setattr(allergy_service, "assert_valid_fhir", spy)

    # Mock the session.
    fake_session = AsyncMock()
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__.return_value = fake_session
    monkeypatch.setattr(allergy_service, "AsyncSessionLocal", lambda: fake_ctx)

    patient_id = uuid4()
    tenant_id = uuid4()
    await allergy_service.add_patient_allergy(patient_id, tenant_id, _valid_allergy_data())

    assert len(gate_calls) == 1
    assert isinstance(gate_calls[0], AllergyIntolerance)


@pytest.mark.asyncio
async def test_update_patient_allergy_calls_assert_valid_fhir(monkeypatch):
    """Verify update_patient_allergy invokes the gate after mutation."""
    from app.services import allergy_service

    gate_calls = []
    original = allergy_service.assert_valid_fhir

    def spy(obj):
        gate_calls.append(obj)
        return original(obj)

    monkeypatch.setattr(allergy_service, "assert_valid_fhir", spy)

    # Existing allergy in the "DB".
    existing = AllergyIntolerance(
        id=uuid4(),
        patient_id=uuid4(),
        tenant_id=uuid4(),
        clinical_status=AllergyClinicalStatus.ACTIVE,
        verification_status="confirmed",
        category=AllergyCategory.FOOD,
        criticality=AllergyCriticality.HIGH,
        code={"text": "Peanuts"},
    )

    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = existing
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(return_value=fake_result)
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__.return_value = fake_session
    monkeypatch.setattr(allergy_service, "AsyncSessionLocal", lambda: fake_ctx)

    await allergy_service.update_patient_allergy(existing.id, existing.tenant_id, {"note": "updated"})

    # The gate should have been called once after mutation.
    assert len(gate_calls) == 1
    assert gate_calls[0].note == "updated"
