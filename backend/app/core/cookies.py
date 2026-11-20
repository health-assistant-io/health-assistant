"""Cookie sessions + double-submit CSRF (identity-auth §10, plan 16 H3).

Browsers authenticate with cookies; user clients keep Bearer (§9 — the
Android/integrations HMAC bridge is behaviorally frozen and unaffected:
its requests carry neither session cookies nor this middleware's
attention). Token semantics per §10:

===============  =====================  =====================================
cookie           value                  flags
===============  =====================  =====================================
``ha_access``    access JWT (session)   HttpOnly; SameSite=Lax; Secure;
``__Host-ha_...`` (when TLS)           Path=``/``; Max-Age=access TTL
``ha_refresh``   refresh JWT            HttpOnly; SameSite=Lax; Secure;
                                        Path=``/api/v1/auth``; Max-Age=
                                        refresh TTL (cannot take the
                                        ``__Host-`` prefix — its Path is
                                        narrower than ``/``)
``ha_csrf``      random (JS-readable)   NOT HttpOnly; SameSite=Lax;
                                        Secure; Path=``/``; Max-Age=
                                        access TTL
===============  =====================  =====================================

CSRF rule (double submit): a non-safe ``/api/*`` request that carries any
session/CSRF cookie must echo the ``ha_csrf`` cookie value in the
``X-CSRF-Token`` header — mismatch (or absence) is a 403. Requests with
an ``Authorization`` header are exempt (§9 Bearer clients are not cookie
sessions, and a cross-site attacker cannot set that header without a
failed CORS preflight). The auth bootstrap endpoints (login / refresh /
register / setup / demo-login) and health/docs are exempt: they run
before a session exists (refresh is exempt because its rotation must
keep working after the CSRF cookie — same TTL as the access cookie — has
expired).
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
COOKIE_PATH_ROOT = "/"
COOKIE_PATH_AUTH = "/api/v1/auth"

# Per-app prefix (family co-location): cookies are scoped by hostname —
# ports are ignored — so family apps on one host need distinct names.
# Pre-prefix `nx_*` names linger in upgraded browsers for up to the
# refresh absolute TTL; the CSRF middleware expires them on sight.
ACCESS_COOKIE = "ha_access"
ACCESS_COOKIE_HOST_PREFIXED = "__Host-ha_access"
REFRESH_COOKIE = "ha_refresh"
CSRF_COOKIE = "ha_csrf"
CSRF_HEADER = "x-csrf-token"
LEGACY_ACCESS_COOKIE = "nx_access"
LEGACY_ACCESS_COOKIE_HOST_PREFIXED = "__Host-nx_access"
LEGACY_REFRESH_COOKIE = "nx_refresh"
LEGACY_CSRF_COOKIE = "nx_csrf"
LEGACY_COOKIE_NAMES = (
    LEGACY_ACCESS_COOKIE,
    LEGACY_REFRESH_COOKIE,
    LEGACY_CSRF_COOKIE,
    LEGACY_ACCESS_COOKIE_HOST_PREFIXED,
)

# Non-safe requests to these path prefixes skip the double-submit check
# (bootstrap endpoints + health/docs). Logout is deliberately NOT here:
# it is a cookie-authenticated session action and must be CSRF-gated.
# /auth/mfa/* runs pre-session (like login): the challenge is answered
# before any cookie exists, and stale cookies from a dead prior session
# must not 403 the verify (plan 16 H5).
CSRF_EXEMPT_PREFIXES = (
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/register",
    "/api/v1/auth/setup",
    "/api/v1/auth/demo-login",
    "/api/v1/auth/mfa",
    "/api/v1/auth/passkeys",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


@dataclass(frozen=True)
class CookieNames:
    access: str
    refresh: str
    csrf: str


def cookie_names() -> CookieNames:
    """``__Host-ha_access`` under TLS (§10); ``ha_refresh`` cannot use the
    prefix (its Path is narrower than ``/``), matching the table above."""
    access = ACCESS_COOKIE_HOST_PREFIXED if settings.HA_COOKIE_SECURE else ACCESS_COOKIE
    return CookieNames(access=access, refresh=REFRESH_COOKIE, csrf=CSRF_COOKIE)


def access_cookie_candidates() -> tuple[str, ...]:
    """Both possible access-cookie names — readers accept either so a
    deployment that flips ``HA_COOKIE_SECURE`` keeps verifying sessions
    minted under the previous name."""
    return (ACCESS_COOKIE, ACCESS_COOKIE_HOST_PREFIXED)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _samesite() -> str:
    return settings.HA_COOKIE_SAMESITE.lower()


def _access_max_age() -> int:
    return max(1, settings.HA_AUTH_ACCESS_TTL_MINUTES) * 60


def _refresh_max_age() -> int:
    return max(1, settings.HA_AUTH_REFRESH_TTL_DAYS) * 86400


def set_session_cookies(
    response: Any,
    *,
    access_token: str,
    refresh_token: str | None = None,
    csrf_token: str | None = None,
) -> None:
    """Stamp the §10 cookie triple onto ``response`` (FastAPI ``Response``).

    ``csrf_token`` is minted fresh when not supplied; it rotates with
    every issuance (login / refresh / switch), which is safe — the browser
    and the echoing header both read the same cookie.
    """
    names = cookie_names()
    secure = settings.HA_COOKIE_SECURE
    samesite = _samesite()
    csrf = csrf_token or new_csrf_token()

    response.set_cookie(
        names.access,
        access_token,
        max_age=_access_max_age(),
        httponly=True,
        secure=secure,
        samesite=samesite,
        path=COOKIE_PATH_ROOT,
    )
    if refresh_token:
        response.set_cookie(
            names.refresh,
            refresh_token,
            max_age=_refresh_max_age(),
            httponly=True,
            secure=secure,
            samesite=samesite,
            path=COOKIE_PATH_AUTH,
        )
    response.set_cookie(
        names.csrf,
        csrf,
        max_age=_access_max_age(),
        httponly=False,
        secure=secure,
        samesite=samesite,
        path=COOKIE_PATH_ROOT,
    )


def clear_session_cookies(response: Any) -> None:
    """Drop the §10 cookie triple (logout / logout-all).

    Also clears the *other* access-cookie name so an instance that
    flipped ``HA_COOKIE_SECURE`` still expires a lingering cookie (the
    ``__Host-`` deletion itself needs Secure set, which it is in that
    mode; in plain mode the extra header is simply ignored by browsers).
    """
    names = cookie_names()
    secure = settings.HA_COOKIE_SECURE
    samesite = _samesite()
    response.delete_cookie(
        names.access, path=COOKIE_PATH_ROOT, secure=secure, samesite=samesite, httponly=True
    )
    other = (
        ACCESS_COOKIE
        if names.access == ACCESS_COOKIE_HOST_PREFIXED
        else ACCESS_COOKIE_HOST_PREFIXED
    )
    if other != names.access:
        response.delete_cookie(
            other, path=COOKIE_PATH_ROOT, secure=secure, samesite=samesite, httponly=True
        )
    response.delete_cookie(
        names.refresh, path=COOKIE_PATH_AUTH, secure=secure, samesite=samesite, httponly=True
    )
    response.delete_cookie(names.csrf, path=COOKIE_PATH_ROOT, secure=secure, samesite=samesite)


def parse_cookies(header: str | None) -> dict[str, str]:
    """Minimal ``Cookie:`` header parser (name=value; …, split on the
    first ``=`` so padded base64url values survive)."""
    cookies: dict[str, str] = {}
    if not header:
        return cookies
    for chunk in header.split(";"):
        name, _, value = chunk.strip().partition("=")
        if name:
            cookies[name] = value
    return cookies


class LegacyCookieClearingMiddleware:
    """Expire pre-prefix ``nx_*`` cookies once, on sight.

    Upgraded browsers still hold the family-shared names for up to the
    refresh absolute TTL; left alone they shadow nothing here (the app
    reads only ``ha_*``) but ride every request and can trip CSRF logic
    elsewhere. Any request carrying a legacy name gets matching expired
    ``Set-Cookie`` headers — ``nx_refresh`` at both its historical Paths,
    both access variants — so the jar cleans up on the first response
    after the upgrade. Read-only otherwise.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        present = [
            name for name in LEGACY_COOKIE_NAMES if name in parse_cookies(headers.get("cookie"))
        ]
        if not present:
            await self.app(scope, receive, send)
            return

        async def send_with_clearing(message: Any) -> None:
            if message["type"] == "http.response.start":
                extra: list[tuple[bytes, bytes]] = []
                for name in present:
                    paths = (COOKIE_PATH_ROOT, COOKIE_PATH_AUTH) if name == LEGACY_REFRESH_COOKIE else (COOKIE_PATH_ROOT,)
                    for path in paths:
                        extra.append(
                            (
                                b"set-cookie",
                                f"{name}=; Max-Age=0; Path={path}; SameSite=Lax".encode("ascii"),
                            )
                        )
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        await self.app(scope, receive, send_with_clearing)


