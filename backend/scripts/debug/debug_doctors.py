import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.doctor_model import DoctorModel
from uuid import UUID


async def debug_doctors_list():
    tenant_id = UUID("27302caf-713b-4788-ae3d-f49c2b6e9876")
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DoctorModel)
                .where(DoctorModel.tenant_id == tenant_id)
                .order_by(DoctorModel.name)
            )
            doctors = result.scalars().unique().all()
            print(f"Found {len(doctors)} doctors.")
            for d in doctors:
                data = d.to_dict()
                print(f"Serialized Doctor: {data}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(debug_doctors_list())
