"""One-off recovery: re-link observations whose ``biomarker_id`` points at a
biomarker in a *different* tenant (the cross-tenant mapping bug fixed in
``map_observations_to_biomarkers``). Such rows 404 on the tenant-scoped
biomarker details page ("Biomarker not found") even though the data exists.

Idempotent. Run from ``backend/`` with the dev DB configured:

    PYTHONPATH=.:.. python scripts/remap_cross_tenant_biomarkers.py [--dry-run]

What it does, per affected observation:
  1. clears the bad ``biomarker_id`` (the cross-tenant link);
  2. calls ``map_observations_to_biomarkers`` (now tenant-scoped) so the obs
     links to a biomarker in its own tenant (matching by code/name/slug) or
     auto-creates one there.

Reports the counts. ``--dry-run`` lists what would change without writing.
"""
import argparse
import asyncio
import logging
import sys

from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.patient import Observation
from app.services.fhir_service import map_observations_to_biomarkers

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("remap_cross_tenant")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="list only; don't write")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        # Observations whose biomarker lives in a different tenant (and isn't global).
        rows = (
            await db.execute(
                text(
                    """
                    SELECT o.id AS obs_id, o.tenant_id AS obs_tenant, b.tenant_id AS bio_tenant
                    FROM fhir_observations o
                    JOIN biomarker_definitions b ON b.id = o.biomarker_id
                    WHERE o.biomarker_id IS NOT NULL
                      AND b.tenant_id IS NOT NULL
                      AND b.tenant_id <> o.tenant_id
                    """
                )
            )
        ).all()

        logger.info("Found %d cross-tenant observation→biomarker link(s).", len(rows))
        if not rows:
            return 0
        for r in rows:
            logger.info(
                "  obs %s (tenant %s) -> biomarker tenant %s", r.obs_id, r.obs_tenant, r.bio_tenant
            )

        if args.dry_run:
            logger.info("Dry run — no changes made.")
            return 0

        # Clear the bad links, then re-resolve via the (now tenant-scoped) waterfall.
        # Batch to stay under asyncpg's 32767 parameter limit (the IN (...) clause).
        BATCH = 2000
        obs_ids = [r.obs_id for r in rows]
        total_relinked = 0
        for i in range(0, len(obs_ids), BATCH):
            batch = obs_ids[i : i + BATCH]
            result = await db.execute(
                select(Observation).where(Observation.id.in_(batch))
            )
            observations = result.scalars().all()
            for o in observations:
                o.biomarker_id = None
            await db.flush()
            await map_observations_to_biomarkers(db, observations)
            await db.commit()
            total_relinked += len(observations)
            logger.info(
                "  re-mapped %d/%d (%d%%)", total_relinked, len(obs_ids), int(100 * total_relinked / len(obs_ids))
            )
        logger.info("Re-mapped %d observation(s) total.", total_relinked)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
