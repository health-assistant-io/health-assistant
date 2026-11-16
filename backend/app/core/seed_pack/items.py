"""The pack item-type registry — the coverage matrix and dispatch (ADR 0056).

One table maps every pack item file to its seed stage and capabilities:

* **stage** — the ``SeedService`` stage name the file feeds (the dispatch
  key ``seed_all`` and the single-stage CLIs use).
* **collections** — the list-valued envelope keys the file carries
  (``{"items"}`` for the entity files, ``{"units", "biomarkers"}`` for the
  default catalog, the three picklists for the OMB file). The parser
  normalizes every stage to its collections mapping so the seeders consume
  lists, whatever the file's original envelope shape was.
* **tenant_ok** — the tenant capability matrix: ``False`` marks a
  **global-only** type (``units``, ``biomarker_states``, the system
  taxonomy/concepts, anatomy, edges/panels, the OMB vocabulary). A tenant
  pack (``scope: tenant``) declaring a global-only item file is rejected
  with ``GLOBAL_ONLY_ITEM_TYPE``; system packs may target everything.
* **key_space** — the identity table the file's ``seed_key``s populate;
  ``concepts.json`` and ``diseases.json`` share the concept key space, so a
  key repeated across the two files of one pack is a ``DUPLICATE_SEED_KEY``.

Not pack items (the app-only stages): ``retention_policies``,
``proposal_kinds`` and ``ai_task_defs`` are code/contract vocabularies the
app seeds from its own registries — a pack declaring them is refused with
``APP_ONLY_ITEM_TYPE``. The binary anatomy figures stay bundled-only in v1
(they are copied to ``HA_UPLOAD_DIR`` by the figure seeder, not shipped
through packs); the coverage matrix documents this.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.core.seed_pack.manifest import PackError


@dataclass(frozen=True)
class ItemType:
    """One pack item file type (coverage-matrix row)."""

    filename: str
    stage: str
    collections: tuple[str, ...]
    tenant_ok: bool
    key_space: str


REGISTRY: tuple[ItemType, ...] = (
    ItemType("concepts.json", "concepts", ("items",), False, "concept"),
    ItemType("diseases.json", "diseases", ("items",), False, "concept"),
    ItemType("medications.json", "medications", ("items",), True, "medication"),
    ItemType("vaccines.json", "vaccines", ("items",), True, "vaccine"),
    ItemType(
        "clinical_event_types.json", "clinical_event_types", ("items",), True, "clinical-event-type"
    ),
    ItemType("allergies.json", "allergies", ("items",), True, "allergy"),
    ItemType("anatomy_structures.json", "body_parts", ("items",), False, "anatomy"),
    ItemType("biomarker_states.json", "biomarker_states", ("items",), False, "biomarker-state"),
    ItemType("default_catalog.json", "default_catalog", ("units", "biomarkers"), False, "catalog"),
    ItemType("concept_edges.json", "concept_edges", ("items",), False, "concept-edge"),
    ItemType("biomarker_panels.json", "biomarker_panels", ("items",), False, "panel-membership"),
    ItemType(
        "omb_race_ethnicity.json",
        "omb_picklist",
        ("races", "ethnicities", "languages"),
        False,
        "omb",
    ),
)
"""The coverage matrix: every pack item type, its stage and capabilities."""

BY_FILENAME: dict[str, ItemType] = {item.filename: item for item in REGISTRY}

APP_ONLY_FILENAMES: frozenset[str] = frozenset(
    {"retention_policies.json", "proposal_kinds.json", "ai_task_defs.json"}
)
"""Code/contract vocabularies seeded by the app — never pack items."""

GLOBAL_ONLY_STAGES: frozenset[str] = frozenset(
    item.stage for item in REGISTRY if not item.tenant_ok
)
"""Stages a tenant pack may not target (the capability matrix's reject set)."""

BUNDLED_ONLY_BINARY = "anatomy_figures/"
"""Binary anatomy figures are bundled-only in v1 (documented; never a pack
item path — the path grammar only admits ``.json`` anyway)."""


def classify(path: str) -> ItemType | None:
    """The registry entry for a pack-relative item path (``None`` = unknown)."""
    return BY_FILENAME.get(path.rsplit("/", 1)[-1]) if path.endswith(".json") else None


def item_type_error(path: str, scope: str) -> str | None:
    """The typed matrix code for a pack item path, or ``None`` when allowed.

    ``scope`` is the manifest's declared pack scope (``system|tenant``).
    Classification is textual — the manifest path-safety checks have already
    classified hostile shapes; this classifies the item *type*.
    """
    filename = path.rsplit("/", 1)[-1]
    if filename in APP_ONLY_FILENAMES:
        return "APP_ONLY_ITEM_TYPE"
    item = BY_FILENAME.get(filename)
    if item is None:
        return "ITEM_TYPE_UNKNOWN"
    if scope == "tenant" and not item.tenant_ok:
        return "GLOBAL_ONLY_ITEM_TYPE"
    return None


@dataclass(frozen=True)
class ParsedItemFile:
    """One parsed item file: its stage, digest and normalized collections."""

    path: str
    stage: str
    digest: str
    collections: dict[str, list[dict]] = field(default_factory=dict, compare=False)

    def items(self) -> list[dict]:
        """The file's primary entity list (``items[]``; empty otherwise)."""
        return self.collections.get("items", [])


@dataclass(frozen=True)
class ParsedPack:
    """A validated pack: the manifest, its whole-pack digest and the parsed
    item files dispatched by stage.

    ``stage_items`` merges the entity lists of a stage's files (the shape the
    entity seeders consume); ``stage_data`` returns the whole normalized
    collections mapping of a stage's files (``default_catalog``'s
    units/biomarkers, the OMB picklist's lists).
    """

    manifest: dict
    digest: str
    files: dict[str, ParsedItemFile]

    def stage_files(self, stage: str) -> list[ParsedItemFile]:
        return [parsed for parsed in self.files.values() if parsed.stage == stage]

    def stage_items(self, stage: str) -> list[dict]:
        out: list[dict] = []
        for parsed in self.stage_files(stage):
            out.extend(parsed.items())
        return out

    def stage_data(self, stage: str) -> dict[str, list[dict]]:
        merged: dict[str, list[dict]] = {}
        for parsed in self.stage_files(stage):
            for key, rows in parsed.collections.items():
                merged.setdefault(key, []).extend(rows)
        return merged


def parse_item_file(
    path: str, content: str | bytes, digest: str
) -> tuple[ParsedItemFile | None, list]:
    """Normalize one validated item file into its stage + collections.

    The envelope was already validated (``validate_item_file``): a metadata
    object plus list collections. The registry's declared collections drive
    the dispatch; a file whose declared collections are all absent is an
    envelope error.
    """
    try:
        parsed = json.loads(content if isinstance(content, str) else content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, [PackError("ITEM_FILE_UNPARSEABLE", path, str(exc))]
    item_type = classify(path)
    if item_type is None:
        return None, [PackError("ITEM_TYPE_UNKNOWN", path, "no registered pack item type")]
    collections: dict[str, list[dict]] = {}
    for key in item_type.collections:
        value = parsed.get(key)
        if isinstance(value, list) and all(isinstance(entry, dict) for entry in value):
            collections[key] = value
    if not collections:
        return None, [
            PackError(
                "ITEM_ENVELOPE_INVALID",
                path,
                f"expected list collections {list(item_type.collections)}",
            )
        ]
    parsed = ParsedItemFile(
        path=path, stage=item_type.stage, digest=digest, collections=collections
    )
    return parsed, []


def pack_digest(manifest: Mapping[str, object]) -> str:
    """The whole-pack content digest: sha256 over the canonical manifest pin.

    Digests the content-addressed part of the manifest — the declared
    ``schema_version`` and the sorted ``(path, sha256)`` item pins — through
    canonical JSON (sorted keys, no whitespace). Human-facing manifest fields
    (description, license, priority) do not move the digest, and there are no
    timestamps anywhere: the same content always digests the same.
    """
    item_files = manifest.get("item_files")
    pins = (
        sorted(
            [entry["path"], entry["sha256"]]
            for entry in item_files
            if isinstance(entry, Mapping)
            and isinstance(entry.get("path"), str)
            and isinstance(entry.get("sha256"), str)
        )
        if isinstance(item_files, list)
        else []
    )
    canonical = json.dumps(
        {"schema_version": manifest.get("schema_version"), "item_files": pins},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def iter_seed_keys(collections: Mapping[str, Iterable[dict]]) -> Iterable[tuple[str, str]]:
    """Every ``(collection, seed_key)`` in a parsed file's collections."""
    for key, rows in collections.items():
        for row in rows:
            seed_key = row.get("seed_key")
            if isinstance(seed_key, str) and seed_key:
                yield key, seed_key
