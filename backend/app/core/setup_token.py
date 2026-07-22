"""First-run setup token.

On boot, if the database has zero users, the lifespan generates a random
one-time token and prints it to the container logs. The first-run setup
wizard (``POST /auth/setup``) requires it unless the request is local
(localhost) or the app runs in a dev/test env. This closes the
first-claim race for internet-exposed instances: only someone with
server/log access can claim a fresh install.

Storage is a module-level variable — the backend is a single HTTP
process (one uvicorn service in the compose stack), so this is
sufficient for the first-run window. If multi-worker HTTP is ever
introduced, move this to Redis alongside ``app.core.token_store``.
"""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Request

from app.core.config import settings

_LOCALHOST_HOSTS = {"127.0.0.1", "::1", "localhost"}
_DEV_ENVS = {"development", "test", "testing"}

_current_token: Optional[str] = None


def _format(raw: str) -> str:
    """Render the token as two hyphen-separated halves for readability."""
    mid = len(raw) // 2
    return f"{raw[:mid]}-{raw[mid:]}" if mid else raw


def generate() -> str:
    """Generate (and store) a fresh one-time setup token.

    Returns the formatted token so the caller can log it.
    """
    global _current_token
    raw = secrets.token_urlsafe(24)
    _current_token = _format(raw)
    return _current_token


def get() -> Optional[str]:
    """Return the current setup token, if any (None after first use)."""
    return _current_token


def validate(token: Optional[str]) -> bool:
    """True if the supplied token matches the current setup token.

    An empty/None current token (e.g. dev env, or already consumed) is
    only valid when the request does not require a token at all — so
    callers must gate on :func:`is_setup_token_required` first.
    """
    if _current_token is None:
        return False
    if token is None:
        return False
    return secrets.compare_digest(token, _current_token)


def clear() -> None:
    """Invalidate the setup token (called after a successful setup)."""
    global _current_token
    _current_token = None


def _request_host(request: Request) -> str:
    host = getattr(request, "client", None)
    if host and getattr(host, "host", None):
        return host.host
    return ""


def is_local_request(request: Request) -> bool:
    """True if the request originates from localhost."""
    return _request_host(request) in _LOCALHOST_HOSTS


def is_setup_token_required(request: Request) -> bool:
    """Whether the setup wizard must present the setup token.

    Token is *skipped* for localhost requests and for dev/test envs so
    local Docker users aren't burdened. Required everywhere else.
    """
    if is_local_request(request):
        return False
    return settings.APP_ENV not in _DEV_ENVS
