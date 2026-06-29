import asyncio
from typing import Any, cast
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings
from app.models.document_model import DocumentModel
from app.workers.ai_tasks import process_document
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Recategorize")


async def main():
    engine = create_async_engine(settings.DATABASE_URL)
    LocalSession = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with LocalSession() as db:
        query = select(DocumentModel).where(DocumentModel.status == "completed")
        result = await db.execute(query)
        documents = result.scalars().all()

        count = 0
        for doc in documents:
            # We want to reprocess specifically the eye exam since it was completely missed
            if (
                "eyes" in doc.filename.lower()
                or "eye" in doc.filename.lower()
                or "uncategorized" in doc.filename.lower()
                or doc.entities.get("document_category") == "Uncategorized"
                or not doc.entities.get("document_category")
            ):
                try:
                    logger.info(f"Queueing document {doc.id} for full re-extraction")
                    cast(Any, process_document).apply_async(
                        args=[str(doc.id), str(doc.file_path), str(doc.tenant_id)]
                    )
                    count += 1

                except Exception as e:
                    logger.error(f"Failed to queue {doc.id}: {e}")

        logger.info(f"Queued {count} documents for re-extraction.")


if __name__ == "__main__":
    asyncio.run(main())
