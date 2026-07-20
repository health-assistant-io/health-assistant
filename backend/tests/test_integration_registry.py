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


# ---------------------------------------------------------------------------
# SDK base: vestigial fetch_json removed (workstream A.3)
# ---------------------------------------------------------------------------


def test_sdk_base_no_longer_declares_fetch_json():
    """``BaseHealthProvider.fetch_json`` was a pre-``http_request`` GET helper
    that inlined its own retry loop. It had zero provider overrides or call
    sites at removal time, and every retry/backoff/jitter concern now lives
    in ``integrations.sdk.http._retry_request``. Providers that need a
    robust GET call ``await http_request(self._http_client, "GET", url,
    ...)`` instead. Source-level guard against a revert.
    """
    from integrations.sdk.base import BaseHealthProvider as SDKBaseProvider

    assert not hasattr(SDKBaseProvider, "fetch_json"), (
        "SDK BaseHealthProvider.fetch_json should be removed — the shared "
        "_retry_request helper (integrations.sdk.http) replaced it. "
        "Re-adding it would re-introduce a divergent retry implementation."
    )


def test_sdk_base_no_longer_imports_asyncio_or_legacy_exceptions():
    """After ``fetch_json`` removal, ``asyncio`` and the auth/rate-limit
    exception imports it used should be gone from ``sdk/base.py`` — guards
    against a partial revert that leaves dangling imports (which ruff would
    also catch, but this test makes the intent explicit)."""
    import inspect

    from integrations.sdk import base as sdk_base

    src = inspect.getsource(sdk_base)
    assert "import asyncio" not in src, (
        "sdk/base.py should not import asyncio — fetch_json (the only user) "
        "has been removed"
    )
    assert "IntegrationAuthError" not in src, (
        "sdk/base.py should not reference IntegrationAuthError — fetch_json "
        "(the only user) has been removed; the shared _retry_request helper "
        "raises it on 401/403"
    )
    assert "IntegrationRateLimitError" not in src, (
        "sdk/base.py should not reference IntegrationRateLimitError — "
        "fetch_json (the only user) has been removed; the shared "
        "_retry_request helper raises it on 429-after-retries"
    )


# ---------------------------------------------------------------------------
# SDK base: clinical-events opt-in hook (workstream B.2)
# ---------------------------------------------------------------------------


def test_sdk_base_declares_clinical_events_opt_in_hook_with_safe_defaults():
    """``BaseHealthProvider`` must declare ``supports_clinical_events`` and
    ``pull_clinical_events`` with safe defaults so existing providers that
    don't opt in are unaffected. Source-level guard for the contract."""
    import inspect
    from integrations.sdk.base import BaseHealthProvider as SDKBaseProvider

    # Defaults — False / [] so the engine's ``_opt_in`` probe skips
    # providers that haven't opted in.
    assert hasattr(SDKBaseProvider, "supports_clinical_events")
    assert hasattr(SDKBaseProvider, "pull_clinical_events")

    # Pull the default method off the class (unbound) and confirm the body
    # shape guards against a future revert that flips the default.
    supports_src = inspect.getsource(SDKBaseProvider.supports_clinical_events)
    assert "return False" in supports_src, (
        "supports_clinical_events must default to False — flipping it to "
        "True would opt every existing integration into the clinical-events "
        "pull path"
    )

    pull_src = inspect.getsource(SDKBaseProvider.pull_clinical_events)
    assert "return []" in pull_src, (
        "pull_clinical_events must default to [] — providers that haven't "
        "implemented it must return an empty list, not raise"
    )


def test_clinical_event_create_is_reexported_from_sdk():
    """``from integrations.sdk import ClinicalEventCreate`` must work — the
    SDK re-exports the schema so providers can build event payloads in
    ``pull_clinical_events`` without reaching into ``app.schemas``."""
    from integrations.sdk import ClinicalEventCreate
    from app.schemas.clinical_event import ClinicalEventCreate as SchemaSource

    assert ClinicalEventCreate is SchemaSource, (
        "SDK re-export must alias the schema, not duplicate it"
    )


def test_sdk_base_declares_examinations_opt_in_hook_with_safe_defaults():
    """``BaseHealthProvider`` must declare ``supports_examinations`` and
    ``pull_examinations`` with safe defaults (False / []) so existing
    providers that don't opt in are unaffected. Mirrors the clinical-events
    contract test above."""
    import inspect
    from integrations.sdk.base import BaseHealthProvider as SDKBaseProvider

    assert hasattr(SDKBaseProvider, "supports_examinations")
    assert hasattr(SDKBaseProvider, "pull_examinations")

    supports_src = inspect.getsource(SDKBaseProvider.supports_examinations)
    assert "return False" in supports_src, (
        "supports_examinations must default to False — flipping it to True "
        "would opt every existing integration into the examinations pull "
        "path"
    )

    pull_src = inspect.getsource(SDKBaseProvider.pull_examinations)
    assert "return []" in pull_src, (
        "pull_examinations must default to [] — providers that haven't "
        "implemented it must return an empty list, not raise"
    )


def test_examination_create_is_reexported_from_sdk():
    """``from integrations.sdk import ExaminationCreate`` must work — the
    SDK re-exports the schema so providers can build exam payloads in
    ``pull_examinations`` without reaching into ``app.schemas``."""
    from integrations.sdk import ExaminationCreate
    from app.schemas.examination import ExaminationCreate as SchemaSource

    assert ExaminationCreate is SchemaSource, (
        "SDK re-export must alias the schema, not duplicate it"
    )
