"""
Simple integration tests for AI Config endpoints
Tests the basic CRUD operations without complex fixtures
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID
from datetime import datetime

from app.main import app
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.enums import AIScope
from app.ai.schemas.config import (
    AIProviderResponse,
    AIModelResponse,
    AITaskAssignmentResponse,
)


def mock_user():
    """Create mock user for testing"""
    uid = uuid4()
    return TokenData(
        user_id=uid,
        sub="test@example.com",
        role="SYSTEM_ADMIN",
        tenant_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_create_provider(async_client):
    """Test creating a provider"""
    app.dependency_overrides[get_current_user] = mock_user

    provider_id = uuid4()

    # Mock the service methods
    with patch(
        "app.api.v1.endpoints.ai_config.AIProviderService"
    ) as mock_service_class:
        mock_instance = MagicMock()

        async def mock_create_provider(provider_data):
            return AIProviderResponse(
                id=provider_id,
                name=provider_data.name,
                scope=provider_data.scope,
                provider_type=provider_data.provider_type,
                api_base=provider_data.api_base,
                api_key=provider_data.api_key
                if hasattr(provider_data, "api_key")
                else None,
                is_default=getattr(provider_data, "is_default", False),
                is_active=True,
                settings={},
                tenant_id=None,
                user_id=provider_data.user_id,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

        async def mock_get_providers(*args, **kwargs):
            return []

        mock_instance.create_provider = mock_create_provider
        mock_instance.get_providers = mock_get_providers
        mock_service_class.return_value = mock_instance

        response = await async_client.post(
            "/api/v1/ai-config/providers",
            json={
                "name": "Test Provider",
                "provider_type": "openai",
                "api_base": "https://api.test.com/v1",
                "is_default": True,
            },
        )

        assert response.status_code == 201
        assert response.json()["name"] == "Test Provider"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_providers(async_client):
    """Test listing providers"""
    app.dependency_overrides[get_current_user] = mock_user

    provider_id = uuid4()

    # Mock the service methods
    with patch(
        "app.api.v1.endpoints.ai_config.AIProviderService"
    ) as mock_service_class:
        mock_instance = MagicMock()

        async def mock_get_providers(*args, **kwargs):
            return [
                AIProviderResponse(
                    id=provider_id,
                    name="OpenAI",
                    scope=AIScope.SYSTEM,
                    provider_type="openai",
                    api_base="https://api.openai.com/v1",
                    api_key=None,
                    is_default=True,
                    is_active=True,
                    settings={},
                    tenant_id=None,
                    user_id=None,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
            ]

        mock_instance.get_providers = mock_get_providers
        mock_service_class.return_value = mock_instance

        response = await async_client.get("/api/v1/ai-config/providers")

        assert response.status_code == 200
        assert isinstance(response.json(), list)
        assert len(response.json()) == 1
        assert response.json()[0]["name"] == "OpenAI"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_model(async_client):
    """Test creating a model"""
    app.dependency_overrides[get_current_user] = mock_user

    provider_id = uuid4()
    model_id = uuid4()

    # Mock the service methods
    with patch(
        "app.api.v1.endpoints.ai_config.AIProviderService"
    ) as mock_service_class:
        mock_instance = MagicMock()

        async def mock_get_provider(provider_id):
            return AIProviderResponse(
                id=provider_id,
                name="Test Provider",
                scope=AIScope.SYSTEM,
                provider_type="openai",
                api_base="https://api.test.com/v1",
                api_key=None,
                is_default=False,
                is_active=True,
                settings={},
                tenant_id=None,
                user_id=None,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

        async def mock_create_model(model_data):
            return AIModelResponse(
                id=model_id,
                name=model_data.name,
                model_name=model_data.model_name,
                description=model_data.description
                if hasattr(model_data, "description")
                else None,
                is_default=False,
                is_active=True,
                max_tokens=model_data.max_tokens,
                temperature=model_data.temperature,
                settings={},
                provider_id=model_data.provider_id,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

        mock_instance.get_provider = mock_get_provider
        mock_instance.create_model = mock_create_model
        mock_service_class.return_value = mock_instance

        response = await async_client.post(
            f"/api/v1/ai-config/providers/{provider_id}/models",
            json={
                "name": "GPT-4",
                "model_name": "gpt-4",
                "max_tokens": 4096,
                "temperature": 0.7,
                "is_active": True,
                "provider_id": str(provider_id),
            },
        )

        assert response.status_code == 201

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_config_summary(async_client):
    """Test getting AI config summary"""
    app.dependency_overrides[get_current_user] = mock_user

    # Mock the service methods
    with patch(
        "app.api.v1.endpoints.ai_config.AIProviderService"
    ) as mock_service_class:
        mock_instance = MagicMock()

        async def mock_get_config_summary(*args, **kwargs):
            # Return all required fields for AIConfigSummary
            return {
                "providers": [],
                "models": [],
                "task_assignments": [],
                "default": None,
                "ocr": None,
                "nlp": None,
                "medication_interaction": None,
                "anomaly_detection": None,
                "fill_biomarker_form": None,
                "fill_medication_form": None,
                "magic_fill_examination": None,
                "define_biomarker": None,
                "define_medication": None,
                "suggest_category_icon": None,
                "generate_category_icon": None,
                "chat": None,
            }

        mock_instance.get_config_summary = mock_get_config_summary
        mock_service_class.return_value = mock_instance

        response = await async_client.get("/api/v1/ai-config/summary")

        assert response.status_code == 200
        assert "providers" in response.json()
        assert "ocr" in response.json()

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ai_config_routes_registered():
    """Test that AI config routes are registered in the app"""
    from app.api.v1.endpoints.ai_config import router

    # Check router has the expected routes
    routes = [r.path for r in router.routes]

    assert "/providers" in str(routes)
    assert "/models" in str(routes)
    assert "/task-assignments" in str(routes)
    assert "/summary" in str(routes)


def test_models_import():
    """Test that AI models can be imported"""
    from app.models.ai_provider_model import AIProviderModel, AIModel, AITaskAssignment

    assert AIProviderModel.__tablename__ == "ai_providers"
    assert AIModel.__tablename__ == "ai_models"
    assert AITaskAssignment.__tablename__ == "ai_task_assignments"


def test_schemas_import():
    """Test that AI schemas can be imported"""
    from app.ai.schemas.config import (
        AIProviderCreate,
        AIProviderResponse,
        AIModelCreate,
        AIModelResponse,
        AITaskAssignmentCreate,
        AIConfigSummary,
    )

    # Test schema creation
    provider = AIProviderCreate(
        name="Test", provider_type="openai", api_base="https://api.test.com/v1"
    )
    assert provider.name == "Test"
    assert provider.provider_type == "openai"


def test_processor_import():
    """Test that AI processor functions can be imported"""
    from app.ai.processors.ocr import get_ocr_processor, get_ocr_processor_from_db
    from app.ai.processors.nlp import get_nlp_extractor, get_nlp_extractor_from_db

    # Test basic OCR processor creation
    ocr = get_ocr_processor(
        provider="openai",
        api_key="test-key",
        api_base="https://api.openai.com/v1",
        model="gpt-4",
    )
    assert ocr is not None

    # Test basic NLP extractor creation
    nlp = get_nlp_extractor(
        provider="openai",
        api_key="test-key",
        api_base="https://api.openai.com/v1",
        model="gpt-4",
    )
    assert nlp is not None
