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


# ---------------------------------------------------------------------------
# Legacy OAuth stubs removed from the core base
# ---------------------------------------------------------------------------


def test_core_base_no_longer_declares_legacy_oauth_stubs():
    """The pre-SmartOAuth trio (get_auth_url / exchange_token /
    refresh_access_token) has been removed from the core base.

    These methods predated the SDK's ``begin_oauth`` / ``complete_oauth``
    pair + ``SmartOAuth`` helper and were never called by any engine path;
    they silently returned ``""`` / ``{}`` / ``{}`` and invited new
    integrations to override the wrong shape. Greenfield codebase — no
    backwards-compat shim is warranted.

    Source-level guard against a revert.
    """
    from integrations import base as core_base

    for legacy_name in ("get_auth_url", "exchange_token", "refresh_access_token"):
        assert not hasattr(core_base.BaseHealthProvider, legacy_name), (
            f"BaseHealthProvider.{legacy_name} should be removed — the SDK "
            f"base (integrations/sdk/base.py) declares the canonical "
            f"begin_oauth / complete_oauth pair, and SmartOAuth "
            f"(integrations/sdk/auth.py) handles token refresh. Re-adding "
            f"the legacy stub would re-introduce the silent no-op footgun."
        )


def test_core_base_does_not_import_warnings_module():
    """The deprecation-warning shim (a prior, more conservative phase of this
    cleanup) imported ``warnings``. With the methods gone, the import should
    be gone too — guards against an partial revert that leaves a dangling
    import."""
    import importlib
    import inspect

    from integrations import base as core_base

    src = inspect.getsource(core_base)
    assert "import warnings" not in src, (
        "integrations/base.py should not import warnings — the legacy "
        "OAuth stubs that used it have been removed"
    )
    # Sanity: the module is still importable
    assert importlib.reload(core_base) is core_base
