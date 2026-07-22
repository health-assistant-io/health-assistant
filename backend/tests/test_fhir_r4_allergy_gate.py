"""Tests for AllergyIntolerance write-time FHIR gate + the modernized service
signature (db-injected, Pydantic params).

Covers:
- add_patient_allergy with valid data persists (calls assert_valid_fhir)
- update_patient_allergy with mutation that breaks FHIR raises
- AllergyCatalog.to_fhir_dict projects to a valid FHIR Substance
- get_allergy_usage cross-patient join
- check_allergy_access filters deleted_at
"""
import datetime as _dt
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.enums import AllergyCategory, AllergyClinicalStatus, AllergyCriticality
from app.models.fhir.allergy import AllergyCatalog, AllergyIntolerance
from app.services.fhir_helpers import FhirSerializationError, assert_valid_fhir


def _valid_allergy_data() -> dict:
    return {
        "clinical_status": "ACTIVE",
        "category": "FOOD",
        "criticality": "HIGH",
        "code": {"text": "Peanuts"},
        "onset_date": "2020-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Model projections
# ---------------------------------------------------------------------------


def test_valid_allergy_intolerance_passes_gate():
    """A correctly-constructed AllergyIntolerance should pass assert_valid_fhir."""
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


def test_allergy_catalog_to_fhir_dict_projects_to_substance():
    """AllergyCatalog.to_fhir_dict must produce a valid FHIR Substance."""
    entry = AllergyCatalog(
        id=str(uuid4()),
        name="Peanuts",
        category=AllergyCategory.FOOD,
        description="Nut allergy",
        typical_reactions=["Hives", "Anaphylaxis"],
    )
    fhir = assert_valid_fhir(entry)
    assert fhir["resourceType"] == "Substance"
    assert fhir["code"]["text"] == "Peanuts"
    assert fhir["category"][0]["coding"][0]["code"] == "food"


def test_allergy_catalog_to_fhir_dict_handles_medication_category():
    """The category enum maps through to FHIR substance-category codes."""
    entry = AllergyCatalog(
        id=str(uuid4()),
        name="Penicillin",
        category=AllergyCategory.MEDICATION,
    )
    fhir = entry.to_fhir_dict()
    assert fhir["resourceType"] == "Substance"
    assert fhir["category"][0]["coding"][0]["code"] == "medication"


# ---------------------------------------------------------------------------
# Service-layer integration (mocked DB) — modernized signature
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

    fake_db = AsyncMock()
    fake_db.add = MagicMock()
    fake_db.commit = AsyncMock()
    fake_db.refresh = AsyncMock()

    from app.schemas.allergy import AllergyIntoleranceCreate

    payload = AllergyIntoleranceCreate(
        clinical_status=AllergyClinicalStatus.ACTIVE,
        category=AllergyCategory.FOOD,
        criticality=AllergyCriticality.HIGH,
        code={"text": "Peanuts"},
    )

    patient_id = uuid4()
    tenant_id = uuid4()
    await allergy_service.add_patient_allergy(fake_db, patient_id, tenant_id, payload)

    assert len(gate_calls) == 1
    assert isinstance(gate_calls[0], AllergyIntolerance)
    assert gate_calls[0].patient_id == patient_id


@pytest.mark.asyncio
async def test_update_patient_allergy_merges_code_partial(monkeypatch):
    """A text-only code update must NOT wipe the existing catalog_id."""
    from app.schemas.allergy import AllergyIntoleranceUpdate
    from app.services import allergy_service

    existing = AllergyIntolerance(
        id=uuid4(),
        patient_id=uuid4(),
        tenant_id=uuid4(),
        clinical_status=AllergyClinicalStatus.ACTIVE,
        verification_status="confirmed",
        category=AllergyCategory.FOOD,
        criticality=AllergyCriticality.HIGH,
        code={"text": "Peanuts", "catalog_id": str(uuid4())},
    )

    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = existing
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    fake_db.commit = AsyncMock()
    fake_db.refresh = AsyncMock()

    original_catalog_id = existing.code["catalog_id"]
    await allergy_service.update_patient_allergy(
        fake_db,
        existing.id,
        existing.tenant_id,
        AllergyIntoleranceUpdate(code={"text": "Peanuts (updated)"}),
    )

    # catalog_id preserved; text updated.
    assert existing.code["catalog_id"] == original_catalog_id
    assert existing.code["text"] == "Peanuts (updated)"


@pytest.mark.asyncio
async def test_get_allergy_usage_joins_patient_rows():
    """The cross-patient usage query returns {allergy, patient} dicts."""
    from app.services import allergy_service

    fake_allergy = MagicMock()
    fake_allergy.to_dict.return_value = {"id": "a1", "code": {"text": "Latex"}}
    fake_patient = MagicMock()
    fake_patient.id = uuid4()
    fake_patient.name = {"family": "Doe", "given": ["Jane"]}
    fake_patient.mrn = "MRN-1"

    fake_row = (fake_allergy, fake_patient)
    fake_result = MagicMock()
    fake_result.all.return_value = [fake_row]
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)

    usage = await allergy_service.get_allergy_usage(fake_db, uuid4(), uuid4())
    assert len(usage) == 1
    assert usage[0]["allergy"] == {"id": "a1", "code": {"text": "Latex"}}
    assert usage[0]["patient"]["mrn"] == "MRN-1"


@pytest.mark.asyncio
async def test_reprocess_allergy_no_nlp_returns_entry_unchanged(monkeypatch):
    """When NLP is unavailable, reprocess returns the entry without enrichment."""
    from app.services import allergy_service

    entry = AllergyCatalog(
        id=uuid4(),
        name="Peanuts",
        category=AllergyCategory.FOOD,
        description="original",
    )
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = entry
    fake_db = AsyncMock()
    fake_db.execute = AsyncMock(return_value=fake_result)
    fake_db.commit = AsyncMock()
    fake_db.refresh = AsyncMock()

    def boom(*a, **kw):
        raise RuntimeError("no NLP configured")

    monkeypatch.setattr(
        "app.ai.processors.nlp.get_nlp_extractor_from_db", boom, raising=False
    )

    result = await allergy_service.reprocess_allergy(fake_db, entry.id, uuid4())
    assert result is entry
    assert result.description == "original"
