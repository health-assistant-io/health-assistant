"""Unpack a downloaded seeds ZIP into backend/data/seeds/ (with backup).

The companion to ``scripts/export_seeds.py`` and the UI's "Download seeds"
button. A maintainer downloads ``health-assistant-seeds.zip`` from a running
instance, transfers it to their dev machine, and runs:

    python scripts/unpack_seeds_zip.py path/to/health-assistant-seeds.zip

Safety pipeline (mirrors SeedExportService.write_all):
- copies each existing data/seeds/*.json to .backup-<timestamp>/
- extracts the ZIP's flat files into data/seeds/
- reports what changed

Then review with `git diff data/seeds/` before committing.
"""

import argparse
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

DATA_SEEDS = Path(__file__).parent.parent / "data" / "seeds"
SEED_FILENAMES = {
    "concepts.json",
    "concept_edges.json",
    "anatomy_structures.json",
    "anatomy_relations.json",
    "default_catalog.json",
    "biomarker_panels.json",
    "clinical_event_types.json",
    "medications.json",
    "allergies.json",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unpack a seeds ZIP into backend/data/seeds/ (with backup)."
    )
    parser.add_argument("zip", type=Path, help="Path to the downloaded seeds ZIP.")
    parser.add_argument(
        "--out",
        type=Path,
        default=DATA_SEEDS,
        help=f"Target directory (default: {DATA_SEEDS}).",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backing up existing files to .backup-<timestamp>/.",
    )
    args = parser.parse_args()

    if not args.zip.exists():
        print(f"ZIP not found: {args.zip}", file=sys.stderr)
        sys.exit(1)

    args.out.mkdir(parents=True, exist_ok=True)

    # Validate the ZIP contains only known seed files (defensive — a curdled
    # or hostile archive shouldn't write arbitrary files into data/seeds/).
    with zipfile.ZipFile(args.zip) as zf:
        names = set(zf.namelist())
    unknown = names - SEED_FILENAMES
    if unknown:
        print(
            f"ZIP contains unexpected entries (expected only seed files): {unknown}",
            file=sys.stderr,
        )
        sys.exit(1)

    backup_dir = None
    if not args.no_backup:
        backup_dir = args.out / f".backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name in SEED_FILENAMES & names:
            src = args.out / name
            if src.exists():
                shutil.copy2(src, backup_dir / name)

    with zipfile.ZipFile(args.zip) as zf:
        zf.extractall(args.out)

    print(f"Unpacked {len(names)} seed files into {args.out}")
    if backup_dir:
        print(f"Backup of previous files: {backup_dir}")
    print("Review with: git diff data/seeds/")


if __name__ == "__main__":
    main()
