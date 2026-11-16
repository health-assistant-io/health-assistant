"""Seed CLI: run the boot seed pipeline, or a hard reseed of the curated
system catalogs.

    python scripts/seed.py                  # seed_all() (idempotent upserts)
    python scripts/seed.py --reset-curated # delete curated system rows, then reseed

``--reset-curated`` is the deterministic re-identity path (ADR 0055): it
deletes ONLY curated system rows (``tenant_id IS NULL``; seed-provenance
edges) and reseeds with the ``uuid5`` ids derived from each item's
``seed_key``. Tenant rows are never touched. Safe because there are no
users — an existing pre-identity install should run this once so legacy
random-id rows are replaced. On an install with live references into the
curated rows (tenant or instance rows still linked to them, including unit
pins) the reset refuses with ``CuratedResetBlocked`` before deleting
anything.
"""

import argparse
import asyncio
import json
import os
import sys

# Ensure backend path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset-curated",
        action="store_true",
        help="delete curated SYSTEM rows for the seeded catalogs, then reseed "
        "(tenant rows untouched; idempotent)",
    )
    args = parser.parse_args()

    from app.core.database import DATABASE_AVAILABLE

    if not DATABASE_AVAILABLE:
        print("Error: Database is not available.")
        sys.exit(1)

    if args.reset_curated:
        from app.services.seed.reset import reset_curated_system_rows

        report = await reset_curated_system_rows()
        print("Hard reseed complete.")
        print("Deleted system rows:", json.dumps(report["deleted"], indent=2))
        print("Seeded stages:")
        for stage, stats in report["seeded"].items():
            print(f"  {stage}: {json.dumps(stats)}")
    else:
        from app.services.seed.service import SeedService

        result = await SeedService().seed_all()
        print("Seed complete.")
        for stage, stats in result.items():
            print(f"  {stage}: {json.dumps(stats)}")


if __name__ == "__main__":
    asyncio.run(main())
