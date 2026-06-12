import asyncio
from app.models.document_model import DocumentModel
from app.core.database import AsyncSessionLocal

async def run():
    async with AsyncSessionLocal() as session:
        # Fetch all
        docs = await session.run_sync(lambda s: s.query(DocumentModel).all())
        for d in docs:
            text = d.extracted_text
            text_len = len(text) if isinstance(text, str) else 0
            print(f"Doc: {d.id}, Status: {d.status}, Text: {text_len}")

if __name__ == "__main__":
    asyncio.run(run())
