import asyncio
from sqlalchemy import text
from app.core.database import AsyncSessionLocal


async def run():
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        )
        print("Tables in public schema:")
        for row in res:
            print(f"- {row[0]}")


if __name__ == "__main__":
    asyncio.run(run())
