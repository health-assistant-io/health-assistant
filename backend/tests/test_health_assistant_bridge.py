import pytest
import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider
from app.models.user_integration import UserIntegration
from app.ai.schemas.nlp import MapResponsePayload, MappedMetric

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
    from app.ai.schemas.nlp import MetricMappingRequest
    
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
        with patch("app.ai.providers.service.AIProviderService") as mock_ai_service:
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
            with patch.dict('sys.modules', {'app.ai.providers.service': MagicMock(AIProviderService=mock_ai_service)}):
                result = await provider._handle_map_request(integration_mock, map_request)
            
            assert mock_ai_service_instance.get_nlp_extractor.called
            assert mock_nlp_extractor.map_external_metrics.called
            assert result["mappings"][0]["new_biomarker_name"] == "Test Metric"


# ---------------------------------------------------------------------------
# Workstream E.2 (this stack): bridge routes examinations through the
# canonical service instead of inlining dedup + direct ORM construction.
# Source-level guards against a revert that re-introduces the stale
# ``category_id`` field (the live model has ``category_concept_id``).
# ---------------------------------------------------------------------------


def test_bridge_routes_examinations_through_canonical_service():
    """The bridge's ``_process_and_save_sync_data`` must delegate exam
    creation to ``examination_service.create_examination`` instead of
    constructing ``ExaminationModel`` rows directly.

    Before E.2 the bridge inlined ~80 LOC of dedup + ORM construction
    that had already gone stale: it set ``category_id=`` (a column that
    no longer exists on the live model — categories moved into the
    unified taxonomy as ``category_concept_id``), referenced the
    deleted ``ExaminationCategory`` model, and pre-generated UUIDs
    instead of using the ``gen_random_uuid()`` server default. Routing
    through the service fixes all three and gets dedup + audit
    provenance for free.
    """
    import re
    import inspect
    from integrations.health_assistant_bridge import provider as bridge_mod

    src = inspect.getsource(bridge_mod.HealthAssistantBridgeProvider._process_and_save_sync_data)

    # Positive: must delegate to the service.
    assert "create_examination" in src, (
        "bridge must call examination_service.create_examination for exam "
        "writes (workstream E.2 migration)"
    )
    assert "resolve_integration_actor" in src, (
        "bridge must resolve a service-context actor via workstream D "
        "before calling create_examination"
    )

    # Negative: must not construct ExaminationModel directly, must not
    # reference the deleted ExaminationCategory model, must not set the
    # stale category_id field.
    assert "ExaminationModel(" not in src, (
        "bridge must not construct ExaminationModel directly — that's the "
        "service's job after E.2"
    )
    assert "ExaminationCategory" not in src, (
        "bridge must not import ExaminationCategory — the model was deleted "
        "when categories moved into the unified taxonomy"
    )
    # Use a negative lookbehind so ``category_concept_id=`` (the live
    # field name) doesn't trip the check for the stale ``category_id=``.
    stale_category_id = re.search(r"(?<!concept_)category_id=", src)
    assert stale_category_id is None, (
        "bridge must not set category_id — the live column is "
        "category_concept_id; the old bridge silently dropped the category "
        f"on every exam it created (found at offset {stale_category_id.start()})"
    )
