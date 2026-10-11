"""§16 rate-limit knob routing in the deployed shape (plan 21 + the
standalone/prod compose env contract).

The compose files forward unset optional knobs as blank strings
(``${HA_RATELIMIT_AUTH:-}``); a blank ceiling must mean "unset" (the
route default applies), never a boot-fatal ``int('')``. Found by the
2026-10-11 deploy rehearsal; pinned here so the compose↔Settings
contract cannot drift apart again.
"""

from __future__ import annotations

import pytest

from tests.settings_factory import dev_settings, validate_field_names

_CEILING_FIELDS = (
    "HA_RATELIMIT_AUTH",
    "HA_RATELIMIT_AUTH_EMAIL",
    "HA_RATELIMIT_AI",
    "HA_RATELIMIT_MCP",
    "HA_RATELIMIT_DEFAULT",
)


def _deployed_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    """Mirror the compose anchor: every ceiling forwarded, unset ones blank."""
    validate_field_names(**dict.fromkeys(_CEILING_FIELDS))
    for name in _CEILING_FIELDS:
        monkeypatch.setenv(name, values.get(name, ""))
    monkeypatch.setenv("HA_RATELIMIT_ENABLED", values.get("HA_RATELIMIT_ENABLED", "true"))
    monkeypatch.setenv("HA_TRUSTED_PROXY_COUNT", values.get("HA_TRUSTED_PROXY_COUNT", "0"))


def test_blank_compose_knobs_boot_and_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _deployed_env(monkeypatch)
    settings = dev_settings()
    assert settings.HA_RATELIMIT_AUTH is None
    assert settings.HA_RATELIMIT_AI is None
    assert settings.HA_RATELIMIT_ENABLED is True


def test_set_compose_knob_reaches_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _deployed_env(monkeypatch, HA_RATELIMIT_AUTH="5", HA_RATELIMIT_ENABLED="false")
    settings = dev_settings()
    assert settings.HA_RATELIMIT_AUTH == 5
    assert settings.HA_RATELIMIT_ENABLED is False


def test_blank_knob_whitespace_is_also_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HA_RATELIMIT_AUTH", "   ")
    settings = dev_settings()
    assert settings.HA_RATELIMIT_AUTH is None
