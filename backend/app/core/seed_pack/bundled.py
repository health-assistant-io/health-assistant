"""The bundled pack — ``backend/data/seeds`` wrapped as a seed pack (ADR 0056).

The dev loop stays "edit ``data/seeds/*.json`` → ``seed_pack.py pack`` →
restart": this module builds the bundled pack (manifest + per-file hashes)
from the seeds directory, validates it through the same contract as every
other pack, and hands the parsed stages to ``SeedService.seed_all``.

Determinism rules: the manifest carries no timestamps and the builder emits
byte-identical item files (raw copies — the digests pin exact bytes), so the
same seeds always produce the same manifest and the same whole-pack digest.

The bundled pack is **trusted** (auto-approved by definition, ADR 0056 D9):
the boot apply path (program Phase 9B) re-checks its digest and applies it
when it changes on a deploy. Binary anatomy figures stay bundled-only in v1 —
they ride with the repo and are copied to ``HA_UPLOAD_DIR`` by the figure
seeder, not through packs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.seed_pack.items import REGISTRY, ParsedPack
from app.core.seed_pack.manifest import (
    MANIFEST_NAME,
    PACK_SCHEMA_VERSION,
    PackError,
    content_digest,
    parse_pack,
)

logger = logging.getLogger(__name__)

BUNDLED_PACK_ID = "health-bundled-seeds"
BUNDLED_PACK_VERSION = "1.0.0"
BUNDLED_LICENSE = "CC-BY-SA-4.0"
BUNDLED_DESCRIPTION = (
    "Bundled curated seed catalog (concepts, diseases, medications, vaccines, "
    "allergies, clinical event types, anatomy structures, biomarker states, "
    "default catalog, edges, panels, OMB picklist). Anatomy figures ship as "
    "bundled WebP assets outside the pack (CC BY-SA 3.0, see NOTICE)."
)

#: The bundled item files, in manifest order (the coverage matrix's file set).
BUNDLED_ITEM_FILES: tuple[str, ...] = tuple(item.filename for item in REGISTRY)


class SeedPackInvalid(RuntimeError):
    """A pack failed validation — carries the typed :class:`PackError` list."""

    def __init__(self, errors: list[PackError]) -> None:
        rendered = "; ".join(str(error) for error in errors)
        super().__init__(f"seed pack invalid: {rendered}")
        self.errors = errors


def bundled_seeds_dir() -> Path:
    """The repo's ``backend/data/seeds`` directory."""
    return Path(__file__).resolve().parents[3] / "data" / "seeds"


def build_bundled_pack(seeds_dir: Path | None = None) -> tuple[dict, dict[str, bytes]]:
    """Build the bundled pack from a seeds directory (manifest + raw files).

    Item paths are ``items/<filename>``; the manifest pins each file's exact
    bytes. Every ``*.json`` file in the seeds dir must be a registered pack
    item — an unregistered JSON file is a loud error, not a silent skip (the
    coverage matrix is the whole truth).
    """
    seeds_dir = seeds_dir or bundled_seeds_dir()
    item_files: list[dict[str, str]] = []
    files: dict[str, bytes] = {}
    for filename in BUNDLED_ITEM_FILES:
        path = seeds_dir / filename
        if not path.is_file():
            raise SeedPackInvalid(
                [PackError("ITEM_FILE_MISSING", f"items/{filename}", f"{path} not found")]
            )
        digest = content_digest(path.read_bytes())
        files[f"items/{filename}"] = path.read_bytes()
        item_files.append({"path": f"items/{filename}", "sha256": digest})
    for stray in sorted(seeds_dir.glob("*.json")):
        if stray.name not in BUNDLED_ITEM_FILES:
            raise SeedPackInvalid(
                [
                    PackError(
                        "ITEM_TYPE_UNKNOWN",
                        stray.name,
                        "a seeds-dir JSON file is not a registered pack item type",
                    )
                ]
            )
    manifest = {
        "id": BUNDLED_PACK_ID,
        "schema_version": PACK_SCHEMA_VERSION,
        "version": BUNDLED_PACK_VERSION,
        "license": BUNDLED_LICENSE,
        "scope": "system",
        "priority": 0,
        "description": BUNDLED_DESCRIPTION,
        "item_files": item_files,
    }
    return manifest, files


def write_bundled_pack(out_dir: Path, seeds_dir: Path | None = None) -> str:
    """Write the bundled pack (``manifest.json`` + ``items/*.json``) to disk.

    Returns the whole-pack digest. The written files are raw copies, so the
    digests in the manifest pin the exact bytes on disk.
    """
    manifest, files = build_bundled_pack(seeds_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "items").mkdir(exist_ok=True)
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    for path, content in files.items():
        (out_dir / path).write_bytes(content)
    from app.core.seed_pack.items import pack_digest

    return pack_digest(manifest)


def load_bundled_pack(seeds_dir: Path | None = None) -> ParsedPack:
    """Load + validate the bundled pack (cached per seeds directory).

    Raises:
        SeedPackInvalid: when the bundled content fails the pack contract —
            the boot path surfaces this (abort in production, warn in dev).
    """
    resolved = (seeds_dir or bundled_seeds_dir()).resolve()
    cached = _CACHE.get(str(resolved))
    if cached is not None:
        return cached
    manifest, files = build_bundled_pack(resolved)
    parsed, errors = parse_pack(manifest, files)
    if errors or parsed is None:
        raise SeedPackInvalid(errors)
    _CACHE[str(resolved)] = parsed
    return parsed


def bundled_stage_items(stage: str, seeds_dir: Path | None = None) -> list[dict]:
    """A bundled stage's parsed entity items (the seeders' default input)."""
    return load_bundled_pack(seeds_dir).stage_items(stage)


def bundled_stage_data(stage: str, seeds_dir: Path | None = None) -> dict[str, list[dict]]:
    """A bundled stage's normalized collections (``default_catalog`` etc.)."""
    return load_bundled_pack(seeds_dir).stage_data(stage)


def omb_picklist(seeds_dir: Path | None = None) -> dict[str, list[dict]]:
    """The bundled OMB race/ethnicity/language picklist (consumers read it
    through the pack path — validated with everything else, never raw)."""
    data = bundled_stage_data("omb_picklist", seeds_dir)
    return data if data else {}


_CACHE: dict[str, ParsedPack] = {}
