import asyncio
import uuid
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.fhir import Patient


async def check_patient():
    print("Listing all patients:")
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Patient))
        patients = result.scalars().all()
        for p in patients:
            print(f"ID: {p.id}, Tenant: {p.tenant_id}, Name: {p.name}")
        if not patients:
            print("No patients found in fhir_patients table.")


if __name__ == "__main__":
    asyncio.run(check_patient())
