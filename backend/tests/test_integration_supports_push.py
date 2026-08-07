"""Tests for the ``supports_push`` capability hook and engine push gating.

Phase 2.1 of the integrations remediation: previously ``push_data`` was
called unconditionally on every provider (the only capability without an
explicit opt-in), while ``pull_data`` was ``@abstractmethod`` — forcing
push-only providers (``webhook``, ``bridge``, ``mcp_client``) to stub a
no-op ``pull_data``. This redresses the asymmetry:

* ``pull_data`` is now concrete (default ``[]``) — no more abstract-method tax.
* ``supports_push()`` gates the engine's outbound push call, mirroring the
  eight ``supports_*`` / ``pull_*`` opt-in families.

Post-fix contract pinned here:

1. ``BaseHealthProvider`` instantiates without overriding ``pull_data``
   (no longer abstract).
2. ``supports_push`` detects a real ``push_data`` override automatically.
3. ``run_sync`` calls ``push_data`` only when ``supports_push`` is True.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from integrations.sdk.base import BaseHealthProvider
from integrations.sdk import BaseHealthProvider as ExportedBase  # export parity


# ---------------------------------------------------------------------------
# pull_data is no longer abstract
# ---------------------------------------------------------------------------


def test_pull_data_is_not_abstract():
    """``pull_data`` must not carry ``__isabstractmethod__`` — push-only
    providers instantiate without overriding it."""
    assert getattr(BaseHealthProvider.pull_data, "__isabstractmethod__", False) is False


def test_pull_data_default_returns_empty_list():
    """The concrete default returns ``[]`` (a cheap no-op for push-only
    providers)."""
    # Use a minimal concrete subclass that overrides nothing.
    class _Minimal(BaseHealthProvider):
        domain = "minimal_test"

    import asyncio
    provider = _Minimal()
    result = asyncio.get_event_loop().run_until_complete(
        provider.pull_data(MagicMock())
    )
    assert result == []


def test_minimal_subclass_instantiates_without_pull_data_override():
    """A provider that overrides neither ``pull_data`` nor ``push_data``
    instantiates cleanly (the historical ABC would have raised TypeError)."""
    class _ToolOnly(BaseHealthProvider):
        domain = "tool_only_test"

    provider = _ToolOnly()
    assert isinstance(provider, BaseHealthProvider)


# ---------------------------------------------------------------------------
# supports_push override detection
# ---------------------------------------------------------------------------


def test_supports_push_false_when_push_data_not_overridden():
    """A provider that inherits the base ``push_data`` no-op does not push."""
    class _PullOnly(BaseHealthProvider):
        domain = "pull_only_test"

    assert _PullOnly().supports_push() is False


def test_supports_push_true_when_push_data_overridden():
    """A provider that overrides ``push_data`` is automatically opted in."""
    class _Pusher(BaseHealthProvider):
        domain = "push_test"

        async def push_data(self, integration, data):
            return {"pushed": True}

    assert _Pusher().supports_push() is True


def test_supports_push_can_be_force_disabled():
    """A provider may override ``supports_push`` to return False even when
    ``push_data`` is implemented (escape hatch)."""
    class _Disabled(BaseHealthProvider):
        domain = "disabled_push_test"

        async def push_data(self, integration, data):
            return {"pushed": True}

        def supports_push(self) -> bool:
            return False

    assert _Disabled().supports_push() is False


def test_dev_dummy_and_fhir_server_are_detected_as_pushers():
    """The two real providers that override ``push_data`` are picked up
    automatically (regression: they must not silently stop pushing)."""
    from integrations.dev_dummy.provider import DevDummyProvider
    from integrations.fhir_server.provider import FhirServerProvider

    assert DevDummyProvider().supports_push() is True
    assert FhirServerProvider().supports_push() is True


def test_webhook_bridge_mcp_are_not_pushers():
    """The push-only / tool-only providers do not push outward."""
    from integrations.webhook.provider import WebhookProvider
    from integrations.health_assistant_bridge.provider import HealthAssistantBridgeProvider

    assert WebhookProvider().supports_push() is False
    assert HealthAssistantBridgeProvider().supports_push() is False


# ---------------------------------------------------------------------------
# run_sync gates push_data behind supports_push
# ---------------------------------------------------------------------------


def test_run_sync_calls_push_data_only_when_supports_push_true():
    """Source-level guard: the engine's push block probes ``supports_push``
    via ``_opt_in`` before calling ``provider.push_data``."""
    from app.services import integration_sync_service as svc

    src = inspect.getsource(svc)
    # The push block must gate on supports_push (not call unconditionally).
    assert '_opt_in(provider, "supports_push")' in src, (
        "run_sync must gate push_data behind _opt_in(provider, 'supports_push')"
    )
