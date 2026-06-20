import asyncio
from app.core.database import engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, update
from app.models.fhir.patient import Observation
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from datetime import datetime

async def main():
    LocalSession = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with LocalSession() as db:
        # Get all observations with document IDs
        obs_query = await db.execute(
            select(Observation).where(Observation.document_id.isnot(None))
        )
        observations = obs_query.scalars().all()
        
        updated_count = 0
        for obs in observations:
            # Find document and examination
            doc_query = await db.execute(
                select(DocumentModel).where(DocumentModel.id == obs.document_id)
            )
            doc = doc_query.scalar_one_or_none()
            if doc and doc.examination_id:
                exam_query = await db.execute(
                    select(ExaminationModel).where(ExaminationModel.id == doc.examination_id)
                )
                exam = exam_query.scalar_one_or_none()
                if exam and exam.examination_date:
                    # Update observation date
                    from datetime import timezone
                    new_date = datetime.combine(exam.examination_date, datetime.min.time(), tzinfo=timezone.utc)
                    obs.effective_datetime = new_date
                    updated_count += 1
        
        await db.commit()
        print(f"Updated {updated_count} observations to use their examination dates!")

if __name__ == "__main__":
    asyncio.run(main())
