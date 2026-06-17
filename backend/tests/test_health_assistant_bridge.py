import pytest
import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from app.models.user_integration import UserIntegration
from app.schemas.ai_nlp import MapResponsePayload, MappedMetric

@pytest.fixture
def integration_mock():
    integration = UserIntegration()
    integration.id = uuid4()
    integration.tenant_id = uuid4()
    integration.user_id = uuid4()
    integration.patient_id = uuid4()
    integration.provider = "health_assistant_bridge"
    integration.instance_name = "Test Bridge"
    integration.user_config = {"_sync_state": {"last_timestamp": "2024-06-15T12:00:00Z"}}
    integration.is_debug_enabled = False
    integration.last_synced_at = datetime.datetime.now(datetime.timezone.utc)
    return integration

@pytest.fixture
def provider():
    return HealthAssistantBridgeProvider()

@pytest.mark.asyncio
async def test_handle_api_request_status(provider, integration_mock):
    # Mock the request
    request_mock = MagicMock()

    result = await provider.handle_api_request(
        integration=integration_mock,
        path="status",
        method="GET",
        request=request_mock
    )
    
    assert result["status"] == "active"
    assert result["integration_id"] == str(integration_mock.id)
    assert result["cursor"] == "2024-06-15T12:00:00Z"
    assert "last_synced_at" in result

@pytest.mark.asyncio
@patch("integrations.health_assistant_bridge.provider.HealthAssistantBridgeProvider._handle_map_request")
async def test_handle_api_request_map(mock_handle_map, provider, integration_mock):
    request_mock = AsyncMock()
    request_mock.json.return_value = {
        "unmapped_metrics": [
            {"name": "Test Metric"}
        ]
    }
    
    mock_handle_map.return_value = {"mappings": []}
    
    result = await provider.handle_api_request(
        integration=integration_mock,
        path="map",
        method="POST",
        request=request_mock
    )
    
    assert mock_handle_map.called
    assert result == {"mappings": []}

@pytest.mark.asyncio
@patch("integrations.health_assistant_bridge.provider.HealthAssistantBridgeProvider._process_and_save_sync_data")
async def test_handle_api_request_sync(mock_save, provider, integration_mock):
    request_mock = AsyncMock()
    request_mock.json.return_value = {
        "client_version": "1.0",
        "source_system": "test",
        "cursor": "2024-06-16T12:00:00Z",
        "records": [
            {
                "type": "quantitative",
                "name": "Test Metric",
                "value": 100.0,
                "unit": "mg/dL"
            }
        ]
    }
    
    mock_save.return_value = 1
    
    result = await provider.handle_api_request(
        integration=integration_mock,
        path="sync",
        method="POST",
        request=request_mock
    )
    
    assert result["success"] is True
    assert result["metrics_synced"] == 1
    assert integration_mock.user_config["_sync_state"]["last_timestamp"] == "2024-06-16T12:00:00Z"
    assert mock_save.called

@pytest.mark.asyncio
@patch("integrations.health_assistant_bridge.provider.HealthAssistantBridgeProvider._process_and_save_sync_data")
async def test_handle_api_request_sync_examinations(mock_save, provider, integration_mock):
    request_mock = AsyncMock()
    request_mock.json.return_value = {
        "client_version": "1.2.0",
        "source_system": "test",
        "cursor": "2024-06-16T12:00:00Z",
        "examinations": [
            {
                "id": "ext-exam-123",
                "date": "2024-06-16T10:00:00Z",
                "lab_name": "Test Lab",
                "category": "Blood Test",
                "records": [
                    {
                        "type": "quantitative",
                        "name": "Test Metric 2",
                        "value": 50.0,
                        "unit": "mg/dL"
                    }
                ]
            }
        ]
    }
    
    mock_save.return_value = 1
    
    result = await provider.handle_api_request(
        integration=integration_mock,
        path="sync",
        method="POST",
        request=request_mock
    )
    
    assert result["success"] is True
    assert result["metrics_synced"] == 1
    assert integration_mock.user_config["_sync_state"]["last_timestamp"] == "2024-06-16T12:00:00Z"
    assert mock_save.called

@pytest.mark.asyncio
async def test_parse_records(provider, integration_mock):
    from integrations.health_assistant_bridge.provider import ClientRecord
    
    builder = provider.create_observation_builder(integration_mock)
    
    records = [
        ClientRecord(
            type="quantitative",
            name="Sodium",
            biomarker_id="123e4567-e89b-12d3-a456-426614174000",
            code="2951-2",
            coding_system="custom",
            value=140.0,
            unit="mmol/L",
            performer="Test Lab"
        )
    ]
    
    observations = provider._parse_records(records, builder, str(integration_mock.id), integration_mock.instance_name)
    assert len(observations) == 1
    
    obs = observations[0]
    assert obs.raw_value == 140.0
    assert obs.value_quantity["unit"] == "mmol/L"
    assert obs.code["text"] == "Sodium"
    assert str(obs.biomarker_id) == "123e4567-e89b-12d3-a456-426614174000"
    assert obs.performer[0]["display"] == "Test Lab"
    assert obs.performer[0]["reference"] == f"Integration/{integration_mock.id}"

@pytest.mark.asyncio
@patch("integrations.health_assistant_bridge.provider.HealthAssistantBridgeProvider")
async def test_handle_map_request_internal(mock_provider, provider, integration_mock):
    from integrations.health_assistant_bridge.provider import MapRequestPayload
    from app.schemas.ai_nlp import MetricMappingRequest
    
    # We will mock the DB call internally
    with patch("app.core.database.AsyncSessionLocal") as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        mock_db_execute = AsyncMock()
        mock_session.execute = mock_db_execute
        
        # Mock existing catalog
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_db_execute.return_value.scalars = MagicMock(return_value=mock_scalars)
        
        # Mock AI Service and NLP Extractor
        with patch("app.services.ai_provider_service.AIProviderService") as mock_ai_service:
            mock_ai_service_instance = MagicMock()
            mock_ai_service.return_value = mock_ai_service_instance
            mock_nlp_extractor = AsyncMock()
            mock_ai_service_instance.get_nlp_extractor = AsyncMock(return_value=mock_nlp_extractor)
            
            # Mock response from NLP extractor
            mock_map_response = MapResponsePayload(
                mappings=[MappedMetric(original_name="Test", action="create_new", new_biomarker_name="Test Metric")]
            )
            mock_nlp_extractor.map_external_metrics.return_value = mock_map_response
            
            # Run Method
            map_request = MapRequestPayload(
                unmapped_metrics=[MetricMappingRequest(name="Test")]
            )
            
            # The provider imports AIProviderService locally inside the method, 
            # so we mock the global module attribute instead of the provider attribute.
            with patch.dict('sys.modules', {'app.services.ai_provider_service': MagicMock(AIProviderService=mock_ai_service)}):
                result = await provider._handle_map_request(integration_mock, map_request)
            
            assert mock_ai_service_instance.get_nlp_extractor.called
            assert mock_nlp_extractor.map_external_metrics.called
            assert result["mappings"][0]["new_biomarker_name"] == "Test Metric"
