import asyncio
from typing import Any, cast
from app.core.database import AsyncSessionLocal
from app.models.document_model import DocumentModel
from app.workers.ai_tasks import process_document
from sqlalchemy import select


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DocumentModel).where(
                DocumentModel.status.in_(["uploaded", "failed"])
            )
        )
        docs = result.scalars().all()
        for doc in docs:
            print(f"Reprocessing {doc.filename} ({doc.id})")
            cast(Any, process_document).apply_async(
                args=[str(doc.id), str(doc.file_path), str(doc.tenant_id)]
            )


if __name__ == "__main__":
    asyncio.run(main())
