from app.core.database import engine
from sqlalchemy import inspect
import asyncio

async def main():
    async with engine.connect() as conn:
        def get_cols(connection):
            inspector = inspect(connection)
            return [c["name"] for c in inspector.get_columns("examinations")]
        
        exam_cols = await conn.run_sync(get_cols)
        print("Examinations columns:", exam_cols)

if __name__ == "__main__":
    asyncio.run(main())
