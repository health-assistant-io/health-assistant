import pytest
from httpx import AsyncClient
from unittest.mock import patch, MagicMock, AsyncMock
import uuid


class MockUser:
    def __init__(self):
        self.id = "65daba01-2bcb-4b46-9f2f-de9352c209d6"
        self.user_id = self.id
        self.role = "user"
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

        return MockScalars()

    def scalar_one_or_none(self):
        return self.data[0] if self.data else None

    def all(self):
        return self.data


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
    mock_bio.category = "blood_laboratory"
    mock_bio.aliases = ["GLU"]
    mock_bio.preferred_unit_id = uuid.uuid4()
    mock_bio.info = "Test info"
    mock_bio.coding_system = "loinc"
    mock_bio.code = "1234-5"
    mock_bio.meta_data = {}

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
async def test_get_groups(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    # Mock Group
    mock_group = MagicMock()
    mock_group.id = uuid.uuid4()
    mock_group.name = "Lipid Panel"
    mock_group.type = "Panel"

    # Mock Biomarker inside group
    mock_bio = MagicMock()
    mock_bio.id = uuid.uuid4()
    mock_bio.slug = "hdl"
    mock_bio.name = "HDL"
    mock_bio.category = "blood_laboratory"
    mock_bio.aliases = ["HDL-C"]

    async def mock_execute_complex(query):
        query_str = str(query).lower()
        if "biomarker_groups" in query_str and "join" not in query_str:
            return MockResult([mock_group])
        return MockResult([mock_bio])

    mock_db = AsyncMock()
    mock_db.execute = mock_execute_complex

    async def get_db_override():
        yield mock_db

    app.dependency_overrides[get_db] = get_db_override

    response = await async_client.get("/api/v1/biomarkers/groups")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Lipid Panel"
    assert len(data[0]["members"]) == 1
    assert data[0]["members"][0]["slug"] == "hdl"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_biomarker(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    mock_unit = MagicMock()
    mock_unit.id = uuid.uuid4()

    async def mock_execute(*args, **kwargs):
        query = args[0] if args else kwargs.get("statement")
        query_str = str(query).lower()
        if "from units" in query_str and "unit.symbol" not in query_str and "units.symbol" not in query_str:
            # When lookup full unit (e.g. by symbol or ID to check existence)
            return MockResult([mock_unit])
        # The query asks specifically for unit.symbol which is an attribute return value
        # SQLAlchemy select(Unit.symbol) translates to selecting just the column
        if "unit.symbol" in query_str or "unit_symbol" in query_str or "select units.symbol" in query_str or "units.symbol" in query_str:
             return MockResult(["mg/dL"])
        # Fallback for Unit creation or other queries
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
    assert mock_db.commit.called

    app.dependency_overrides = {}
