import asyncio
from app.core.database import AsyncSessionLocal
from app.models.examination_model import ExaminationModel
from app.models.document_model import DocumentModel
from sqlalchemy import select


async def check():
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(ExaminationModel)
            .where(ExaminationModel.extraction_status != "completed")
            .order_by(ExaminationModel.updated_at.desc())
        )
        exams = res.scalars().all()
        print(f"Found {len(exams)} non-completed exams")
        for exam in exams:
            print(f"Exam ID: {exam.id}")
            print(
                f"  Status: {exam.extraction_status}, Progress: {exam.extraction_progress}"
            )
            print(f"  Error: {exam.error_message}")
            docs_res = await db.execute(
                select(DocumentModel).where(DocumentModel.examination_id == exam.id)
            )
            docs = docs_res.scalars().all()
            for d in docs:
                print(
                    f"    Doc: {d.filename}, Status: {d.status}, Include: {d.include_in_extraction}"
                )


if __name__ == "__main__":
    asyncio.run(check())
