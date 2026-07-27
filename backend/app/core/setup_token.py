"""First-run setup token — four modes (see dev/audits/setup-token-modes.md).

The decision "is this deployment exposed?" is a **deployment-time choice**,
not a runtime guess. Operators choose one of four modes through
``SETUP_TOKEN_MODE``:

- ``log`` (default)      — lifespan prints a one-time random token to the
                           container logs. Required for non-localhost /
                           non-dev requests. The existing manual-install UX.
- ``env``                — the active token is seeded from
                           ``SETUP_BOOTSTRAP_TOKEN`` (no random mint). The
                           launcher composes the wizard URL with
                           ``?token=<value>`` → one-click, no log-grep.
                           Required for non-localhost / non-dev.
- ``time``               — tokenless for ``SETUP_TOKEN_GRACE_MINUTES`` after
                           first boot, then required. After the window
                           expires, lazily mints + logs a token on the next
                           ``is_setup_token_required`` call (falls back to
                           ``log`` semantics) if no env token was set.
- ``disabled``           — never required. Logs a security warning on boot.
                           Only safe behind a firewall / VPN / 127.0.0.1 bind.

Hosts that already bypass the token — localhost requests and dev/test envs —
keep bypassing in every mode (these are not deployment-target decisions).

State is module-level: the standalone HTTP stack is a single uvicorn process
(migration to Redis unchanged in shape if multi-worker ever lands). With
``time`` mode, a worker restart re-opens the grace window — documented;
operators that want a stable window should set ``SETUP_TOKEN_MODE=env``.

One-shot semantics: ``clear()`` (called by ``POST /auth/setup`` on success)
invalidates the active token in every mode — the system then refuses to
re-bootstrap (410 GONE on ``/auth/setup`` because ``_is_initialized`` is true).
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Optional

from fastapi import Request

from app.core.config import settings

logger = logging.getLogger(__name__)

_LOCALHOST_HOSTS = {"127.0.0.1", "::1", "localhost"}
_DEV_ENVS = {"development", "test", "testing"}

_current_token: Optional[str] = None
_boot_time: Optional[float] = None
_post_grace_token_minted: bool = False


def _format(raw: str) -> str:
    """Render the token as two hyphen-separated halves for readability."""
    mid = len(raw) // 2
    return f"{raw[:mid]}-{raw[mid:]}" if mid else raw


def current_mode() -> str:
    """Return the resolved setup-token mode (after config fallback)."""
    return settings.SETUP_TOKEN_MODE


def generate() -> str:
    """Generate (and store) a fresh one-time setup token.

    Returns the formatted token so the caller can log it.
    """
    global _current_token
    raw = secrets.token_urlsafe(24)
    _current_token = _format(raw)
    return _current_token


def seed_from_env(value: Optional[str]) -> bool:
    """Seed the active token from an operator-supplied value (``env`` mode).

    Returns True if the token was actually set (a non-empty ``value`` was
    provided). An empty/whitespace value returns False and leaves any
    existing token untouched — the caller should fall back to ``log`` mode.
    """
    global _current_token
    if not value or not value.strip():
        return False
    _current_token = value.strip()
    return True


def mark_boot_time() -> None:
    """Record the lifespan boot moment (``time`` mode grace window start)."""
    global _boot_time
    _boot_time = time.time()


def _grace_window_seconds() -> float:
    return float(settings.SETUP_TOKEN_GRACE_MINUTES) * 60.0


def is_within_grace_window() -> bool:
    """True if ``time`` mode is still inside its tokenless grace window."""
    if _boot_time is None:
        return False
    return (time.time() - _boot_time) <= _grace_window_seconds()


def get() -> Optional[str]:
    """Return the current setup token, if any (None after first use)."""
    return _current_token


def validate(token: Optional[str]) -> bool:
    """True if the supplied token matches the current setup token.

    An empty/None current token (already consumed, or never set in disabled /
    time-within-grace / dev) is only valid when the request does not require
    a token at all — callers must gate on :func:`is_setup_token_required`
    first.
    """
    if _current_token is None:
        return False
    if token is None:
        return False
    return secrets.compare_digest(token, _current_token)


def clear() -> None:
    """Invalidate the setup token (called after a successful setup)."""
    global _current_token, _post_grace_token_minted
    _current_token = None
    _post_grace_token_minted = False


def _request_host(request: Request) -> str:
    host = getattr(request, "client", None)
    if host and getattr(host, "host", None):
        return host.host
    return ""


def is_local_request(request: Request) -> bool:
    """True if the request originates from localhost."""
    return _request_host(request) in _LOCALHOST_HOSTS


def _is_dev_env() -> bool:
    return settings.APP_ENV in _DEV_ENVS


def _ensure_post_grace_token() -> None:
    """``time`` mode after the grace window: lazily mint + log a token once.

    Per D5 of the audit: if the window expired and the operator didn't
    pre-provision ``SETUP_BOOTSTRAP_TOKEN``, the system transitions to
    ``log`` semantics on the first call after the window so the deploy
    isn't stuck unable to bootstrap. Idempotent within a single process
    (``_post_grace_token_minted`` guards the log+mint one-shot).
    """
    global _post_grace_token_minted
    if _post_grace_token_minted:
        return
    _post_grace_token_minted = True
    token = generate()
    logger.warning(
        "\n══════════════════════════════════════════════════════\n"
        " SETUP TOKEN GRACE WINDOW EXPIRED\n"
        " The tokenless setup window (SETUP_TOKEN_MODE=time, %d min) has\n"
        " elapsed. A one-time setup token has been minted and is\n"
        " required to complete first-run setup.\n"
        "   %s\n"
        " Retrieve later: docker compose ... logs backend"
        " | grep -i -A 1 'setup token'\n"
        "══════════════════════════════════════════════════════",
        settings.SETUP_TOKEN_GRACE_MINUTES,
        token,
    )


def is_setup_token_required(request: Request) -> bool:
    """Whether the setup wizard must present the setup token.

    Decision tree (see dev/audits/setup-token-modes.md §D1):

    1. Localhost request OR dev/test env → never required (escape hatch).
    2. Mode dispatch:
       - ``log``      → required (the active token was minted+logged on boot).
       - ``env``      → required (token was seeded from SETUP_BOOTSTRAP_TOKEN).
       - ``time``     → not required within the grace window; required after
                        (lazily mints + logs a token if no env seed).
       - ``disabled`` → never required.
    """
    if is_local_request(request) or _is_dev_env():
        return False

    mode = current_mode()
    if mode == "disabled":
        return False
    if mode == "time":
        if is_within_grace_window():
            return False
        # Grace expired — make sure a token exists for validate() to use.
        _ensure_post_grace_token()
        return True
    # ``log`` and ``env`` both require the active token.
    return True