import redis.asyncio as redis

from app.core.config import settings

redis_client = redis.from_url(
    settings.REDIS_URL or "redis://127.0.0.1:6379/0", decode_responses=True
)


async def publish_message(channel: str, message: str):
    await redis_client.publish(channel, message)
