import asyncio
from httpx import AsyncClient

async def run():
    async with AsyncClient() as client:
        pass # To properly test this I'd need the app running, but I can restart the backend and verify the route
