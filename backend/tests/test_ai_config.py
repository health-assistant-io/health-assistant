import pytest
import pytest_asyncio
from httpx import AsyncClient
from uuid import uuid4
from unittest.mock import patch

from app.main import app
from app.core.database import AsyncSessionLocal
from app.models.ai_provider_model import AIProviderModel, AIModel, AITaskAssignment
from app.schemas.ai_config import (
    AIProviderCreate,
    AIModelCreate,
    AITaskAssignmentCreate,
)
from app.core.security import get_current_user
from app.schemas.user import TokenData


def override_get_current_user():
    """Mock current user for tests"""
    uid = uuid4()
    return TokenData(
        user_id=uid,
        sub="test@example.com",
        role="SYSTEM_ADMIN",
        tenant_id=uuid4(),
    )


@pytest_asyncio.fixture
async def test_client():
    """Create test client with database session"""
    from httpx import ASGITransport

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def authenticated_client(test_client: AsyncClient):
    """Create authenticated test client"""
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield test_client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_provider(authenticated_client: AsyncClient):
    """Create a test provider"""
    provider_data = {
        "name": "Test Provider",
        "provider_type": "openai",
        "api_base": "https://api.test-openai.com/v1",
        "api_key": "test-key-123",
        "is_default": True,
        "is_active": True,
    }
    response = await authenticated_client.post(
        "/api/v1/ai-config/providers", json=provider_data
    )
    assert response.status_code == 201
    return response.json()


@pytest_asyncio.fixture
async def test_model(authenticated_client: AsyncClient, test_provider: dict):
    """Create a test model for the provider"""
    model_data = {
        "provider_id": test_provider["id"],
        "name": "Test Model",
        "model_name": "gpt-4-test",
        "description": "Test model for OCR",
        "max_tokens": 4096,
        "temperature": 0.0,
        "is_default": True,
        "is_active": True,
    }
    response = await authenticated_client.post(
        f"/api/v1/ai-config/providers/{test_provider['id']}/models", json=model_data
    )
    assert response.status_code == 201
    return response.json()


