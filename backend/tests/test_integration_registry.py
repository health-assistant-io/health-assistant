import pytest
import os
from unittest.mock import patch, MagicMock
from app.core.integration_registry import IntegrationRegistry

@pytest.mark.asyncio
async def test_integration_registry_loads_manifests():
    registry = IntegrationRegistry()
    
    # Mock os.listdir to pretend 'dev_dummy' exists
    with patch("os.listdir") as mock_listdir, \
         patch("os.path.isdir") as mock_isdir, \
         patch("os.path.exists") as mock_exists, \
         patch("builtins.open", new_callable=MagicMock) as mock_open:
         
        mock_listdir.return_value = ["dev_dummy"]
        mock_isdir.return_value = True
        mock_exists.return_value = True
        
        # Setup mock open context manager
        file_mock = MagicMock()
        file_mock.read.return_value = '{"domain": "dev_dummy", "name": "Dummy"}'
        mock_open.return_value.__enter__.return_value = file_mock
        
        registry._load_manifests()
        
        manifests = registry.get_all_manifests()
        assert len(manifests) == 1
        assert manifests[0]["domain"] == "dev_dummy"
        
@pytest.mark.asyncio
async def test_integration_registry_initialize():
    registry = IntegrationRegistry()
    
    # Mock db
    mock_db = MagicMock()
    mock_result = MagicMock()
    
    # Create a mock SystemIntegration row
    mock_si = MagicMock()
    mock_si.domain = "dev_dummy"
    mock_si.is_enabled = True
    
    mock_result.scalars().all.return_value = [mock_si]
    
    async def mock_execute(*args, **kwargs):
        return mock_result
        
    mock_db.execute = mock_execute
    
    with patch.object(registry, "_load_manifests") as mock_load, \
         patch.object(registry, "_load_integration") as mock_load_int:
         
        registry._manifests = {"dev_dummy": {}}
        
        await registry.initialize(mock_db)
        
        # Ensure it tried to load the dev_dummy integration because it was enabled
        mock_load_int.assert_called_once_with("dev_dummy")