class CsrfMiddleware:
    """Double-submit CSRF (identity-auth §10) — pure ASGI, no config.

    Rule (kept intentional, mirroring the auth-kit): a non-safe request
    that **carries any auth/csrf cookie** must echo the ``ha_csrf``
    cookie value in ``X-CSRF-Token``. Cookie-less requests (login /
    register before a session exists, HMAC bridge callbacks, bearer
    clients) pass — they are not cookie-authenticated, so CSRF does not
    apply to them. A session cookie without a CSRF echo is a 403.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        if headers.get("authorization"):
            # §9: Bearer-authenticated user clients are not cookie
            # sessions — CSRF does not apply (and a cross-site page
            # cannot set this header without a failed CORS preflight).
            await self.app(scope, receive, send)
            return

        if any(path == prefix or path.startswith(prefix) for prefix in CSRF_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        cookies = parse_cookies(headers.get("cookie"))
        csrf_cookie = cookies.get(CSRF_COOKIE, "")
        session_present = any(
            name in cookies for name in (*access_cookie_candidates(), REFRESH_COOKIE)
        )
        if not csrf_cookie and not session_present:
            await self.app(scope, receive, send)
            return

        presented = headers.get(CSRF_HEADER, "")
        if not csrf_cookie or not presented or not hmac.compare_digest(csrf_cookie, presented):
            await self._forbidden(send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _forbidden(send: Any) -> None:
        body = b'{"detail":"CSRF token missing or invalid"}'
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
