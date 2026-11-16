"""Unpack a downloaded seed-pack ZIP into backend/data/seeds/ (with backup).

The companion to ``scripts/export_seeds.py`` / ``scripts/seed_pack.py pack``
and the UI's "Download seeds" button. A maintainer downloads the canonical
pack ZIP (``manifest.json`` + ``items/*.json``, ADR 0056) from a running
instance, transfers it to their dev machine, and runs:

    python scripts/unpack_seeds_zip.py path/to/health-assistant-seed-pack.zip

Safety pipeline (mirrors SeedExportService.write_all):
- copies each existing data/seeds/*.json to .backup-<timestamp>/
- a **pack ZIP** is verified first: every ``items/*`` entry must match the
  sha256 pin in ``manifest.json`` (a tampered/truncated archive never lands)
- a flat ZIP of seed files is also accepted (a hand-rolled archive), with
  the same registered-filenames whitelist
- extracts the item files into data/seeds/ (the ``items/`` prefix is
  stripped — data/seeds is the flat seeds tree) and reports what changed

Then review with `git diff data/seeds/` before committing.
"""

import argparse
import json
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.seed_pack import BUNDLED_ITEM_FILES, MANIFEST_NAME

DATA_SEEDS = Path(__file__).parent.parent / "data" / "seeds"
MANIFEST_ENTRY = MANIFEST_NAME


def _entry_name(name: str) -> str | None:
    """The flat seed filename for an archive entry (``None`` = not a seed
    file). Pack entries carry the ``items/`` prefix; directories are skipped
    and anything else outside the registered whitelist is refused."""
    if name.endswith("/"):
        return None
    flat = name[len("items/") :] if name.startswith("items/") else name
    if flat != name and (flat.startswith("/") or "/" in flat):
        return None
    return flat if flat in BUNDLED_ITEM_FILES else None


def _read_archive(zip_path: Path) -> dict[str, bytes]:
    """The archive's seed files, verified. A pack ZIP checks every sha256 pin
    in the manifest; a flat ZIP is accepted whitelist-only. Unknown entries,
    path traversal and digest mismatches are loud refusals."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if MANIFEST_ENTRY in names:
            manifest = json.loads(zf.read(MANIFEST_ENTRY))
            pins = {
                entry["path"]: entry["sha256"]
                for entry in manifest.get("item_files", [])
                if isinstance(entry, dict)
            }
        else:
            pins = None
        out: dict[str, bytes] = {}
        import hashlib

        for name in names:
            flat = _entry_name(name)
            if flat is None:
                if name == MANIFEST_ENTRY or name.endswith("/"):
                    continue
                print(
                    f"ZIP contains unexpected entries (registered seed files only): {name}",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            content = zf.read(name)
            if pins is not None:
                pin = pins.get(f"items/{flat}", pins.get(flat))
                if pin is None:
                    print(
                        f"ZIP entry {name} is not pinned by {MANIFEST_ENTRY} — refusing",
                        file=sys.stderr,
                    )
                    raise SystemExit(1)
                if hashlib.sha256(content).hexdigest() != pin:
                    print(
                        f"ZIP entry {name} does not match its manifest sha256 pin — refusing",
                        file=sys.stderr,
                    )
                    raise SystemExit(1)
            out[flat] = content
        if pins is not None:
            missing = sorted(set(pins) - {f"items/{flat}" for flat in out})
            if missing:
                print(
                    f"ZIP is missing manifest-pinned files: {missing} — refusing",
                    file=sys.stderr,
                )
                raise SystemExit(1)
        if not out:
            print("ZIP holds no seed files", file=sys.stderr)
            raise SystemExit(1)
        return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unpack a seed-pack ZIP into backend/data/seeds/ (with backup)."
    )
    parser.add_argument("zip", type=Path, help="Path to the downloaded seed-pack ZIP.")
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

    files = _read_archive(args.zip)
    args.out.mkdir(parents=True, exist_ok=True)

    backup_dir = None
    if not args.no_backup:
        backup_dir = args.out / f".backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            src = args.out / name
            if src.exists():
                shutil.copy2(src, backup_dir / name)

    for name, content in sorted(files.items()):
        (args.out / name).write_bytes(content)

    print(f"Unpacked {len(files)} seed files into {args.out}")
    if backup_dir:
        print(f"Backup of previous files: {backup_dir}")
    print("Review with: git diff data/seeds/")


if __name__ == "__main__":
    main()
