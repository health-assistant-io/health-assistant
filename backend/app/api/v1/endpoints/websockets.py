"""WebSocket endpoint for live task-progress updates.

Connection hygiene:

1. **Auth token via subprotocol** (``new WebSocket(url, ["bearer", token])``)
   which is not logged as part of the URL. A query-string ``?token=...``
   fallback is retained for backward compatibility with existing clients.

2. **Bounded polling cadence** via ``pubsub.get_message(timeout=1.0)`` with
   explicit event-loop yields, keeping Redis round-trips low.

3. **Errors logged before close(1011)** so operators can diagnose drops.

A lightweight server-side ping (every 30s) keeps intermediaries from
timing the connection out.
"""

from datetime import datetime, timezone
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.core.security import get_current_user_ws
from app.core.redis import redis_client
from app.schemas.user import TokenData

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSockets"])

# How long to block on Redis per poll. This single value sets the effective
# polling cadence (no additional sleep needed).
_POLL_TIMEOUT_SECONDS = 1.0
# Server-side keepalive ping interval. intermediaries (nginx, load balancers)
# typically drop idle connections after 60-120s; a 30s ping stays well inside.
_PING_INTERVAL_SECONDS = 30


async def _extract_token(websocket: WebSocket, query_token: str | None) -> str | None:
    """B11: prefer the Sec-WebSocket-Protocol subprotocol over the URL query
    string so the token is not logged by reverse proxies or browser history.

    Clients connect with ``new WebSocket(url, ["bearer", "<token>"])``. We
    accept the token that follows the ``bearer`` sentinel. Subprotocols are
    read from the ASGI ``scope["subprotocols"]`` list (the canonical location
    uvicorn populates) and, as a fallback, from the raw
    ``Sec-WebSocket-Protocol`` header. If neither yields a token, fall back to
    the legacy ``?token=`` query parameter.
    """
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
    return query_token


@router.websocket("/tasks")
async def websocket_tasks_endpoint(
    websocket: WebSocket,
    token: str = Query(default=None),
):
    """Live task-progress stream for the caller's tenant.

    Auth: prefer Sec-WebSocket-Protocol subprotocol (``["bearer", token]``);
    fall back to ``?token=`` for legacy clients (B11).
    """
    resolved_token = await _extract_token(websocket, token)
    if not resolved_token:
        # No token from either source — reject before accepting the socket.
        await websocket.close(code=1008)
        return

    try:
        current_user: TokenData = await get_current_user_ws(resolved_token)
    except Exception as e:
        logger.info("WebSocket auth rejected: %s", e)
        await websocket.close(code=1008)
        return

    # If a subprotocol was requested, echo it back so the client knows we
    # honoured it; otherwise accept without one.
    subprotocols = websocket.headers.get("sec-websocket-protocol", "")
    negotiated = None
    if subprotocols:
        parts = [p.strip() for p in subprotocols.split(",") if p.strip()]
        if parts:
            negotiated = parts[0]
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
    token: str = Query(default=None),
):
    """Live per-user notification stream.

    Subscribes to the Redis channel ``user:{user_id}:notifications`` so each
    authenticated user receives their own fan-out (notifications are targeted
    at concrete user ids by ``notification_service.emit``). Auth + connection
    hygiene mirror ``/ws/tasks`` (subprotocol-preferred token).
    """
    resolved_token = await _extract_token(websocket, token)
    print(
        f"[WS-DEBUG] /ws/notifications resolved_token={'<set>' if resolved_token else '<None>'}",
        flush=True,
    )
    if not resolved_token:
        await websocket.close(code=1008)
        return

    try:
        current_user: TokenData = await get_current_user_ws(resolved_token)
        print(f"[WS-DEBUG] auth OK user={current_user.user_id}", flush=True)
    except Exception as e:
        print(f"[WS-DEBUG] auth RAISED: {type(e).__name__}: {e}", flush=True)
        logger.info("Notification WebSocket auth rejected: %s", e)
        await websocket.close(code=1008)
        return

    subprotocols = websocket.headers.get("sec-websocket-protocol", "")
    negotiated = None
    if subprotocols:
        parts = [p.strip() for p in subprotocols.split(",") if p.strip()]
        if parts:
            negotiated = parts[0]
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
