import redis.asyncio as redis
from app.core.config import settings

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


async def publish_message(channel: str, message: str):
    await redis_client.publish(channel, message)
