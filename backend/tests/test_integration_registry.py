import pytest
import os
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from integrations.base import BaseHealthProvider, BaseConfigFlow
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
        
        # Ensure it tried to load the dev_dummy integration because it was enabled.
        # Item 5 of integrations-sdk-improvements: initialize now passes
        # the per-domain system_config (None for the mock → coerced to {}).
        mock_load_int.assert_called_once_with("dev_dummy", system_config={})


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


# ---------------------------------------------------------------------------
# Workstream F.1 (this stack): ``supports_catalog_proposals`` opt-in hook
# + ``CatalogProposal`` re-export. Source-level guards against a revert.
# ---------------------------------------------------------------------------


def test_sdk_base_declares_catalog_proposals_opt_in_hook_with_safe_defaults():
    """``BaseHealthProvider`` must declare ``supports_catalog_proposals`` and
    ``pull_catalog_proposals`` with safe defaults (False / []) so existing
    providers that don't opt in are unaffected. Mirrors the clinical-events
    + examinations contract tests."""
    import inspect

    from integrations.sdk.base import BaseHealthProvider as SDKBaseProvider

    assert hasattr(SDKBaseProvider, "supports_catalog_proposals")
    assert hasattr(SDKBaseProvider, "pull_catalog_proposals")

    supports_src = inspect.getsource(
        SDKBaseProvider.supports_catalog_proposals
    )
    assert "return False" in supports_src, (
        "supports_catalog_proposals must default to False — flipping it to "
        "True would opt every existing integration into the catalog-proposal "
        "pull path"
    )

    pull_src = inspect.getsource(SDKBaseProvider.pull_catalog_proposals)
    assert "return []" in pull_src, (
        "pull_catalog_proposals must default to [] — providers that haven't "
        "implemented it must return an empty list, not raise"
    )


def test_catalog_proposal_is_reexported_from_sdk():
    """``from integrations.sdk import CatalogProposal`` must work — the SDK
    re-exports the spec so providers can build proposals in
    ``pull_catalog_proposals`` without reaching into the SDK submodule
    directly."""
    from integrations.sdk import CatalogProposal
    from integrations.sdk.catalog import (
        CatalogProposal as CatalogProposalSource,
    )

    assert CatalogProposal is CatalogProposalSource, (
        "SDK re-export must alias the spec, not duplicate it"
    )


# ---------------------------------------------------------------------------
# Workstream G.3 (this stack): ``supports_hitl_proposals`` opt-in hook +
# ``IntegrationProposalSpec`` / ``ProposalOutcome`` re-exports.
# Source-level guards against a revert.
# ---------------------------------------------------------------------------


def test_sdk_base_declares_hitl_proposals_opt_in_hook_with_safe_defaults():
    """``BaseHealthProvider`` must declare ``supports_hitl_proposals``,
    ``pull_hitl_proposals``, and ``handle_proposal_resolution`` with safe
    defaults (False / [] / no-op) so existing providers that don't opt in
    are unaffected. Mirrors the clinical-events + examinations +
    catalog-proposals contract tests."""
    import inspect

    from integrations.sdk.base import BaseHealthProvider as SDKBaseProvider

    assert hasattr(SDKBaseProvider, "supports_hitl_proposals")
    assert hasattr(SDKBaseProvider, "pull_hitl_proposals")
    assert hasattr(SDKBaseProvider, "handle_proposal_resolution")

    supports_src = inspect.getsource(
        SDKBaseProvider.supports_hitl_proposals
    )
    assert "return False" in supports_src, (
        "supports_hitl_proposals must default to False — flipping it to "
        "True would opt every existing integration into the HITL pull "
        "path"
    )

    pull_src = inspect.getsource(SDKBaseProvider.pull_hitl_proposals)
    assert "return []" in pull_src, (
        "pull_hitl_proposals must default to [] — providers that haven't "
        "implemented it must return an empty list, not raise"
    )

    # ``handle_proposal_resolution`` must be awaitable + no-op by default.
    import asyncio
    import uuid

    class _P(SDKBaseProvider):
        domain = "test"

        async def pull_data(self, integration):
            return []

    provider = _P()
    coro = provider.handle_proposal_resolution(None, uuid.uuid4(), None)
    assert hasattr(coro, "__await__"), (
        "handle_proposal_resolution must be async so the resolver can "
        "``await`` it unconditionally"
    )
    assert asyncio.run(coro) is None, (
        "handle_proposal_resolution must default to a no-op (return None) "
        "so providers that haven't opted in don't need to override it"
    )

    # Source-level guard: the default body should explicitly return None
    # so a future revert that drops the ``return`` doesn't implicitly
    # return None (works but loses the documented contract).
    cb_src = inspect.getsource(
        SDKBaseProvider.handle_proposal_resolution
    )
    assert "return None" in cb_src, (
        "handle_proposal_resolution must explicitly ``return None`` — "
        "documents the no-op contract"
    )


