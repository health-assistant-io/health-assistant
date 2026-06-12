import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings
from app.models.document_model import DocumentModel
from sqlalchemy import select

async def check():
    engine = create_async_engine(settings.DATABASE_URL)
    LocalSession = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    async with LocalSession() as db:
        result = await db.execute(select(DocumentModel).order_by(DocumentModel.created_at.desc()).limit(3))
        docs = result.scalars().all()
        for doc in docs:
            print(f"Doc {doc.filename}: Status={doc.status}, Progress={doc.progress}%")
            
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check())
