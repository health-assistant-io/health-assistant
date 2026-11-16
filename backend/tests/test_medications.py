import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.fhir.medication import MedicationCatalog


class MockUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.user_id = self.id
        self.role = "USER"
        self.tenant_id = uuid.uuid4()

    def get(self, key, default=None):
        return getattr(self, key, default)


def override_get_current_user():
    return MockUser()


@pytest.mark.asyncio
@patch("app.services.clinical.medication_service.get_catalog_medication")
async def test_get_catalog_medication_details(mock_get_details, async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = override_get_current_user

    catalog_id = uuid.uuid4()
    mock_catalog_entry = MagicMock(spec=MedicationCatalog)
    mock_catalog_entry.id = catalog_id
    mock_catalog_entry.name = "Test Med"
    mock_catalog_entry.description = "Test Description"
    mock_catalog_entry.indications = "Test Indications"
    mock_catalog_entry.side_effects = ["Nausea"]
    mock_catalog_entry.contraindications = "None"
    mock_catalog_entry.dosage_info = "Take daily"
    mock_catalog_entry.tenant_id = None
    # Fields the extended MedicationCatalogResponse now projects.
    mock_catalog_entry.seed_key = None
    mock_catalog_entry.coding_system = None
    mock_catalog_entry.code = None
    mock_catalog_entry.class_concept_id = None
    mock_catalog_entry.class_concept_slug = None
    mock_catalog_entry.class_concept_name = None
    mock_catalog_entry.scope = None
    mock_catalog_entry.created_by = None
    mock_catalog_entry.class_concept = None

    # The endpoint serializes through ``serialize_catalog_row`` (which reads
    # ``to_dict()``), so the mock carries the projected row dict.
    mock_catalog_entry.to_dict.return_value = {
        "id": str(catalog_id),
        "name": "Test Med",
        "seed_key": None,
        "coding_system": None,
        "code": None,
        "description": "Test Description",
        "indications": "Test Indications",
        "side_effects": ["Nausea"],
        "contraindications": "None",
        "dosage_info": "Take daily",
        "class_concept_id": None,
        "class_concept_slug": None,
        "class_concept_name": None,
        "scope": None,
        "tenant_id": None,
        "created_by": None,
        "created_at": None,
        "updated_at": None,
    }
    mock_get_details.return_value = mock_catalog_entry

    response = await async_client.get(f"/api/v1/medications/catalog/{catalog_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Med"
    assert "Nausea" in data["side_effects"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.services.clinical.medication_service.get_catalog_medication")
async def test_get_catalog_medication_not_found(mock_get_details, async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_details.return_value = None

    catalog_id = uuid.uuid4()
    response = await async_client.get(f"/api/v1/medications/catalog/{catalog_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Medication not found in catalog"

    app.dependency_overrides = {}


def test_medication_status_enum():
    from app.models.enums import MedicationStatus

    # Verify enum names match expected uppercase values
    assert MedicationStatus.ACTIVE == "ACTIVE"
    assert MedicationStatus.COMPLETED == "COMPLETED"
    assert MedicationStatus.ENTERED_IN_ERROR == "ENTERED_IN_ERROR"
    assert MedicationStatus.ON_HOLD == "ON_HOLD"

    # Verify we can access by uppercase names
    assert MedicationStatus("ACTIVE") == MedicationStatus.ACTIVE
    assert MedicationStatus("ENTERED_IN_ERROR") == MedicationStatus.ENTERED_IN_ERROR
