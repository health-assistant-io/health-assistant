import asyncio
from app.workers.ai_tasks import cumulative_extraction, ocr_document
from sqlalchemy import select
from app.models.document_model import DocumentModel
from app.workers.tasks import AsyncSessionFactory
from uuid import UUID


async def trigger():
    exam_id = "072581a0-8e01-47c6-b15d-7431bf29409e"
    print(f"Manually triggering cumulative_extraction for {exam_id}")
    task = cumulative_extraction.delay(exam_id)
    print(f"Task ID: {task.id}")


if __name__ == "__main__":
    asyncio.run(trigger())
