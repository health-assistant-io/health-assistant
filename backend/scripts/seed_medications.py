import asyncio
import sys
import os

# Ensure backend path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.seed_service import seed_service
from app.core.database import DATABASE_AVAILABLE


async def main():
    if not DATABASE_AVAILABLE:
        print("❌ Error: Database is not available.")
        return

    print("🚀 Starting manual medication catalog sync...")
    stats = await seed_service.seed_medications()

    print("-" * 30)
    print(f"✅ Sync complete!")
    print(f"➕ Added:   {stats['added']}")
    print(f"🔄 Updated: {stats['updated']}")
    print(f"❌ Errors:  {stats['errors']}")
    print("-" * 30)


if __name__ == "__main__":
    asyncio.run(main())
