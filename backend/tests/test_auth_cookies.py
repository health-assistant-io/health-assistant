"""Cookie sessions + double-submit CSRF + the WS Origin gate (plan 16 H3).

Covers identity-auth §10 as landed for health:

- the §10 cookie triple (flags, paths, ``__Host-`` prefix under Secure);
- double-submit CSRF: missing / wrong echo ⇒ 403, matching echo passes,
  Bearer-authenticated requests exempt (§9), cookie-less requests pass;
- cookie-authenticated requests end-to-end (``get_token`` cookie-first);
- refresh rotation rotates the cookies (cookie-presented refresh);
- logout / logout-all clear the triple;
- the WS handshake: ``nx_access`` cookie accepted, disallowed ``Origin``
  rejected with 1008, configured ``HA_WS_ALLOWED_ORIGINS`` honored, the
  §9 subprotocol channel keeps working;
- a §9 user-client Bearer flow end-to-end (no cookies involved).

The HMAC bridge is untouched — see test_websocket_webhook_hmac.py and
test_bridge_* (frozen per §9); nothing here may change their behavior.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient as FastTestClient
from httpx import AsyncClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.main import app
from tests._auth_helpers import create_user, sign_in

# Every test here implements identity-auth §18.5 (cookie triple, CSRF,
# WS Origin gate) plus the §18.3 rotation/replay halves — drift gate.
pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _set_cookies_by_name(response) -> dict[str, list[str]]:
    """Raw Set-Cookie headers keyed by cookie name (flags preserved)."""
    jars: dict[str, list[str]] = {}
    for raw in response.headers.get_list("set-cookie"):
        name = raw.split("=", 1)[0]
        jars.setdefault(name, []).append(raw)
    return jars


def _flag(cookie_header: str, flag: str) -> bool:
    """Case-insensitive attribute check (gotcha #6: SameSite/HttpOnly case)."""
    parts = [p.strip().lower() for p in cookie_header.split(";")]
    return any(p.startswith(flag.lower()) for p in parts)


def _attr(cookie_header: str, key: str) -> str | None:
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k.lower() == key.lower():
            return v
    return None


async def _login_client(async_client: AsyncClient):
    """A real password login through the endpoint (cookie jar populated)."""
    from tests._auth_helpers import create_user as _cu

    user = await _cu(password="super-secret-pass-1")
    resp = await async_client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": "super-secret-pass-1"},
    )
    assert resp.status_code == 200, resp.text
    return user, resp


# ---------------------------------------------------------------------------
# §10 cookie flags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_sets_contract_cookie_triple(async_client: AsyncClient):
    """login stamps nx_access / nx_refresh / nx_csrf with the §10 flags."""
    _, resp = await _login_client(async_client)

    jars = _set_cookies_by_name(resp)

    access = jars["nx_access"][0]
    assert _flag(access, "HttpOnly"), access
    assert _flag(access, "SameSite=Lax"), access
    assert _attr(access, "Path") == "/", access
    assert int(_attr(access, "Max-Age") or 0) > 0
    # Plain HTTP default: no Secure (HA_COOKIE_SECURE=false ⇒ no __Host-).
    assert not _flag(access, "Secure"), access
    assert "__Host-" not in jars, jars.keys()

    refresh = jars["nx_refresh"][0]
    assert _flag(refresh, "HttpOnly"), refresh
    assert _flag(refresh, "SameSite=Lax"), refresh
    # The refresh cookie is pinned to the auth prefix (§10).
    assert _attr(refresh, "Path") == "/api/v1/auth", refresh

    csrf = jars["nx_csrf"][0]
    assert not _flag(csrf, "HttpOnly"), "nx_csrf must stay JS-readable"
    assert _flag(csrf, "SameSite=Lax"), csrf
    assert _attr(csrf, "Path") == "/", csrf


@pytest.mark.asyncio
async def test_secure_mode_uses_host_prefixed_access_cookie(async_client: AsyncClient):
    """HA_COOKIE_SECURE=true ⇒ __Host-nx_access + Secure on everything."""
    user = await create_user(password="super-secret-pass-2")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(settings, "HA_COOKIE_SECURE", True)
    try:
        resp = await async_client.post(
            "/api/v1/auth/login",
            data={"username": user.email, "password": "super-secret-pass-2"},
        )
    finally:
        monkey.undo()

    assert resp.status_code == 200, resp.text
    jars = _set_cookies_by_name(resp)
    assert "__Host-nx_access" in jars, jars.keys()
    assert "nx_access" not in jars
    assert _flag(jars["__Host-nx_access"][0], "Secure")
    assert _flag(jars["nx_refresh"][0], "Secure")
    assert _flag(jars["nx_csrf"][0], "Secure")


@pytest.mark.asyncio
async def test_samesite_attribute_is_case_insensitive_parsed():
    """Gotcha #6: the SameSite check must tolerate any attribute casing."""
    header = "nx_access=abc; httponly; samesite=LAX; path=/"
    assert _flag(header, "SameSite=Lax")
    assert _flag(header, "HTTPONLY")


