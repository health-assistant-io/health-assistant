from app.core.database import engine
from sqlalchemy import text
import asyncio

async def main():
    async with engine.connect() as conn:
        await conn.execute(text("DELETE FROM examinations CASCADE"))
        await conn.execute(text("DELETE FROM fhir_observations CASCADE"))
        await conn.commit()
        print("Deleted examinations and observations")

if __name__ == "__main__":
    asyncio.run(main())
