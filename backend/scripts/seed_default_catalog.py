import asyncio
import os
import sys

# Ensure backend path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.services.seed.service import SeedService


async def main():
    if not DATABASE_AVAILABLE:
        print("❌ Error: Database is not available.")
        sys.exit(1)

    print("🚀 Starting default catalog seed (identity upsert)...")

    # The seeder never owns the transaction (ADR 0056): one session, one commit.
    async with AsyncSessionLocal() as session:
        stats = await SeedService().seed_default_catalog(session)
        await session.commit()
    details = stats.get("details", {})

    print("-" * 30)
    print("✅ Catalog sync complete!")
    print(f"🧬 Biomarkers Added:     {details.get('biomarkers_added', 0)}")
    print(f"🧬 Biomarkers Updated:   {details.get('biomarkers_updated', 0)}")
    print(f"🧬 Biomarkers Unchanged: {details.get('biomarkers_unchanged', 0)}")
    print(f"📏 Units Added:          {details.get('units_added', 0)}")
    print(f"📏 Units Updated:        {details.get('units_updated', 0)}")
    print(f"📏 Units Unchanged:      {details.get('units_unchanged', 0)}")
    print(f"❌ Errors:               {stats.get('errors', 0)}")
    print("-" * 30)

    if stats.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
