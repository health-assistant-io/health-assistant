"""Boot-time env-name typo telemetry (audit follow-up a).

``extra="ignore"`` on the Settings model silently drops unknown env vars —
an operator's typo'd ``APP_ENV=production`` boots in development with zero
signal. The ``_warn_unknown_env_names`` validator makes that class loud:
warning-only, never a refusal (the family's refusal gates are the boot
guards). These tests pin the four contract points:

* a known-RENAMED name warns naming its replacement;
* an unknown ``HA_*`` name warns;
* a fully valid environment warns nothing;
* production boot behavior is unchanged (guards still refuse, telemetry
  never does).
"""

from __future__ import annotations

import logging
import os

import pytest
from pydantic import ValidationError

from app.core.config import RENAMED_ENV_HINTS, TOOLING_ENV_NAMES
from tests import settings_factory

# Every warning this feature emits starts with one of these — the filter
# that keeps the "warns nothing" test independent of unrelated validators
# (e.g. the dev-mode ephemeral-key warnings).
_TELEMETRY_PREFIXES = ("Renamed env var ", "Unknown env var ")


def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every name the telemetry watches — hermetic baseline.

    pytest-env loads the repo .env / .env.test into os.environ, and the
    developer shell may carry anything; scrub first so each test controls
    exactly which names are set. monkeypatch restores the world afterwards.
    """
    for name in list(os.environ):
        if name.startswith("HA_") or name in RENAMED_ENV_HINTS:
            monkeypatch.delenv(name, raising=False)


def _telemetry_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.getMessage().startswith(_TELEMETRY_PREFIXES)
    ]


def test_renamed_app_env_warns_naming_the_correct_name(monkeypatch, caplog):
    """The audit's origin story: typo'd APP_ENV=production must not be silent."""
    _scrub_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")  # gate-allow: APP_ENV
    with caplog.at_level(logging.WARNING):
        settings_factory.dev_settings()
    messages = [r.getMessage() for r in _telemetry_records(caplog)]
    assert len(messages) == 1, messages
    assert "Renamed env var APP_ENV" in messages[0]
    assert "HA_APP_ENV" in messages[0], "the warning must name the correct replacement"


def test_renamed_secret_key_warns_naming_the_key_family(monkeypatch, caplog):
    _scrub_env(monkeypatch)
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    with caplog.at_level(logging.WARNING):
        settings_factory.dev_settings()
    messages = [r.getMessage() for r in _telemetry_records(caplog)]
    assert len(messages) == 1, messages
    assert "Renamed env var SECRET_KEY" in messages[0]
    assert "HA_SESSION_KEY" in messages[0]
    assert "HA_DATA_KEY" in messages[0]


def test_unknown_ha_name_warns(monkeypatch, caplog):
    _scrub_env(monkeypatch)
    # HA_FOO_BAR: a deliberately unknown probe name — declared in
    # test_config_contract.UNREAD_BY_DESIGN (set to prove the ignore-path
    # telemetry, not read by the app).
    monkeypatch.setenv("HA_FOO_BAR", "1")
    with caplog.at_level(logging.WARNING):
        settings_factory.dev_settings()
    messages = [r.getMessage() for r in _telemetry_records(caplog)]
    assert len(messages) == 1, messages
    assert "Unknown env var HA_FOO_BAR" in messages[0]


def test_fully_valid_env_warns_nothing(monkeypatch, caplog):
    """A correct environment produces zero telemetry — no false positives."""
    _scrub_env(monkeypatch)
    monkeypatch.setenv("HA_APP_ENV", "development")
    monkeypatch.setenv("HA_COOKIE_SECURE", "false")
    monkeypatch.setenv("HA_WS_ALLOWED_ORIGINS", "http://localhost:3000")
    with caplog.at_level(logging.WARNING):
        settings_factory.dev_settings()
    assert _telemetry_records(caplog) == []


def test_tooling_env_names_are_exempt(monkeypatch, caplog):
    """Launcher/logging names are read outside Settings on purpose — the
    telemetry must not cry wolf on them."""
    _scrub_env(monkeypatch)
    for name in sorted(TOOLING_ENV_NAMES):
        monkeypatch.setenv(name, "1")
    with caplog.at_level(logging.WARNING):
        settings_factory.dev_settings()
    assert _telemetry_records(caplog) == []


def test_telemetry_never_refuses_and_boot_guards_still_do(monkeypatch, caplog):
    """Production boot behavior unchanged: typos warn AND boot; real guard
    misconfiguration still refuses. Telemetry is not a gate."""
    _scrub_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")  # gate-allow: APP_ENV
    monkeypatch.setenv("HA_FOO_BAR", "1")
    with caplog.at_level(logging.WARNING):
        settings = settings_factory.prod_settings()
    assert settings.HA_APP_ENV == "production"
    assert len(_telemetry_records(caplog)) == 2

    # The refusal gates keep their edge (a weak key still refuses to boot).
    with pytest.raises(ValidationError):
        settings_factory.prod_settings(HA_SESSION_KEY="weak")