def test_integration_proposal_spec_is_reexported_from_sdk():
    """``from integrations.sdk import IntegrationProposalSpec`` must work —
    the SDK re-exports the spec so providers can build HITL proposals in
    ``pull_hitl_proposals`` without reaching into the SDK submodule
    directly."""
    from integrations.sdk import IntegrationProposalSpec
    from integrations.sdk.proposals import (
        IntegrationProposalSpec as SpecSource,
    )

    assert IntegrationProposalSpec is SpecSource, (
        "SDK re-export must alias the spec, not duplicate it"
    )


def test_proposal_outcome_is_reexported_from_sdk():
    """``from integrations.sdk import ProposalOutcome`` must work — the
    resolver passes instances of this to ``handle_proposal_resolution``."""
    from integrations.sdk import ProposalOutcome
    from integrations.sdk.proposals import ProposalOutcome as OutcomeSource

    assert ProposalOutcome is OutcomeSource, (
        "SDK re-export must alias the spec, not duplicate it"
    )


# ---------------------------------------------------------------------------
# Item 5 of integrations-sdk-improvements: setup() gets real config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_integration_passes_system_config_to_setup():
    """``_load_integration`` must forward the system-level config dict
    to ``provider.setup(config)`` instead of the legacy empty dict."""
    registry = IntegrationRegistry()

    # Build a fake provider module + class so we can capture the setup
    # call without importing a real integration. The registry's
    # ``_find_class_by_base`` checks ``item.__module__ == module.__name__``
    # so we have to set both consistently.
    captured: dict = {}

    class FakeProvider(BaseHealthProvider):
        domain = "fake_setup_test"

        async def setup(self, config: dict | None = None) -> None:
            captured["config"] = config or {}

        async def pull_data(self, integration):
            return []

    class FakeConfigFlow(BaseConfigFlow):
        domain = "fake_setup_test"

        async def get_schema(self) -> dict:
            return {}

        async def validate_input(self, user_input: dict) -> dict:
            return user_input

    FakeProvider.__module__ = "fake_provider"
    FakeConfigFlow.__module__ = "fake_config_flow"

    fake_module = SimpleNamespace(FakeProvider=FakeProvider, __name__="fake_provider")
    fake_flow_module = SimpleNamespace(
        FakeConfigFlow=FakeConfigFlow, __name__="fake_config_flow"
    )

    with patch("importlib.import_module") as mock_import:
        mock_import.side_effect = [fake_module, fake_flow_module]
        await registry._load_integration(
            "fake_setup_test",
            system_config={"entitlement": "pro", "region": "eu"},
        )

    assert captured.get("config") == {
        "entitlement": "pro",
        "region": "eu",
    }, "registry must forward the SystemIntegration.global_config to setup()"


@pytest.mark.asyncio
async def test_initialize_passes_per_domain_system_config(monkeypatch):
    """``initialize`` must build the per-domain config map from the
    SystemIntegration rows and pass each to ``_load_integration``."""
    registry = IntegrationRegistry()
    registry._manifests = {
        "alpha": {"domain": "alpha"},
        "beta": {"domain": "beta"},
    }

    # Mock DB query.
    alpha_si = MagicMock(domain="alpha", is_enabled=True, global_config={"k": "alpha-v"})
    beta_si = MagicMock(domain="beta", is_enabled=True, global_config=None)
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [alpha_si, beta_si]

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db = MagicMock()
    mock_db.execute = mock_execute

    # Capture _load_integration calls.
    calls: list[tuple[str, dict]] = []

    async def _capture(domain, *, system_config=None):
        calls.append((domain, system_config or {}))

    monkeypatch.setattr(registry, "_load_integration", _capture)
    monkeypatch.setattr(registry, "_load_manifests", lambda: None)

    await registry.initialize(mock_db)

    assert ("alpha", {"k": "alpha-v"}) in calls
    assert ("beta", {}) in calls, "None global_config must coerce to empty dict"


def test_dev_dummy_provider_overrides_setup():
    """The reference provider overrides setup() so authors have a
    copy-paste example of the lifecycle hook."""
    from integrations.dev_dummy.provider import DevDummyProvider

    # The class must override setup (not just inherit the no-op default).
    assert DevDummyProvider.setup.__qualname__.startswith("DevDummyProvider"), (
        "DevDummyProvider must override setup() to demonstrate the lifecycle hook"
    )


@pytest.mark.asyncio
async def test_dev_dummy_setup_does_not_raise():
    from integrations.dev_dummy.provider import DevDummyProvider

    provider = DevDummyProvider()
    # Must accept both shapes: dict / None.
    await provider.setup({"k": "v"})
    await provider.setup(None)
