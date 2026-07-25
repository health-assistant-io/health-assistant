"""Tests for the four ``SETUP_TOKEN_MODE`` modes (see
``dev/audits/setup-token-modes.md``).

Pure unit tests on ``app.core.setup_token`` + a few ``setup_status``
endpoint tests for the new ``token_mode`` / ``setup_url_hint`` response
fields. The endpoint behaviour already has its own suite
(``tests/test_auth_setup.py``); we extend coverage for the mode-specific
paths that previously didn't exist.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.core import setup_token
from app.core.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_request() -> MagicMock:
    req = MagicMock()
    req.client = MagicMock(host="127.0.0.1")
    req.headers = {}
    return req


def _remote_request(host: str = "203.0.113.1") -> MagicMock:
    req = MagicMock()
    req.client = MagicMock(host=host)
    req.headers = {}
    return req


@pytest.fixture(autouse=True)
def _reset_token_state():
    """Each test starts from a clean token state."""
    setup_token.clear()
    # Also reset the boot-time marker so 'time' tests don't bleed.
    setup_token._boot_time = None  # type: ignore[attr-defined]
    setup_token._post_grace_token_minted = False  # type: ignore[attr-defined]
    yield
    setup_token.clear()
    setup_token._boot_time = None  # type: ignore[attr-defined]
    setup_token._post_grace_token_minted = False  # type: ignore[attr-defined]


def _set_mode(mode: str, **overrides):
    settings.SETUP_TOKEN_MODE = mode
    for k, v in overrides.items():
        setattr(settings, k, v)


# ---------------------------------------------------------------------------
# current_mode
# ---------------------------------------------------------------------------


def test_current_mode_reflects_settings():
    for mode in ("log", "env", "time", "disabled"):
        _set_mode(mode)
        assert setup_token.current_mode() == mode


# ---------------------------------------------------------------------------
# log mode (default behaviour preserved)
# ---------------------------------------------------------------------------


def test_log_mode_remote_requires_token():
    _set_mode("log", APP_ENV="production")
    assert setup_token.is_setup_token_required(_remote_request()) is True


def test_log_mode_local_never_requires_token():
    _set_mode("log", APP_ENV="production")
    assert setup_token.is_setup_token_required(_local_request()) is False


def test_log_mode_dev_env_never_requires_token():
    _set_mode("log", APP_ENV="development")
    assert setup_token.is_setup_token_required(_remote_request()) is False


def test_log_mode_generate_then_validate_round_trip():
    _set_mode("log", APP_ENV="production")
    token = setup_token.generate()
    assert setup_token.validate(token) is True
    assert setup_token.validate("wrong") is False
    assert setup_token.validate(None) is False
    setup_token.clear()
    assert setup_token.validate(token) is False  # one-shot


# ---------------------------------------------------------------------------
# env mode
# ---------------------------------------------------------------------------


def test_env_mode_seed_from_env_succeeds():
    _set_mode("env", APP_ENV="production", SETUP_BOOTSTRAP_TOKEN="my-launcher-token")
    ok = setup_token.seed_from_env(settings.SETUP_BOOTSTRAP_TOKEN)
    assert ok is True
    assert setup_token.get() == "my-launcher-token"
    assert setup_token.validate("my-launcher-token") is True


def test_env_mode_empty_seed_value_returns_false():
    _set_mode("env", APP_ENV="production", SETUP_BOOTSTRAP_TOKEN="   ")
    ok = setup_token.seed_from_env(settings.SETUP_BOOTSTRAP_TOKEN)
    assert ok is False
    assert setup_token.get() is None


def test_env_mode_remote_requires_token_after_seed():
    _set_mode("env", APP_ENV="production", SETUP_BOOTSTRAP_TOKEN="tk")
    setup_token.seed_from_env(settings.SETUP_BOOTSTRAP_TOKEN)
    assert setup_token.is_setup_token_required(_remote_request()) is True


def test_env_mode_one_shot_clear():
    _set_mode("env", APP_ENV="production", SETUP_BOOTSTRAP_TOKEN="tk")
    setup_token.seed_from_env(settings.SETUP_BOOTSTRAP_TOKEN)
    assert setup_token.validate("tk") is True
    setup_token.clear()
    # After clear(), the env token is dead — even a re-supply of the same
    # value does not resurrect it (the system refuses re-bootstrap at the
    # endpoint level via _is_initialized → 410).
    assert setup_token.get() is None
    assert setup_token.validate("tk") is False


# ---------------------------------------------------------------------------
# time mode
# ---------------------------------------------------------------------------


def test_time_mode_within_window_never_requires_token():
    _set_mode("time", APP_ENV="production", SETUP_TOKEN_GRACE_MINUTES=30)
    setup_token.mark_boot_time()
    assert setup_token.is_setup_token_required(_remote_request()) is False


def test_time_mode_local_never_requires_token_even_in_window():
    _set_mode("time", APP_ENV="production")
    setup_token.mark_boot_time()
    assert setup_token.is_setup_token_required(_local_request()) is False


def test_time_mode_after_window_requires_token_and_mints_one():
    _set_mode("time", APP_ENV="production", SETUP_TOKEN_GRACE_MINUTES=30)
    setup_token.mark_boot_time()
    # Backdate the boot time past the window.
    setup_token._boot_time = time.time() - (31 * 60)  # type: ignore[attr-defined]
    assert setup_token.is_within_grace_window() is False
    # The is_required call also lazily mints a token (log-mode fallback).
    with patch("app.core.setup_token.logger") as mock_log:
        assert setup_token.is_setup_token_required(_remote_request()) is True
    # The mint-once path logs a warning + the new token.
    assert mock_log.warning.called
    assert setup_token.get() is not None
    # Validator accepts the freshly-minted token.
    assert setup_token.validate(setup_token.get()) is True


def test_time_mode_mint_after_window_is_one_shot_per_process():
    _set_mode("time", APP_ENV="production", SETUP_TOKEN_GRACE_MINUTES=30)
    setup_token.mark_boot_time()
    setup_token._boot_time = time.time() - (31 * 60)  # type: ignore[attr-defined]
    setup_token.is_setup_token_required(_remote_request())
    first_token = setup_token.get()
    # A second call within the same process does NOT mint another token.
    with patch("app.core.setup_token.generate") as mock_gen:
        setup_token.is_setup_token_required(_remote_request())
        mock_gen.assert_not_called()
    assert setup_token.get() == first_token


def test_time_mode_clear_resets_grace_mint_flag():
    _set_mode("time", APP_ENV="production", SETUP_TOKEN_GRACE_MINUTES=30)
    setup_token.mark_boot_time()
    setup_token._boot_time = time.time() - (31 * 60)  # type: ignore[attr-defined]
    setup_token.is_setup_token_required(_remote_request())
    first_token = setup_token.get()
    assert first_token is not None
    setup_token.clear()
    # After clear(), the post-grace mint flag is reset so a subsequent
    # is_required() lazily mints a NEW token (the old one is dead — one-shot).
    assert setup_token.is_setup_token_required(_remote_request()) is True
    new_token = setup_token.get()
    assert new_token is not None
    assert new_token != first_token  # re-minted because clear reset the flag
    assert setup_token.validate(first_token) is False  # old token is dead
    assert setup_token.validate(new_token) is True


# ---------------------------------------------------------------------------
# disabled mode
# ---------------------------------------------------------------------------


def test_disabled_mode_never_requires_token():
    _set_mode("disabled", APP_ENV="production")
    assert setup_token.is_setup_token_required(_remote_request()) is False
    assert setup_token.is_setup_token_required(_local_request()) is False


def test_disabled_mode_clear_is_a_noop_for_required_flag():
    _set_mode("disabled", APP_ENV="production")
    setup_token.clear()
    assert setup_token.is_setup_token_required(_remote_request()) is False


# ---------------------------------------------------------------------------
# setup_status endpoint — token_mode + setup_url_hint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_status_reports_token_mode_for_each_mode():
    from app.api.v1.endpoints import auth as auth_endpoint
    from unittest.mock import AsyncMock

    for mode in ("log", "env", "time", "disabled"):
        _set_mode(
            mode,
            APP_ENV="production",
            SETUP_BOOTSTRAP_TOKEN=("tk" if mode == "env" else None),
        )
        if mode == "env":
            setup_token.seed_from_env(settings.SETUP_BOOTSTRAP_TOKEN)
        if mode == "time":
            setup_token.mark_boot_time()
        with patch.object(
            auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
        ):
            result = await auth_endpoint.setup_status(
                request=_remote_request(), db=MagicMock()
            )
        assert result.token_mode == mode


@pytest.mark.asyncio
async def test_setup_status_emits_setup_url_hint_only_in_env_mode():
    from app.api.v1.endpoints import auth as auth_endpoint
    from unittest.mock import AsyncMock

    _set_mode("env", APP_ENV="production", SETUP_BOOTSTRAP_TOKEN="launcher-secret")
    setup_token.seed_from_env(settings.SETUP_BOOTSTRAP_TOKEN)

    req = MagicMock()
    req.client = MagicMock(host="example.com")
    req.url.scheme = "https"
    req.headers = {"host": "example.com"}

    with patch.object(
        auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
    ):
        result = await auth_endpoint.setup_status(request=req, db=MagicMock())

    assert result.setup_url_hint is not None
    assert "launcher-secret" in result.setup_url_hint
    assert result.setup_url_hint.startswith("https://example.com/setup?token=")


@pytest.mark.asyncio
async def test_setup_status_no_url_hint_in_log_mode():
    from app.api.v1.endpoints import auth as auth_endpoint
    from unittest.mock import AsyncMock

    _set_mode("log", APP_ENV="production")
    setup_token.generate()

    req = MagicMock()
    req.client = MagicMock(host="example.com")
    req.url.scheme = "https"
    req.headers = {"host": "example.com"}

    with patch.object(
        auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
    ):
        result = await auth_endpoint.setup_status(request=req, db=MagicMock())

    assert result.setup_url_hint is None
    assert result.token_mode == "log"


@pytest.mark.asyncio
async def test_setup_status_no_url_hint_after_initialize_in_env_mode():
    """Once initialized, the env-mode URL hint is suppressed (the token is dead)."""
    from app.api.v1.endpoints import auth as auth_endpoint
    from unittest.mock import AsyncMock

    _set_mode("env", APP_ENV="production", SETUP_BOOTSTRAP_TOKEN="tk")
    setup_token.seed_from_env(settings.SETUP_BOOTSTRAP_TOKEN)
    # Simulate a successful /auth/setup -> _is_initialized True + clear.
    setup_token.clear()

    req = MagicMock()
    req.client = MagicMock(host="example.com")
    req.url.scheme = "https"
    req.headers = {"host": "example.com"}

    with patch.object(
        auth_endpoint, "_is_initialized", new=AsyncMock(return_value=True)
    ):
        result = await auth_endpoint.setup_status(request=req, db=MagicMock())

    assert result.setup_url_hint is None
    assert result.initialized is True


# ---------------------------------------------------------------------------
# Config validator — bad mode name + env fallback
# ---------------------------------------------------------------------------


def test_config_rejects_unknown_mode():
    """Settings rejects an invalid SETUP_TOKEN_MODE."""
    from pydantic import ValidationError
    from app.core.config import Settings

    # Settings has its own顾_ validators (DB creds, secret key, VAPID).
    # Build with bare-minimum env to satisfy the prod guards, then flip mode.
    with patch.dict("os.environ", {
        "APP_ENV": "development",
        "SETUP_TOKEN_MODE": "bogus",
    }, clear=False):
        with pytest.raises((ValidationError, ValueError)):
            Settings()


def test_config_env_mode_with_empty_token_falls_back_to_log():
    from app.core.config import Settings

    with patch.dict("os.environ", {
        "APP_ENV": "development",
        "SETUP_TOKEN_MODE": "env",
        "SETUP_BOOTSTRAP_TOKEN": "",
    }, clear=False):
        s = Settings()
        assert s.SETUP_TOKEN_MODE == "log"  # downgraded


def test_config_rejects_grace_below_one_minute():
    from app.core.config import Settings
    from pydantic import ValidationError

    with patch.dict("os.environ", {
        "APP_ENV": "development",
        "SETUP_TOKEN_MODE": "time",
        "SETUP_TOKEN_GRACE_MINUTES": "0",
    }, clear=False):
        with pytest.raises((ValidationError, ValueError)):
            Settings()