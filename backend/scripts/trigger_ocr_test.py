import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.document_model import DocumentModel


async def check():
    engine = create_async_engine(settings.HA_DATABASE_URL)
    LocalSession = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with LocalSession() as db:
        result = await db.execute(
            select(DocumentModel).order_by(DocumentModel.created_at.desc()).limit(3)
        )
        docs = result.scalars().all()
        for doc in docs:
            print(f"Doc {doc.filename}: Status={doc.status}, Progress={doc.progress}%")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check())
