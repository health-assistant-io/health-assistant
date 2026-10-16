"""Tests for the ``push`` capability gate and the engine's push leg.

The charter vocabulary replaces the old hook-probing contract (D18): an
extension pushes exactly when its manifest declares the ``push``
capability — declared data, gated at registration, never a method-shape
probe. Post-fix contract pinned here:

1. ``pull_data`` remains concrete on the SDK base (no abstract-method tax
   for push-only extensions).
2. The engine's push leg runs only for ``push``-declaring extensions
   (``dev_dummy`` and ``fhir_server`` declare it; ``webhook``, the bridge
   and ``mcp_client`` do not).
3. ``run_sync`` calls ``push_data`` only when the capability is declared.
"""

from unittest.mock import MagicMock

from app.extensions.registry import IntegrationRegistry, domain_of
from app.extensions.sdk.base import BaseHealthProvider


def _loaded_registry() -> IntegrationRegistry:
    """A registry with the bundled manifests loaded (the module singleton
    only populates on app startup)."""
    reg = IntegrationRegistry()
    reg._load_manifests()
    return reg


# ---------------------------------------------------------------------------
# pull_data is not abstract
# ---------------------------------------------------------------------------


def test_pull_data_is_not_abstract():
    """``pull_data`` must not carry ``__isabstractmethod__`` — push-only
    extensions instantiate without overriding it."""
    assert getattr(BaseHealthProvider.pull_data, "__isabstractmethod__", False) is False


def test_pull_data_default_returns_empty_list():
    """The concrete default returns ``[]`` (a cheap no-op for push-only
    extensions)."""

    class _Minimal(BaseHealthProvider):
        domain = "minimal_test"

    import asyncio

    provider = _Minimal()
    result = asyncio.get_event_loop().run_until_complete(provider.pull_data(MagicMock()))
    assert result == []


def test_minimal_subclass_instantiates_without_pull_data_override():
    """A provider that overrides neither ``pull_data`` nor ``push_data``
    instantiates cleanly (the historical ABC would have raised TypeError)."""

    class _ToolOnly(BaseHealthProvider):
        domain = "tool_only_test"

    provider = _ToolOnly()
    assert isinstance(provider, BaseHealthProvider)


# ---------------------------------------------------------------------------
# the push capability is manifest-declared (D18 — no supports_push hook)
# ---------------------------------------------------------------------------


def test_supports_push_hook_is_gone():
    """The ``supports_push`` probing method must not exist on the SDK base
    — the capability gate reads the manifest."""
    assert not hasattr(BaseHealthProvider, "supports_push")


def test_dev_dummy_and_fhir_server_declare_push():
    """The two pushers declare the ``push`` capability in their manifests."""
    reg = _loaded_registry()
    for domain in ("dev_dummy", "fhir_server"):
        manifest = reg.get_manifest(domain)
        assert manifest is not None, domain
        assert "push" in manifest["capabilities"], domain


def test_webhook_bridge_mcp_are_not_pushers():
    """Webhook, bridge and mcp_client declare no ``push`` capability —
    their manifests must say so honestly."""
    reg = _loaded_registry()
    for domain in ("webhook", "health_assistant_bridge", "mcp_client"):
        manifest = reg.get_manifest(domain)
        assert manifest is not None, domain
        assert "push" not in manifest["capabilities"], domain


def test_run_sync_gates_push_on_the_declared_capability():
    """Source-level guard: the engine's push block gates on the declared
    ``push`` capability via ``_declared`` — never an unconditional call,
    never a method probe."""
    import inspect

    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc)
    assert '_declared(provider, "supports_push")' in src, (
        "run_sync must gate push_data behind the declared push capability"
    )
    assert "supports_push()" not in src, "the supports_push hook probe is gone"


def test_domain_alias_mapping():
    """The envelope id ↔ product domain alias is mechanical (ruling 4)."""
    assert domain_of("dev-dummy") == "dev_dummy"
    assert domain_of("health-assistant-bridge") == "health_assistant_bridge"
