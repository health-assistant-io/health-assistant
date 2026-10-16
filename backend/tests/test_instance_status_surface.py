"""Tests for the family instance-status surface (ADR-0040 D7/D21).

The vocabulary is family law — ``ok | degraded | failing | stale`` with
``last_success_at`` + ``consecutive_failures`` and the ``needs-config``
condition — while the persistence underneath stays health-owned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.services.instance_status import instance_status_surface


def _integration(provider: str = "webhook", config: dict | None = None):
    return SimpleNamespace(provider=provider, user_config=config or {"webhook_secret": "x"})


def _log(status: str, started: datetime):
    return SimpleNamespace(status=status, started_at=started)


NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)


def test_ok_after_fresh_success():
    surface = instance_status_surface(
        _integration(), [_log("success", NOW - timedelta(minutes=5))], now=NOW
    )
    assert surface["status"] == "ok"
    assert surface["consecutive_failures"] == 0
    assert surface["last_success_at"] is not None


def test_degraded_after_one_failure():
    surface = instance_status_surface(
        _integration(),
        [_log("failed", NOW - timedelta(minutes=5)), _log("success", NOW - timedelta(hours=2))],
        now=NOW,
    )
    assert surface["status"] == "degraded"
    assert surface["consecutive_failures"] == 1
    assert surface["last_success_at"] is not None


def test_failing_after_two_consecutive_failures():
    surface = instance_status_surface(
        _integration(),
        [_log("failed", NOW - timedelta(minutes=5)), _log("failed", NOW - timedelta(hours=1))],
        now=NOW,
    )
    assert surface["status"] == "failing"
    assert surface["consecutive_failures"] == 2


def test_stale_when_last_success_is_old():
    surface = instance_status_surface(
        _integration(), [_log("success", NOW - timedelta(days=3))], now=NOW
    )
    assert surface["status"] == "stale"


def test_no_history_is_ok_without_last_success():
    surface = instance_status_surface(_integration(), [], now=NOW)
    assert surface["status"] == "ok"
    assert surface["last_success_at"] is None


def test_needs_config_when_required_field_missing():
    """D21: stored config that stops validating against the manifest's
    required configSchema fields carries the needs-config condition —
    never a silent default."""
    integration = _integration(config={"instance_name": "n"})  # no webhook_secret
    with patch("app.extensions.registry.integration_registry") as reg:
        manifest = {
            "configSchema": {
                "type": "object",
                "properties": {"webhook_secret": {"type": "string", "x-secret": True}},
                "required": ["webhook_secret"],
            }
        }
        reg.get_manifest.return_value = manifest
        surface = instance_status_surface(integration, [], now=NOW)
    assert surface["conditions"] == ["needs-config"]


def test_no_needs_config_when_requirements_met():
    with patch("app.extensions.registry.integration_registry") as reg:
        reg.get_manifest.return_value = {
            "configSchema": {
                "type": "object",
                "properties": {"webhook_secret": {"type": "string", "x-secret": True}},
                "required": ["webhook_secret"],
            }
        }
        surface = instance_status_surface(_integration(), [], now=NOW)
    assert "conditions" not in surface
