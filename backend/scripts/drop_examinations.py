from app.core.database import engine
from sqlalchemy import text
import asyncio

async def main():
    async with engine.connect() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS examinations CASCADE"))
        await conn.commit()
        print("Dropped examinations table")

if __name__ == "__main__":
    asyncio.run(main())
