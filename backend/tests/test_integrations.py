import pytest
from httpx import AsyncClient
from unittest.mock import patch, MagicMock, AsyncMock
import uuid

class MockUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.user_id = self.id
        self.role = "user"
        self.tenant_id = uuid.uuid4()

def override_get_current_user():
    return MockUser()

@pytest.mark.asyncio
async def test_list_available_integrations(async_client: AsyncClient):
    # Mock the registry directly
    with patch("app.api.v1.endpoints.integrations.integration_registry") as mock_registry:
        mock_registry.get_all_manifests.return_value = [
            {"domain": "dev_dummy", "name": "Dev Dummy", "version": "1.0.0"}
        ]
        
        from app.core.security import get_current_user
        from app.main import app
        from app.core.database import get_db
        
        # We need to mock the DB result for SystemIntegration check
        mock_result = MagicMock()
        mock_system_integration = MagicMock()
        mock_system_integration.domain = "dev_dummy"
        mock_result.scalars.return_value.all.return_value = [mock_system_integration]

        async def override_get_db():
            class MockDB:
                async def execute(self, *args, **kwargs):
                    return mock_result
            yield MockDB()

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db] = override_get_db
        
        response = await async_client.get("/api/v1/integrations/available")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["domain"] == "dev_dummy"
        
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_get_config_flow_not_enabled(async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app
    app.dependency_overrides[get_current_user] = override_get_current_user
    
    # Attempting to get a config flow that hasn't been enabled by system should 400
    response = await async_client.get("/api/v1/integrations/some_random_domain/config-flow")
    assert response.status_code == 400
    assert "not enabled" in response.json()["detail"]
    
    app.dependency_overrides.clear()
    
@pytest.mark.asyncio
async def test_get_config_flow_success(async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app
    
    # Need to mock the database query for SystemIntegration to return true
    with patch("app.api.v1.endpoints.integrations.AsyncSession") as mock_session:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = True # Simulate enabled integration
        
        async def mock_execute(*args, **kwargs):
            return mock_result
            
        with patch("app.api.v1.endpoints.integrations.integration_registry") as mock_registry:
            mock_flow = MagicMock()
            
            async def get_schema():
                return {"step_id": "test"}
                
            mock_flow.get_schema = get_schema
            mock_registry.get_config_flow.return_value = mock_flow
            
            app.dependency_overrides[get_current_user] = override_get_current_user
            
            # Since Depends(get_db) returns an actual DB session in the test app by default,
            # We mock the session.execute via dependency override
            async def override_get_db():
                class MockDB:
                    async def execute(self, *args, **kwargs):
                        return mock_result
                yield MockDB()
                
            from app.core.database import get_db
            app.dependency_overrides[get_db] = override_get_db
            
            response = await async_client.get("/api/v1/integrations/dev_dummy/config-flow")
            assert response.status_code == 200
            assert response.json()["step_id"] == "test"
            
            app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_get_integration_details(async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app
    from app.core.database import get_db

    patient_uuid = uuid.uuid4()
    integration_id = uuid.uuid4()
    
    mock_integration = MagicMock()
    mock_integration.id = integration_id
    mock_integration.provider = "dev_dummy"
    mock_integration.instance_name = "Test Instance"
    mock_integration.status.value = "active"
    mock_integration.user_config = {}
    mock_integration.is_debug_enabled = False
    mock_integration.last_synced_at = None
    
    # Mock multiple query returns (1. Integration, 2. Logs, 3. Exposed, 4. Recent, 5. Synced Exams)
    mock_db = AsyncMock()
    mock_res_integration = MagicMock()
    mock_res_integration.scalar_one_or_none.return_value = mock_integration
    
    mock_res_logs = MagicMock()
    mock_res_logs.scalars().all.return_value = []
    
    mock_res_exposed = MagicMock()
    mock_res_exposed.all.return_value = []
    
    mock_res_recent = MagicMock()
    mock_res_recent.scalars().all.return_value = []
    
    mock_res_exams = MagicMock()
    mock_res_exams.scalars().all.return_value = []
    
    mock_db.execute.side_effect = [
        mock_res_integration, 
        mock_res_logs, 
        mock_res_exposed, 
        mock_res_recent, 
        mock_res_exams
    ]

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    with patch("app.api.v1.endpoints.integrations.integration_registry") as mock_registry:
        response = await async_client.get(f"/api/v1/integrations/instance/{integration_id}/details?patient_id={patient_uuid}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(integration_id)
        assert data["instance_name"] == "Test Instance"
        assert "synced_examinations" in data
        assert "recent_data" in data
        assert "exposed_items" in data

    app.dependency_overrides = {}

@pytest.mark.asyncio
async def test_execute_custom_action_success(async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app
    from app.core.database import get_db

    patient_uuid = uuid.uuid4()
    
    integration_id = uuid.uuid4()
    
    # Mock DB
    mock_result = MagicMock()
    mock_integration = MagicMock()
    mock_integration.provider = "dev_dummy"
    mock_result.scalar_one_or_none.return_value = mock_integration

    class MockDB:
        async def execute(self, *args, **kwargs):
            return mock_result
        async def commit(self):
            pass

    async def override_get_db():
        yield MockDB()

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    with patch("app.api.v1.endpoints.integrations.integration_registry") as mock_registry:
        mock_provider = MagicMock()
        
        async def execute_custom_action(*args, **kwargs):
            return {"message": "Action executed!"}
            
        mock_provider.execute_custom_action = execute_custom_action
        mock_registry.get_provider.return_value = mock_provider
        
        response = await async_client.post(f"/api/v1/integrations/instance/{integration_id}/action/my_action?patient_id={patient_uuid}")
        
        assert response.status_code == 200
        assert response.json()["message"] == "Action executed!"

    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_execute_custom_action_not_supported(async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app
    from app.core.database import get_db

    patient_uuid = uuid.uuid4()
    
    integration_id = uuid.uuid4()
    
    mock_result = MagicMock()
    mock_integration = MagicMock()
    mock_integration.provider = "test_provider"
    mock_result.scalar_one_or_none.return_value = mock_integration

    class MockDB:
        async def execute(self, *args, **kwargs):
            return mock_result

    async def override_get_db():
        yield MockDB()

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db

    with patch("app.api.v1.endpoints.integrations.integration_registry") as mock_registry:
        # Mock provider without custom actions capability
        class SimpleProvider:
            pass
            
        mock_registry.get_provider.return_value = SimpleProvider()
        
        response = await async_client.post(f"/api/v1/integrations/instance/{integration_id}/action/some_action?patient_id={patient_uuid}")
        
        assert response.status_code == 400
        assert "not support custom actions" in response.json()["detail"]

    app.dependency_overrides.clear()
