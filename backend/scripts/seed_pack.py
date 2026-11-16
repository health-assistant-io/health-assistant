#!/usr/bin/env python3
"""Seed pack CLI — build, validate and digest packs (app-owned contract).

The pack format lives at ``contracts/seed-pack.schema.json`` with fixtures in
``contracts/seed-pack.fixtures.json`` (the pytest suite
``tests/test_seed_pack_schema.py`` pins both). This CLI:

* ``pack``      — build the bundled pack from ``backend/data/seeds`` into an
                  output directory (``manifest.json`` + ``items/*.json``, raw
                  byte copies so the manifest pins exact bytes) and print the
                  whole-pack digest;
* ``pack --check`` — verify a checked-in pack directory is **current**: the
                  pack is rebuilt in memory from the seeds tree and compared
                  byte-for-byte against the target; any drift (missing file,
                  differing bytes, manifest change) prints per-file findings
                  and exits non-zero (the CI drift gate);
* ``validate``  — validate an on-disk pack: a directory holding
                  ``manifest.json`` plus the item files it declares;
* ``digest``    — print a digest: for a file, the sha256 a manifest must pin;
                  for a pack directory, the whole-pack digest (the pack must
                  validate first).

Usage (from ``backend/``):

    uv run python scripts/seed_pack.py pack --out /tmp/bundled-pack
    uv run python scripts/seed_pack.py pack --out /tmp/bundled-pack --check
    uv run python scripts/seed_pack.py pack --check --against /path/to/pack
    uv run python scripts/seed_pack.py validate /path/to/pack
    uv run python scripts/seed_pack.py digest /path/to/pack
    uv run python scripts/seed_pack.py digest /path/to/items/medications.json

Exit codes: 0 valid/current, 1 invalid or drifted (findings printed),
2 usage/IO error.
Validation and path-safety rules live in :mod:`app.core.seed_pack`
(:func:`~app.core.seed_pack.load_pack_dir` refuses to read manifest paths
that are absolute, traversing or escaping symlinks; the bundled builder
refuses unregistered JSON files in the seeds directory).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.seed_pack import (
    MANIFEST_NAME,
    SeedPackInvalid,
    build_bundled_pack,
    content_digest,
    load_pack_dir,
    pack_digest,
    validate_pack,
    write_bundled_pack,
)


def cmd_pack(
    out_dir: Path | None, seeds_dir: Path | None, check: bool, against: Path | None
) -> int:
    manifest, files = build_bundled_pack(seeds_dir)
    errors = validate_pack(manifest, files)
    if errors:
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "seed pack: FAIL — the bundled content does not satisfy the contract", file=sys.stderr
        )
        return 1
    if check:
        target = against or out_dir
        if target is None:
            print(
                "seed pack: error — --check needs the pack to verify: --against DIR (or --out DIR)",
                file=sys.stderr,
            )
            return 2
        findings = _check_drift(target, manifest, files)
        if findings:
            for finding in findings:
                print(f"  - {finding}")
            print(f"seed pack: DRIFT — {len(findings)} difference(s) in {target}", file=sys.stderr)
            return 1
        digest = pack_digest(manifest)
        print(digest)
        print(f"seed pack: OK — {target} is current (digest {digest[:16]}…)", file=sys.stderr)
        return 0
    if out_dir is None:
        print("seed pack: error — pack needs --out DIR (or --check)", file=sys.stderr)
        return 2
    digest = write_bundled_pack(out_dir, seeds_dir)
    print(digest)
    print(
        f"seed pack: OK — bundled pack written to {out_dir} ({len(files)} item files)",
        file=sys.stderr,
    )
    return 0


def _check_drift(target: Path, manifest: dict, files: dict[str, bytes]) -> list[str]:
    """Compare a freshly built pack against a checked-in pack directory.

    Byte-for-byte on every item file, structural equality on the manifest
    (the whole-pack digest digests only the content-addressed part, so the
    manifest itself is compared fully — a description edit is a real diff a
    reviewer wants to see). Missing/unreadable targets are findings.
    """
    findings: list[str] = []
    manifest_path = target / MANIFEST_NAME
    if not manifest_path.is_file():
        findings.append(f"{MANIFEST_NAME} missing from {target} (not a pack directory)")
        return findings
    try:
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(f"{MANIFEST_NAME} unreadable: {exc}")
        return findings
    if current != manifest:
        current_digest = pack_digest(current) if isinstance(current, dict) else "?"
        findings.append(
            f"{MANIFEST_NAME} differs (built digest {pack_digest(manifest)[:16]}…, "
            f"checked-in {str(current_digest)[:16]}…)"
        )
    for path, content in sorted(files.items()):
        disk = target / path
        if not disk.is_file():
            findings.append(f"{path} missing from {target}")
            continue
        if content_digest(disk.read_bytes()) != content_digest(content):
            findings.append(f"{path} differs from the seeds tree")
    for stray in sorted(target.glob("items/*.json")):
        rel = f"items/{stray.name}"
        if rel not in files:
            findings.append(f"{rel} exists in {target} but is not a bundled item file")
    return findings


def cmd_validate(pack_dir: Path) -> int:
    manifest, files, load_errors = load_pack_dir(pack_dir)
    errors = [*load_errors, *validate_pack(manifest, files)]
    if errors:
        for error in errors:
            print(f"  - {error}")
        print(f"seed pack: FAIL — {len(errors)} error(s) in {pack_dir}")
        return 1
    print(f"seed pack: OK — {pack_dir} ({len(files)} item files validated)")
    return 0


def cmd_digest(path: Path) -> int:
    if path.is_dir():
        manifest, files, load_errors = load_pack_dir(path)
        errors = [*load_errors, *validate_pack(manifest, files)]
        if errors:
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            print(f"seed pack: FAIL — {len(errors)} error(s) in {path}", file=sys.stderr)
            return 1
        print(pack_digest(manifest))
        return 0
    print(content_digest(path.read_bytes()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed pack build/validation CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack", help="build the bundled pack from backend/data/seeds")
    pack.add_argument(
        "--out", type=Path, default=None, help="output pack directory (required without --check)"
    )
    pack.add_argument(
        "--seeds", type=Path, default=None, help="seeds dir (default: repo data/seeds)"
    )
    pack.add_argument(
        "--check",
        action="store_true",
        help="verify a checked-in pack is current instead of writing (exit non-zero on drift)",
    )
    pack.add_argument(
        "--against",
        type=Path,
        default=None,
        help="pack directory to verify in --check mode (default: --out)",
    )
    validate = sub.add_parser("validate", help="validate a pack directory")
    validate.add_argument("pack", type=Path)
    digest = sub.add_parser("digest", help="sha256 of a file, or whole-pack digest of a directory")
    digest.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "pack":
            return cmd_pack(args.out, args.seeds, args.check, args.against)
        if args.command == "validate":
            return cmd_validate(args.pack)
        return cmd_digest(args.path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"seed pack: error — {exc}", file=sys.stderr)
        return 2
    except SeedPackInvalid as exc:
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        print("seed pack: FAIL — bundled pack build refused", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
