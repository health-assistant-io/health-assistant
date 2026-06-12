import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.doctor_model import DoctorModel


async def test_doctors():
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(DoctorModel))
            doctors = result.scalars().all()
            print(f"Found {len(doctors)} doctors.")
            for d in doctors:
                print(f"Doctor: {d.name}, Tenant: {d.tenant_id}")
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(test_doctors())
