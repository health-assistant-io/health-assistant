import pytest
from httpx import AsyncClient
from unittest.mock import MagicMock, AsyncMock
import uuid


class MockUser:
    def __init__(self):
        self.id = "65daba01-2bcb-4b46-9f2f-de9352c209d6"
        self.user_id = self.id
        # Catalog writes require ADMIN/MANAGER+ (Phase 1 RBAC normalization);
        # the mock exercises the create path, so it carries an ADMIN role.
        self.role = "ADMIN"
        self.tenant_id = str(uuid.uuid4())

    def get(self, key, default=None):
        return getattr(self, key, default)


def override_get_current_user():
    return MockUser()


class MockResult:
    def __init__(self, data):
        self.data = data

    def scalars(self):
        class MockScalars:
            def all(self_inner):
                return self.data

            def first(self_inner):
                return self.data[0] if self.data else None

        return MockScalars()

    def scalar_one_or_none(self):
        return self.data[0] if self.data else None

    def scalar_one(self):
        if not self.data:
            raise RuntimeError("scalar_one called on empty MockResult")
        return self.data[0]

    def first(self):
        return self.data[0] if self.data else None

    def all(self):
        return self.data


def _mock_biomarker(value_type="quantity", **overrides):
    """Build a MagicMock BiomarkerDefinition for the create/reload paths."""
    from app.models.enums import BiomarkerValueType

    bio = MagicMock()
    bio.id = overrides.get("id", uuid.uuid4())
    bio.slug = overrides.get("slug", "new-biomarker")
    bio.name = overrides.get("name", "New Biomarker")
    bio.category = overrides.get("category", None)
    bio.aliases = overrides.get("aliases", [])
    bio.preferred_unit_id = overrides.get("preferred_unit_id", None)
    bio.info = overrides.get("info", None)
    bio.coding_system = overrides.get("coding_system", "loinc")
    bio.code = overrides.get("code", None)
    bio.meta_data = overrides.get("meta_data", {})
    bio.reference_range_min = overrides.get("reference_range_min", None)
    bio.reference_range_max = overrides.get("reference_range_max", None)
    bio.is_telemetry = overrides.get("is_telemetry", False)
    bio.value_type = overrides.get("value_type", BiomarkerValueType(value_type))
    bio.supports_multi_state = overrides.get("supports_multi_state", False)
    bio.allowed_states = overrides.get("allowed_states", [])
    bio.reference_ranges = overrides.get("reference_ranges", [])
    return bio


def get_mock_db(data_to_return):
    async def mock_execute(*args, **kwargs):
        return MockResult(data_to_return)

    mock_db = AsyncMock()
    mock_db.execute = mock_execute
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    async def get_db_override():
        yield mock_db

    return get_db_override


@pytest.mark.asyncio
async def test_get_biomarkers(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    # Mock BiomarkerDefinition
    mock_bio = MagicMock()
    mock_bio.id = uuid.uuid4()
    mock_bio.slug = "glucose"
    mock_bio.name = "Glucose"
    mock_bio.category = "Blood Laboratory"  # read-only property -> concept name
    mock_bio.aliases = ["GLU"]
    mock_bio.preferred_unit_id = uuid.uuid4()
    mock_bio.info = "Test info"
    mock_bio.coding_system = "loinc"
    mock_bio.code = "1234-5"
    mock_bio.meta_data = {}
    # State-biomarker fields (default QUANTITY shape — no allowed_states).
    from app.models.enums import BiomarkerValueType

    mock_bio.value_type = BiomarkerValueType.QUANTITY
    mock_bio.supports_multi_state = False
    mock_bio.allowed_states = []
    mock_bio.reference_ranges = []

    app.dependency_overrides[get_db] = get_mock_db([(mock_bio, "mg/dL")])

    response = await async_client.get("/api/v1/biomarkers/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["slug"] == "glucose"
    assert data[0]["name"] == "Glucose"
    assert "GLU" in data[0]["aliases"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_units(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    # Mock Unit
    mock_unit = MagicMock()
    mock_unit.id = uuid.uuid4()
    mock_unit.symbol = "mg/dL"
    mock_unit.name = "Milligrams per deciliter"
    mock_unit.quantity_type = "mass_concentration"
    mock_unit.conversion_multiplier = 1.0

    app.dependency_overrides[get_db] = get_mock_db([mock_unit])

    response = await async_client.get("/api/v1/biomarkers/units")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["symbol"] == "mg/dL"
    assert data[0]["quantity_type"] == "mass_concentration"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_biomarker(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    mock_unit = MagicMock()
    mock_unit.id = uuid.uuid4()
    mock_bio = _mock_biomarker(
        slug="new-biomarker", name="New Biomarker", aliases=["NB"]
    )

    async def mock_execute(*args, **kwargs):
        query = args[0] if args else kwargs.get("statement")
        query_str = str(query).lower()
        # If the query is specifically selecting just the symbol column
        # and NOT selecting other columns like units.name
        if (
            "select units.symbol \nfrom" in query_str
            or "select unit.symbol \nfrom" in query_str
        ):
            return MockResult(["mg/dL"])
        # State-biomarker reload query (selects BiomarkerDefinition by id).
        if "from biomarker_definitions" in query_str:
            return MockResult([mock_bio])
        # Fallback for Unit creation or other queries (select(Unit)...)
        return MockResult([mock_unit])

    mock_db = AsyncMock()
    mock_db.execute = mock_execute
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    async def mock_refresh(instance):
        instance.id = uuid.uuid4()

    mock_db.refresh = mock_refresh

    async def get_db_override():
        yield mock_db

    app.dependency_overrides[get_db] = get_db_override

    response = await async_client.post(
        "/api/v1/biomarkers/",
        json={
            "slug": "new-biomarker",
            "name": "New Biomarker",
            "category": "custom",
            "aliases": ["NB"],
            "preferred_unit_symbol": "mg/dL",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "new-biomarker"
    assert data["name"] == "New Biomarker"
    assert "NB" in data["aliases"]
    assert data.get("is_telemetry") is False
    assert mock_db.commit.called

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_telemetry_biomarker(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    mock_unit = MagicMock()
    mock_unit.id = uuid.uuid4()
    mock_bio = _mock_biomarker(
        slug="heart-rate",
        name="Heart Rate",
        aliases=["HR"],
        is_telemetry=True,
    )

    async def mock_execute(*args, **kwargs):
        query = args[0] if args else kwargs.get("statement")
        query_str = str(query).lower()

        if (
            "select units.symbol \nfrom" in query_str
            or "select unit.symbol \nfrom" in query_str
        ):
            return MockResult(["bpm"])
        if "from biomarker_definitions" in query_str:
            return MockResult([mock_bio])

        return MockResult([mock_unit])

    mock_db = AsyncMock()
    mock_db.execute = mock_execute
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    async def mock_refresh(instance):
        instance.id = uuid.uuid4()

    mock_db.refresh = mock_refresh

    async def get_db_override():
        yield mock_db

    app.dependency_overrides[get_db] = get_db_override

    response = await async_client.post(
        "/api/v1/biomarkers/",
        json={
            "slug": "heart-rate",
            "name": "Heart Rate",
            "category": "telemetry",
            "aliases": ["HR"],
            "preferred_unit_symbol": "bpm",
            "is_telemetry": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "heart-rate"
    assert data["is_telemetry"] is True
    assert mock_db.commit.called

    app.dependency_overrides = {}
