import asyncio
from sqlalchemy import select
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.task_log import TaskLog
from app.workers.tasks import get_async_session


async def check():
    db, engine = get_async_session()
    async with db:
        print("\n=== RECENT EXAMINATIONS ===")
        res_exam = await db.execute(
            select(ExaminationModel)
            .order_by(ExaminationModel.created_at.desc())
            .limit(10)
        )
        exams = res_exam.scalars().all()
        for e in exams:
            print(
                f"Exam: {e.id} | Status: {e.extraction_status} | Progress: {e.extraction_progress}% | Auto: {e.auto_extract_metadata}"
            )
            if e.error_message:
                print(f"  ❌ Error: {e.error_message}")

            # Find documents for this exam
            res_docs = await db.execute(
                select(DocumentModel).where(DocumentModel.examination_id == e.id)
            )
            docs = res_docs.scalars().all()
            for d in docs:
                print(
                    f"  - Doc: {d.id} | Status: {d.status} | Include: {d.include_in_extraction}"
                )
                if d.error_message:
                    print(f"    ❌ Doc Error: {d.error_message}")

        print("\n=== LATEST TASK LOGS ===")
        res_logs = await db.execute(
            select(TaskLog).order_by(TaskLog.created_at.desc()).limit(15)
        )
        logs = res_logs.scalars().all()
        for log in logs:
            print(f"[{log.level}] {log.task_name} ({log.resource_id}): {log.message}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check())