# ---------------------------------------------------------------------------
# CSRF double submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cookie_non_get_without_csrf_header_is_403(async_client: AsyncClient):
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.post(
        "/api/v1/auth/logout",
        cookies={"nx_access": issued.access_token, "nx_csrf": "cookie-value"},
    )
    assert resp.status_code == 403, resp.text
    assert "CSRF" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cookie_non_get_with_wrong_csrf_header_is_403(async_client: AsyncClient):
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.post(
        "/api/v1/auth/logout",
        cookies={"nx_access": issued.access_token, "nx_csrf": "cookie-value"},
        headers={"X-CSRF-Token": "not-the-cookie-value"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cookie_non_get_with_matching_csrf_header_passes(async_client: AsyncClient):
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.post(
        "/api/v1/auth/logout",
        cookies={"nx_access": issued.access_token, "nx_csrf": "cookie-value"},
        headers={"X-CSRF-Token": "cookie-value"},
        json={"refresh_token": issued.refresh_token},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"revoked": True}


@pytest.mark.asyncio
async def test_bearer_requests_are_csrf_exempt(async_client: AsyncClient):
    """§9: Bearer-authenticated user clients never need the CSRF echo."""
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {issued.access_token}"},
        json={"refresh_token": issued.refresh_token},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_cookieless_non_get_is_not_csrf_gated(async_client: AsyncClient):
    """No session/csrf cookies ⇒ the middleware passes (the route's own
    auth/credentials logic answers)."""
    resp = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "ghost-h3@test.local", "password": "wrong-password-x"},
    )
    # Rejected by the login logic (401), not by CSRF (would be 403).
    assert resp.status_code == 401
    assert "CSRF" not in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_requests_are_never_csrf_gated(async_client: AsyncClient):
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.get(
        "/api/v1/auth/validate",
        cookies={"nx_access": issued.access_token, "nx_csrf": "cookie-value"},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


# ---------------------------------------------------------------------------
# cookie-authenticated flow + get_token precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cookie_authenticated_request_flow(async_client: AsyncClient):
    """A browser-style session: login → cookies → authenticated GETs."""
    user, _ = await _login_client(async_client)
    # The httpx jar now carries the §10 triple set by the login response.
    resp = await async_client.get("/api/v1/auth/validate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    assert body["user_id"] == str(user.id)


@pytest.mark.asyncio
async def test_get_token_prefers_cookie_over_bearer(async_client: AsyncClient):
    """Cookie-first precedence: an (invalid) cookie shadows a valid Bearer."""
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.get(
        "/api/v1/auth/validate",
        cookies={"nx_access": "not-a-valid-jwt"},
        headers={"Authorization": f"Bearer {issued.access_token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_bearer_still_works_end_to_end_user_client(async_client: AsyncClient):
    """§9 user client: pure Bearer, no cookies anywhere in the flow."""
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.get(
        "/api/v1/auth/validate",
        headers={"Authorization": f"Bearer {issued.access_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] == str(user.id)

    logout = await async_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {issued.access_token}"},
        json={"refresh_token": issued.refresh_token},
    )
    assert logout.status_code == 200
    # The bearer credential itself is dead now.
    dead = await async_client.get(
        "/api/v1/auth/validate",
        headers={"Authorization": f"Bearer {issued.access_token}"},
    )
    assert dead.status_code == 401


# ---------------------------------------------------------------------------
# refresh: cookie presentation + cookie rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_via_cookie_rotates_cookies(async_client: AsyncClient):
    """Browsers present no body — the nx_refresh cookie drives rotation and
    the response re-stamps the whole §10 triple."""
    user = await create_user()
    issued = await sign_in(user)

    resp = await async_client.post(
        "/api/v1/auth/refresh",
        cookies={"nx_refresh": issued.refresh_token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    jars = _set_cookies_by_name(resp)
    new_access = jars["nx_access"][0]
    new_refresh = jars["nx_refresh"][0]
    new_csrf = jars["nx_csrf"][0]
    assert issued.access_token not in new_access
    assert issued.refresh_token not in new_refresh
    assert body["refresh_token"] != issued.refresh_token
    # The CSRF cookie rotates with the session too (same TTL contract).
    assert _flag(new_csrf, "SameSite=Lax")

    # The rotated pair actually authenticates (cookie-first get_token).
    ok = await async_client.get(
        "/api/v1/auth/validate",
        cookies={"nx_access": body["access_token"]},
    )
    assert ok.status_code == 200

    # The old refresh token is dead (rotation semantics unchanged).
    replay = await async_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": issued.refresh_token},
    )
    assert replay.status_code == 423


@pytest.mark.asyncio
async def test_refresh_without_body_or_cookie_is_401(async_client: AsyncClient):
    resp = await async_client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing refresh token"


# ---------------------------------------------------------------------------
# logout clears the triple
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_clears_cookies(async_client: AsyncClient):
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.post(
        "/api/v1/auth/logout",
        cookies={"nx_access": issued.access_token, "nx_csrf": "cookie-value"},
        headers={"X-CSRF-Token": "cookie-value"},
        json={"refresh_token": issued.refresh_token},
    )
    assert resp.status_code == 200

    jars = _set_cookies_by_name(resp)
    for name in ("nx_access", "nx_refresh", "nx_csrf"):
        assert name in jars, f"logout must clear {name}: {jars.keys()}"
        header = jars[name][0]
        assert int(_attr(header, "Max-Age") or 1) == 0, header


@pytest.mark.asyncio
async def test_logout_all_clears_cookies(async_client: AsyncClient):
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.post(
        "/api/v1/auth/logout-all",
        cookies={"nx_access": issued.access_token, "nx_csrf": "cookie-value"},
        headers={"X-CSRF-Token": "cookie-value"},
    )
    assert resp.status_code == 200

    jars = _set_cookies_by_name(resp)
    for name in ("nx_access", "nx_refresh", "nx_csrf"):
        assert name in jars and int(_attr(jars[name][0], "Max-Age") or 1) == 0


@pytest.mark.asyncio
async def test_logout_via_refresh_cookie_only(async_client: AsyncClient):
    """Body-less logout: the nx_refresh cookie supplies the refresh token."""
    user = await create_user()
    issued = await sign_in(user)
    resp = await async_client.post(
        "/api/v1/auth/logout",
        cookies={
            "nx_access": issued.access_token,
            "nx_refresh": issued.refresh_token,
            "nx_csrf": "cookie-value",
        },
        headers={"X-CSRF-Token": "cookie-value"},
    )
    assert resp.status_code == 200, resp.text
    # The access jti is revoked → the cookie session is dead.
    dead = await async_client.get(
        "/api/v1/auth/validate",
        cookies={"nx_access": issued.access_token},
    )
    assert dead.status_code == 401


# ---------------------------------------------------------------------------
# WS: cookie handshake + Origin gate (§10)
# ---------------------------------------------------------------------------


def _ws_client() -> FastTestClient:
    return FastTestClient(app)


@pytest.mark.asyncio
async def test_ws_cookie_handshake_accepted():
    user = await create_user()
    issued = await sign_in(user)
    client = _ws_client()
    with client.websocket_connect(
        "/api/v1/ws/notifications",
        headers={"cookie": f"nx_access={issued.access_token}"},
    ) as ws:
        # Handshake accepted (a rejection closes with 1008 before accept,
        # which raises WebSocketDisconnect on enter).
        assert ws is not None


@pytest.mark.asyncio
async def test_ws_origin_gate_rejects_unknown_origin():
    user = await create_user()
    issued = await sign_in(user)
    client = _ws_client()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/ws/notifications",
            headers={
                "cookie": f"nx_access={issued.access_token}",
                "origin": "https://evil.example",
            },
        ):
            pass


@pytest.mark.asyncio
async def test_ws_origin_gate_allows_same_origin():
    user = await create_user()
    issued = await sign_in(user)
    client = _ws_client()
    # TestClient base_url is http://testserver → same-origin handshake.
    with client.websocket_connect(
        "/api/v1/ws/notifications",
        headers={
            "cookie": f"nx_access={issued.access_token}",
            "origin": "http://testserver",
        },
    ) as ws:
        assert ws is not None


@pytest.mark.asyncio
async def test_ws_origin_gate_honors_configured_allowlist():
    user = await create_user()
    issued = await sign_in(user)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(settings, "HA_WS_ALLOWED_ORIGINS", "https://spa.example")
    try:
        client = _ws_client()
        with client.websocket_connect(
            "/api/v1/ws/notifications",
            headers={
                "cookie": f"nx_access={issued.access_token}",
                "origin": "https://spa.example",
            },
        ) as ws:
            assert ws is not None
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/v1/ws/notifications",
                headers={
                    "cookie": f"nx_access={issued.access_token}",
                    "origin": "https://other.example",
                },
            ):
                pass
    finally:
        monkey.undo()


@pytest.mark.asyncio
async def test_ws_subprotocol_channel_still_works():
    """§9 (frozen): the ["bearer", token] subprotocol path keeps working —
    the Android app may use it."""
    user = await create_user()
    issued = await sign_in(user)
    client = _ws_client()
    with client.websocket_connect(
        "/api/v1/ws/notifications",
        subprotocols=["bearer", issued.access_token],
    ) as ws:
        assert ws is not None


@pytest.mark.asyncio
async def test_ws_origin_gate_missing_origin_passes():
    """Non-browser clients (no Origin header — Android, integrations) are
    not cross-site pages; §9 keeps their channel open."""
    user = await create_user()
    issued = await sign_in(user)
    client = _ws_client()
    with client.websocket_connect(
        "/api/v1/ws/notifications",
        headers={"cookie": f"nx_access={issued.access_token}"},
    ) as ws:
        assert ws is not None
