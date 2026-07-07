"""Export the running instance's taxonomy/anatomy/catalog data into seed files.

The inverse of SeedService: serializes DB rows into the slug-keyed
data/seeds/*.json format so a curator can build the canonical set via the UI +
AI and snapshot it into the shipped seeds. See
dev/plans/seed-export-service-2026-07-07.md.

Usage (run from backend/ with the venv active, PYTHONPATH covering the repo):

    python scripts/export_seeds.py                     # global -> data/seeds
    python scripts/export_seeds.py --dry-run           # preview only, no writes
    python scripts/export_seeds.py --source TENANT_ID  # a template tenant
    python scripts/export_seeds.py --out /tmp/myseeds  # custom output dir
    python scripts/export_seeds.py --no-backup         # skip the .backup-* dir

The write is safe: files stage to .export-staging/, existing files back up to
.backup-<timestamp>/, then atomic write. Review with `git diff data/seeds/`.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.services.seed_export_service import SeedExportService


def _parse_source(raw: str):
    if raw == "global":
        return None
    try:
        return UUID(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--source must be 'global' or a UUID tenant id, got: {raw!r}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the instance's data into data/seeds/*.json."
    )
    parser.add_argument(
        "--source",
        type=_parse_source,
        default=None,
        help="'global' (default; tenant_id IS NULL rows) or a tenant UUID to "
        "treat as a template.",
    )
    default_out = Path(__file__).parent.parent / "data" / "seeds"
    parser.add_argument(
        "--out",
        type=Path,
        default=default_out,
        help=f"Output directory (default: {default_out}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview counts only; write nothing.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backing up existing files to .backup-<timestamp>/.",
    )
    args = parser.parse_args()

    if not DATABASE_AVAILABLE:
        print("Database is not available.")
        sys.exit(1)

    # --source global -> None (export global taxonomy)
    tenant_id = None if args.source is None or args.source == "global" else args.source

    async with AsyncSessionLocal() as session:
        svc = SeedExportService(session, tenant_id=tenant_id)

        if args.dry_run:
            payloads = await svc.export_all()
            print(f"Dry run — would write {len(payloads)} files to {args.out}:")
            for filename, payload in sorted(payloads.items()):
                items = payload.get("items", payload.get("biomarkers", []))
                count = len(items) if isinstance(items, list) else 0
                print(f"  {filename:28} {count:>5} items")
            return

        report = await svc.write_all(args.out, backup=not args.no_backup)

    print(f"Wrote seed files to {args.out}:")
    for filename, info in sorted(report["files"].items()):
        print(f"  {filename:28} {info['count']:>5} items  ({info['bytes']} bytes)")
    if report.get("backup_dir"):
        print(f"Backup of previous files: {report['backup_dir']}")
    print("Review with: git diff data/seeds/")


if __name__ == "__main__":
    asyncio.run(main())
