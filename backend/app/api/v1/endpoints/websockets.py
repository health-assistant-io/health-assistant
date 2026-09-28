"""WebSocket endpoint for live task-progress updates.

Connection hygiene:

1. **Auth (§10, plan 16 H3):** the browser's access JWT is read from the
   HttpOnly ``nx_access`` cookie; the ``Sec-WebSocket-Protocol``
   subprotocol form (``new WebSocket(url, ["bearer", token])``) stays for
   non-browser clients (§9 — the Android app may use it; both channels
   are fine). A query-string ``?token=...`` remains forbidden (proxy
   logs / browser history).

2. **Origin gate (§10):** browser handshakes carry ``Origin`` — it is
   verified against the configured allow-list (``HA_WS_ALLOWED_ORIGINS``
   or same-origin + APP_URL/FRONTEND_URL + the dev LAN regex); anything
   else is rejected with 1008 before the socket is accepted.
   Non-browser clients (no ``Origin`` header) pass — they are not
   cross-site pages, and §9 freezes their behavior.

3. **Bounded polling cadence** via ``pubsub.get_message(timeout=1.0)`` with
   explicit event-loop yields, keeping Redis round-trips low.

4. **Errors logged before close(1011)** so operators can diagnose drops.

A lightweight server-side ping (every 30s) keeps intermediaries from
timing the connection out.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import DEV_LAN_ORIGIN_REGEX, settings
from app.core.cookies import access_cookie_candidates, parse_cookies
from app.core.redis import redis_client
from app.core.security import get_session_user_ws
from app.schemas.user import TokenData

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSockets"])

# How long to block on Redis per poll. This single value sets the effective
# polling cadence (no additional sleep needed).
_POLL_TIMEOUT_SECONDS = 1.0
# Server-side keepalive ping interval. intermediaries (nginx, load balancers)
# typically drop idle connections after 60-120s; a 30s ping stays well inside.
_PING_INTERVAL_SECONDS = 30

# Compiled once — same pattern the dev CORS middleware allows.
_DEV_LAN_ORIGIN = re.compile(DEV_LAN_ORIGIN_REGEX)


def _ws_allowed_origins() -> set[str]:
    """The configured WS Origin allow-list (§10).

    ``HA_WS_ALLOWED_ORIGINS`` (comma-separated) wins when set; the default
    is the CORS list — APP_URL + FRONTEND_URL origins. Same-origin (the
    request's own Host) and the dev LAN regex are checked separately in
    :func:`_origin_allowed` so the common SPA-served-by-backend and dev
    setups work with zero configuration.
    """
    raw = settings.HA_WS_ALLOWED_ORIGINS
    if raw:
        return {origin.strip() for origin in raw.split(",") if origin.strip()}
    origins: set[str] = set()
    for url in (settings.APP_URL, settings.FRONTEND_URL):
        try:
            parsed = urlparse(url or "")
            if parsed.scheme and parsed.netloc:
                origins.add(f"{parsed.scheme}://{parsed.netloc}")
        except Exception:
            continue
    return origins


def _origin_allowed(websocket: WebSocket) -> bool:
    """§10: verify the handshake ``Origin`` against the allow-list.

    A missing ``Origin`` (non-browser client — Android, integrations,
    curl) passes: it is not a cross-site page and §9 freezes its
    behavior. A present-but-unlisted origin (including ``null``) is
    rejected — that is the cross-site WebSocket hijacking vector the
    cookie channel would otherwise open.
    """
    origin = (websocket.headers.get("origin") or "").strip()
    if not origin:
        return True
    if origin in _ws_allowed_origins():
        return True
    # Same-origin: the SPA is served by the backend itself (or proxied),
    # so the handshake origin equals ws(s)://<request host>.
    host = (websocket.headers.get("host") or "").strip()
    if host:
        scheme = websocket.url.scheme or "ws"
        http_scheme = "https" if scheme in ("wss", "https") else "http"
        if origin.lower() == f"{http_scheme}://{host}".lower():
            return True
    if settings.APP_ENV == "development" and _DEV_LAN_ORIGIN.match(origin):
        return True
    return False


async def _extract_token(websocket: WebSocket) -> str | None:
    """§10: the HttpOnly ``nx_access`` cookie is the browser auth channel;
    the ``Sec-WebSocket-Protocol`` subprotocol is the non-browser one.

    Cookie first (read from the raw ``Cookie`` header); the subprotocol
    form ``["bearer", "<token>"]`` stays for §9 clients (the Android app
    may use it). Subprotocols are read from the ASGI
    ``scope["subprotocols"]`` list (the canonical location uvicorn
    populates) and, as a fallback, from the raw
    ``Sec-WebSocket-Protocol`` header.
    """
    # 0. §10 cookie channel (browsers).
    cookies = parse_cookies(websocket.headers.get("cookie"))
    for name in access_cookie_candidates():
        token = cookies.get(name)
        if token:
            return token

    # 1. ASGI scope subprotocols (canonical; uvicorn/Starlette populate this).
    scope_subs = websocket.scope.get("subprotocols") or []
    # 2. Raw header (comma-joined if multiple values).
    header_subs = websocket.headers.get("sec-websocket-protocol", "")
    parts: list[str] = list(scope_subs)
    if header_subs:
        parts.extend([p.strip() for p in header_subs.split(",") if p.strip()])

    logger.debug(
        "WS auth: scope_subprotocols=%r header=%r", scope_subs, header_subs[:40]
    )

    for i, part in enumerate(parts):
        if part.lower() == "bearer" and i + 1 < len(parts):
            return parts[i + 1]
    # Some clients send the raw token directly as the (only) subprotocol.
    for part in parts:
        if part.lower() != "bearer" and part.count(".") >= 2:
            return part
    # Audit 2026-08 AUTH-L4: the ?token= query-string fallback is REMOVED —
    # query strings land in reverse-proxy access logs and browser history.
    # Browsers use the cookie (§10); other clients the subprotocol form
    # (new WebSocket(url, ["bearer", token])).
    return None


async def _authenticate_handshake(websocket: WebSocket) -> TokenData | None:
    """Origin gate + token verification for a WS handshake (§10).

    Returns the authenticated ``TokenData``, or ``None`` after closing
    the socket with 1008 (policy violation) — callers return early.
    """
    if not _origin_allowed(websocket):
        logger.info(
            "WebSocket handshake rejected: Origin %r not allowed",
            websocket.headers.get("origin"),
        )
        await websocket.close(code=1008)
        return None

    resolved_token = await _extract_token(websocket)
    if not resolved_token:
        # No token from any source — reject before accepting the socket.
        await websocket.close(code=1008)
        return None

    try:
        return await get_session_user_ws(resolved_token)
    except Exception as e:
        logger.info("WebSocket auth rejected: %s", e)
        await websocket.close(code=1008)
        return None


def _negotiated_subprotocol(websocket: WebSocket) -> str | None:
    """Echo the first requested subprotocol so the client knows we
    honoured it; accept without one when none was requested."""
    subprotocols = websocket.headers.get("sec-websocket-protocol", "")
    if subprotocols:
        parts = [p.strip() for p in subprotocols.split(",") if p.strip()]
        if parts:
            return parts[0]
    return None


@router.websocket("/tasks")
async def websocket_tasks_endpoint(
    websocket: WebSocket,
):
    """Live task-progress stream for the caller's tenant.

    Auth (§10): the ``nx_access`` cookie (browsers) or the
    Sec-WebSocket-Protocol subprotocol ``["bearer", token]`` (§9 clients);
    the ``?token=`` query fallback stays removed (audit 2026-08 AUTH-L4).
    The handshake ``Origin`` is verified against the configured
    allow-list — unauthorized origins are rejected with 1008.
    """
    current_user = await _authenticate_handshake(websocket)
    if current_user is None:
        return

    negotiated = _negotiated_subprotocol(websocket)
    await websocket.accept(subprotocol=negotiated)

    tenant_id = current_user.tenant_id

    pubsub = redis_client.pubsub()
    channel = f"tenant:{tenant_id}:tasks"

    try:
        await pubsub.subscribe(channel)

        async def _read_loop():
            try:
                while True:
                    await websocket.receive()
            except WebSocketDisconnect:
                pass

        read_task = asyncio.create_task(_read_loop())
        last_ping = datetime.now(timezone.utc)

        while True:
            if read_task.done():
                break

            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_POLL_TIMEOUT_SECONDS
            )
            if message and message.get("type") == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                await websocket.send_text(data)

            now = datetime.now(timezone.utc)
            if (now - last_ping).total_seconds() >= _PING_INTERVAL_SECONDS:
                try:
                    await websocket.send_json({"type": "ping", "ts": now.isoformat()})
                except Exception:
                    break
                last_ping = now
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected (tenant=%s)", tenant_id)
    except asyncio.CancelledError:
        # Server shutdown / task cancellation — exit cleanly.
        raise
    except Exception as e:
        # B11: was previously a silent close(1011). Log so operators can
        # diagnose unexpected drops.
        logger.warning("WebSocket error for tenant=%s: %s", tenant_id, e, exc_info=True)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
        except Exception:
            pass


@router.websocket("/notifications")
async def websocket_notifications_endpoint(
    websocket: WebSocket,
):
    """Live per-user notification stream.

    Subscribes to the Redis channel ``user:{user_id}:notifications`` so each
    authenticated user receives their own fan-out (notifications are targeted
    at concrete user ids by ``notification_service.emit``). Auth + connection
    hygiene mirror ``/ws/tasks`` (cookie-or-subprotocol token, §10 Origin
    gate, subprotocol-preferred negotiation).
    """
    current_user = await _authenticate_handshake(websocket)
    if current_user is None:
        return

    negotiated = _negotiated_subprotocol(websocket)
    await websocket.accept(subprotocol=negotiated)

    user_id = current_user.user_id

    pubsub = redis_client.pubsub()
    channel = f"user:{user_id}:notifications"

    try:
        await pubsub.subscribe(channel)

        async def _read_loop():
            try:
                while True:
                    await websocket.receive()
            except WebSocketDisconnect:
                pass

        read_task = asyncio.create_task(_read_loop())
        last_ping = datetime.now(timezone.utc)

        while True:
            if read_task.done():
                break

            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_POLL_TIMEOUT_SECONDS
            )
            if message and message.get("type") == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                await websocket.send_text(data)

            now = datetime.now(timezone.utc)
            if (now - last_ping).total_seconds() >= _PING_INTERVAL_SECONDS:
                try:
                    await websocket.send_json({"type": "ping", "ts": now.isoformat()})
                except Exception:
                    break
                last_ping = now
    except WebSocketDisconnect:
        logger.debug("Notification WebSocket client disconnected (user=%s)", user_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "Notification WebSocket error for user=%s: %s", user_id, e, exc_info=True
        )
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
        except Exception:
            pass
