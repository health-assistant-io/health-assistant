import asyncio
import sys
import os
import json
from pathlib import Path

# Ensure backend path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.catalog_import_service import CatalogImportService
from app.schemas.biomarker import CatalogImportPayload
from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE

async def main():
    if not DATABASE_AVAILABLE:
        print("❌ Error: Database is not available.")
        sys.exit(1)

    catalog_path = Path(__file__).parent.parent / "data" / "seeds" / "default_catalog.json"
    
    if not catalog_path.exists():
        print(f"❌ Error: Default catalog not found at {catalog_path}")
        sys.exit(1)

    print("🚀 Starting default catalog import...")
    
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        payload = CatalogImportPayload.model_validate(data)
        
        async with AsyncSessionLocal() as session:
            import_service = CatalogImportService(session)
            stats = await import_service.import_catalog(payload)
            
            print("-" * 30)
            print(f"✅ Catalog Sync complete!")
            print(f"🧬 Biomarkers Added:   {stats.get('biomarkers_added', 0)}")
            print(f"🧬 Biomarkers Updated: {stats.get('biomarkers_updated', 0)}")
            print(f"📏 Units Added:        {stats.get('units_added', 0)}")
            print(f"📏 Units Updated:      {stats.get('units_updated', 0)}")
            print("-" * 30)

    except Exception as e:
        print(f"❌ Error importing catalog: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())