class TestAIProviderEndpoints:
    """Test AI provider CRUD endpoints"""

    async def test_create_provider(self, authenticated_client: AsyncClient):
        """Test creating a new provider"""
        provider_data = {
            "name": "OpenAI Production",
            "provider_type": "openai",
            "api_base": "https://api.openai.com/v1",
            "api_key": "sk-test-key",
            "is_default": False,
            "is_active": True,
        }
        response = await authenticated_client.post(
            "/api/v1/ai-config/providers", json=provider_data
        )
        assert response.status_code == 201
        assert response.json()["name"] == "OpenAI Production"
        assert response.json()["provider_type"] == "openai"

    async def test_get_providers(
        self, authenticated_client: AsyncClient, test_provider: dict
    ):
        """Test listing providers"""
        response = await authenticated_client.get("/api/v1/ai-config/providers")
        assert response.status_code == 200
        providers = response.json()
        assert len(providers) >= 1
        assert any(p["id"] == test_provider["id"] for p in providers)

    async def test_get_provider(
        self, authenticated_client: AsyncClient, test_provider: dict
    ):
        """Test getting a single provider"""
        response = await authenticated_client.get(
            f"/api/v1/ai-config/providers/{test_provider['id']}"
        )
        assert response.status_code == 200
        assert response.json()["id"] == test_provider["id"]
        assert response.json()["name"] == test_provider["name"]

    async def test_get_provider_with_models(
        self, authenticated_client: AsyncClient, test_provider: dict, test_model: dict
    ):
        """Test getting provider with models"""
        response = await authenticated_client.get(
            f"/api/v1/ai-config/providers/{test_provider['id']}/with-models"
        )
        assert response.status_code == 200
        assert "models" in response.json()
        assert len(response.json()["models"]) >= 1
        assert response.json()["models"][0]["id"] == test_model["id"]

    async def test_update_provider(
        self, authenticated_client: AsyncClient, test_provider: dict
    ):
        """Test updating a provider"""
        update_data = {"name": "Updated Provider Name", "is_active": False}
        response = await authenticated_client.put(
            f"/api/v1/ai-config/providers/{test_provider['id']}", json=update_data
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Provider Name"
        assert response.json()["is_active"] == False

    async def test_delete_provider(self, authenticated_client: AsyncClient):
        """Test deleting a provider"""
        # Create provider to delete
        provider_data = {
            "name": "Provider to Delete",
            "provider_type": "openai",
            "api_base": "https://api.test.com/v1",
            "api_key": "test-key",
        }
        response = await authenticated_client.post(
            "/api/v1/ai-config/providers", json=provider_data
        )
        assert response.status_code == 201
        provider_id = response.json()["id"]

        # Delete
        response = await authenticated_client.delete(
            f"/api/v1/ai-config/providers/{provider_id}"
        )
        assert response.status_code == 204

        # Verify deletion
        response = await authenticated_client.get(
            f"/api/v1/ai-config/providers/{provider_id}"
        )
        assert response.status_code == 404


class TestAIModelEndpoints:
    """Test AI model CRUD endpoints"""

    async def test_create_model(
        self, authenticated_client: AsyncClient, test_provider: dict
    ):
        """Test creating a new model"""
        model_data = {
            "provider_id": test_provider["id"],
            "name": "GPT-4 Turbo",
            "model_name": "gpt-4-turbo-preview",
            "max_tokens": 8192,
            "temperature": 0.7,
        }
        response = await authenticated_client.post(
            f"/api/v1/ai-config/providers/{test_provider['id']}/models", json=model_data
        )
        assert response.status_code == 201
        assert response.json()["model_name"] == "gpt-4-turbo-preview"

    async def test_get_models_for_provider(
        self, authenticated_client: AsyncClient, test_provider: dict, test_model: dict
    ):
        """Test listing models for a provider"""
        response = await authenticated_client.get(
            f"/api/v1/ai-config/providers/{test_provider['id']}/models"
        )
        assert response.status_code == 200
        models = response.json()
        assert len(models) >= 1
        assert models[0]["id"] == test_model["id"]

    async def test_update_model(
        self, authenticated_client: AsyncClient, test_model: dict
    ):
        """Test updating a model"""
        update_data = {"max_tokens": 16384, "temperature": 0.5}
        response = await authenticated_client.put(
            f"/api/v1/ai-config/models/{test_model['id']}", json=update_data
        )
        assert response.status_code == 200
        assert response.json()["max_tokens"] == 16384
        assert response.json()["temperature"] == 0.5

    async def test_delete_model(
        self, authenticated_client: AsyncClient, test_provider: dict
    ):
        """Test deleting a model"""
        # Create model to delete
        model_data = {
            "provider_id": test_provider["id"],
            "name": "Model to Delete",
            "model_name": "delete-me",
        }
        response = await authenticated_client.post(
            f"/api/v1/ai-config/providers/{test_provider['id']}/models", json=model_data
        )
        assert response.status_code == 201
        model_id = response.json()["id"]

        # Delete
        response = await authenticated_client.delete(
            f"/api/v1/ai-config/models/{model_id}"
        )
        assert response.status_code == 204


class TestAITaskAssignmentEndpoints:
    """Test AI task assignment endpoints"""

    async def test_create_task_assignment(
        self, authenticated_client: AsyncClient, test_provider: dict, test_model: dict
    ):
        """Test creating a task assignment"""
        assignment_data = {
            "task_type": "ocr",
            "provider_id": test_provider["id"],
            "model_id": test_model["id"],
            "is_active": True,
            "priority": 1,
        }
        response = await authenticated_client.post(
            "/api/v1/ai-config/task-assignments", json=assignment_data
        )
        assert response.status_code == 201
        assert response.json()["task_type"] == "ocr"
        assert response.json()["provider_id"] == test_provider["id"]

    async def test_get_task_assignments(self, authenticated_client: AsyncClient):
        """Test listing task assignments"""
        response = await authenticated_client.get("/api/v1/ai-config/task-assignments")
        assert response.status_code == 200
        assignments = response.json()
        assert isinstance(assignments, list)

    async def test_get_active_task_assignment(
        self, authenticated_client: AsyncClient, test_provider: dict, test_model: dict
    ):
        """Test getting active assignment for task type"""
        # Create assignment
        assignment_data = {
            "task_type": "nlp",
            "provider_id": test_provider["id"],
            "model_id": test_model["id"],
            "is_active": True,
        }
        response = await authenticated_client.post(
            "/api/v1/ai-config/task-assignments", json=assignment_data
        )
        assert response.status_code == 201

        # Get active
        response = await authenticated_client.get(
            "/api/v1/ai-config/task-assignments/active/nlp"
        )
        assert response.status_code == 200
        assert response.json()["task_type"] == "nlp"

    async def test_update_task_assignment(
        self, authenticated_client: AsyncClient, test_provider: dict, test_model: dict
    ):
        """Test updating a task assignment"""
        # Create assignment
        assignment_data = {
            "task_type": "medication_interaction",
            "provider_id": test_provider["id"],
            "is_active": True,
        }
        response = await authenticated_client.post(
            "/api/v1/ai-config/task-assignments", json=assignment_data
        )
        assignment_id = response.json()["id"]

        # Update
        update_data = {"model_id": test_model["id"], "priority": 2}
        response = await authenticated_client.put(
            f"/api/v1/ai-config/task-assignments/{assignment_id}", json=update_data
        )
        assert response.status_code == 200
        assert response.json()["model_id"] == test_model["id"]
        assert response.json()["priority"] == 2

    async def test_delete_task_assignment(
        self, authenticated_client: AsyncClient, test_provider: dict
    ):
        """Test deleting a task assignment"""
        # Create assignment
        assignment_data = {
            "task_type": "anomaly_detection",
            "provider_id": test_provider["id"],
            "is_active": True,
        }
        response = await authenticated_client.post(
            "/api/v1/ai-config/task-assignments", json=assignment_data
        )
        assignment_id = response.json()["id"]

        # Delete
        response = await authenticated_client.delete(
            f"/api/v1/ai-config/task-assignments/{assignment_id}"
        )
        assert response.status_code == 204


class TestAIConfigSummary:
    """Test AI configuration summary endpoint"""

    async def test_get_config_summary(
        self, authenticated_client: AsyncClient, test_provider: dict, test_model: dict
    ):
        """Test getting complete AI configuration summary"""
        # Create task assignment
        assignment_data = {
            "task_type": "ocr",
            "provider_id": test_provider["id"],
            "model_id": test_model["id"],
            "is_active": True,
        }
        await authenticated_client.post(
            "/api/v1/ai-config/task-assignments", json=assignment_data
        )

        # Get summary
        response = await authenticated_client.get("/api/v1/ai-config/summary")
        assert response.status_code == 200
        summary = response.json()
        assert "providers" in summary
        assert "models" in summary
        assert "task_assignments" in summary
        assert len(summary["providers"]) >= 1
        assert len(summary["models"]) >= 1

    async def test_get_default_for_task(
        self, authenticated_client: AsyncClient, test_provider: dict, test_model: dict
    ):
        """Test getting default provider/model for task"""
        # Create assignment
        assignment_data = {
            "task_type": "ocr",
            "provider_id": test_provider["id"],
            "model_id": test_model["id"],
            "is_active": True,
        }
        await authenticated_client.post(
            "/api/v1/ai-config/task-assignments", json=assignment_data
        )

        # Get default
        response = await authenticated_client.get(
            "/api/v1/ai-config/default-for-task/ocr"
        )
        assert response.status_code == 200
        assert response.json()["provider"]["id"] == test_provider["id"]
