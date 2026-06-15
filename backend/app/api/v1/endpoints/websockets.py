from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from app.core.security import get_current_user_ws
from app.core.redis import redis_client
from app.schemas.user import TokenData
import asyncio
import json

router = APIRouter(prefix="/ws", tags=["WebSockets"])

@router.websocket("/tasks")
async def websocket_tasks_endpoint(
    websocket: WebSocket,
    token: str = Query(...)
):
    try:
        current_user = await get_current_user_ws(token)
    except Exception:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    tenant_id = current_user.tenant_id

    pubsub = redis_client.pubsub()
    channel = f"tenant:{tenant_id}:tasks"
    await pubsub.subscribe(channel)

    try:
        while True:
            # Poll for new messages from redis pubsub
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                data = message["data"]
                await websocket.send_text(data)
            
            # Send a ping to keep connection alive
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        await pubsub.unsubscribe(channel)
    except Exception as e:
        await pubsub.unsubscribe(channel)
        await websocket.close(code=1011)

