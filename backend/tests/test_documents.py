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


def override_get_db():
    db_mock = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MockUser()
    db_mock.execute.return_value = mock_result
    yield db_mock


class MockDocumentModel:
    def __init__(self):
        self.id = "123e4567-e89b-12d3-a456-426614174000"
        self.filename = "test_lab_report.pdf"
        self.content_type = "application/pdf"
        self.status = "completed"
        self.extracted_text = "Blood Glucose: 95 mg/dL"
        self.owner_id = "65daba01-2bcb-4b46-9f2f-de9352c209d6"
        self.tenant_id = str(uuid.uuid4())

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "content_type": self.content_type,
            "status": self.status,
            "extracted_text": self.extracted_text,
            "owner_id": self.owner_id,
            "tenant_id": self.tenant_id,
        }


@pytest.fixture
def mock_document():
    return MockDocumentModel()


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.documents_db.get_document")
async def test_get_document(
    mock_get_document, async_client: AsyncClient, mock_document
):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    mock_get_document.return_value = mock_document

    response = await async_client.get(
        "/api/v1/documents/123e4567-e89b-12d3-a456-426614174000"
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "test_lab_report.pdf"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.documents_db.get_documents")
async def test_list_documents(
    mock_get_documents, async_client: AsyncClient, mock_document
):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    mock_get_documents.return_value = [mock_document]

    response = await async_client.get("/api/v1/documents")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == mock_document.id

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.documents_db.delete_document")
@patch("app.api.v1.endpoints.documents_db.get_document")
async def test_delete_document(
    mock_get_document, mock_delete_document, async_client: AsyncClient, mock_document
):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    mock_get_document.return_value = mock_document
    mock_delete_document.return_value = True

    response = await async_client.delete(
        "/api/v1/documents/123e4567-e89b-12d3-a456-426614174000"
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Document deleted successfully"

    app.dependency_overrides = {}
