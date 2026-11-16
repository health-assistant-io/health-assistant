"""Seed pack validation — the app-owned implementation of the pack contract.

Contract: ``contracts/seed-pack.schema.json`` (manifest) + the item-file
envelopes from ``backend/data/seeds/*.json``. The validator is hand-rolled on
purpose: no JSON-Schema dependency ships in the backend, the manifest subset
is small, and the fixtures at ``contracts/seed-pack.fixtures.json`` are the
executable pin that keeps the two honest.

Full pack validation (ADR 0056 Phase 2): manifest structure, digest
cross-refs, item-file envelopes, manifest path safety (``item_files[].path``
is classified textually — absolute/traversal/backslash forms get typed
errors — and :func:`load_pack_dir` refuses symlink escapes before reading
bytes), the item-type coverage matrix (unknown and app-only item types are
typed errors; global-only types are rejected in tenant packs), size/depth
caps, field-level rules (hex colors, code format, control characters) and
per-key-space ``seed_key`` duplicates. :func:`parse_pack` hands a validated
pack to the apply engine as a :class:`~app.core.seed_pack.items.ParsedPack`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

# items.py imports PackError from this module at runtime, so the objects
# parse_pack dispatches into can only be imported for type checking here.
if TYPE_CHECKING:
    from app.core.seed_pack.items import ParsedItemFile, ParsedPack

PACK_SCHEMA_VERSION = "1.0"

LICENSE_ALLOWLIST = frozenset({"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "Apache-2.0", "MIT"})

PRIORITY_MIN = 0
PRIORITY_MAX = 999
SYSTEM_PRIORITY_MAX = 199
TENANT_PRIORITY_MIN = 200

MANIFEST_NAME = "manifest.json"

MAX_ITEM_FILES = 200
MAX_DESCRIPTION_CHARS = 500
MAX_REPLACES_KEYS = 500

MAX_ITEM_FILE_BYTES = 5 * 1024 * 1024
"""Per-item-file byte cap (the shipped seeds total ~150 KB; untrusted packs
cannot smuggle a blob through an item file)."""

MAX_PACK_TOTAL_BYTES = 64 * 1024 * 1024
"""Whole-pack byte cap across all declared item files."""

MAX_JSON_DEPTH = 16
"""Maximum nesting depth of an item file's parsed JSON."""

MAX_JSON_NODES = 100_000
"""Maximum total node count (objects, arrays, scalars) per parsed item file."""

MANIFEST_REQUIRED = (
    "id",
    "schema_version",
    "version",
    "license",
    "scope",
    "priority",
    "item_files",
)

ERROR_CODES = frozenset(
    {
        "MANIFEST_FIELD_MISSING",
        "MANIFEST_UNKNOWN_FIELD",
        "MANIFEST_ID_INVALID",
        "SCHEMA_VERSION_UNSUPPORTED",
        "VERSION_INVALID",
        "LICENSE_NOT_ALLOWED",
        "SCOPE_INVALID",
        "PRIORITY_INVALID",
        "PRIORITY_SCOPE_MISMATCH",
        "DESCRIPTION_INVALID",
        "REPLACES_INVALID",
        "REPLACES_DUPLICATE",
        "MIN_APP_VERSION_UNMET",
        "ITEM_FILES_INVALID",
        "ITEM_FILE_INVALID",
        "ITEM_FILE_UNKNOWN_FIELD",
        "ITEM_PATH_INVALID",
        "ITEM_PATH_ABSOLUTE",
        "ITEM_PATH_TRAVERSAL",
        "ITEM_PATH_ESCAPE",
        "DUPLICATE_ITEM_PATH",
        "ITEM_DIGEST_INVALID",
        "ITEM_FILE_MISSING",
        "ITEM_DIGEST_MISMATCH",
        "ITEM_FILE_UNDECLARED",
        "ITEM_FILE_UNPARSEABLE",
        "ITEM_ENVELOPE_INVALID",
        "ITEM_TYPE_UNKNOWN",
        "APP_ONLY_ITEM_TYPE",
        "GLOBAL_ONLY_ITEM_TYPE",
        "ITEM_FILE_TOO_LARGE",
        "PACK_TOO_LARGE",
        "ITEM_JSON_TOO_DEEP",
        "ITEM_JSON_TOO_WIDE",
        "ITEM_FIELD_INVALID",
        "DUPLICATE_SEED_KEY",
    }
)
"""The closed typed-error vocabulary of the validator (test-pinned).

A new finding must join this set in the same change — the fixtures and the
pytest vocabulary pin fail otherwise, so consumers can pattern-match codes
without a drift.
"""

_PACK_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
_SEED_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,119}$")
_ITEM_PATH_RE = re.compile(r"^\.?[A-Za-z0-9][A-Za-z0-9._/-]*\.json$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_CODE_RE = re.compile(r"^\S.{0,62}\S$|^\S$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def semver_tuple(version: str) -> tuple[int, int, int] | None:
    """The leading ``major.minor.patch`` of a semver string, or ``None``."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def current_app_version() -> str | None:
    """The running app's version (``settings.VERSION``); ``None`` when the
    settings module is unavailable (pure-contract contexts)."""
    try:
        from app.core.config import settings

        return settings.VERSION
    except Exception:  # pragma: no cover - settings always import in the app
        return None


@dataclass(frozen=True)
class PackError:
    """One typed validation finding (code + location + detail)."""

    code: str
    path: str = ""
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        location = f" at {self.path}" if self.path else ""
        return f"{self.code}{location}: {self.detail}" if self.detail else f"{self.code}{location}"


def content_digest(content: str | bytes) -> str:
    """The sha256 a manifest must pin for these item-file bytes."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


def item_path_error(path: object) -> str | None:
    """The typed path-safety code for a manifest ``item_files[].path``.

    Returns ``None`` when the path is a safe pack-relative JSON path.
    Hostile shapes are classified before the general shape regex so the
    code is deterministic: backslash/Windows-drive/UNC forms and absolute
    POSIX paths are ``ITEM_PATH_ABSOLUTE``, any ``..`` segment is
    ``ITEM_PATH_TRAVERSAL``, everything else that is not a pack-relative
    ``.json`` path is ``ITEM_PATH_INVALID``. These checks are purely
    textual — callers must run them *before* touching the filesystem;
    symlink escape needs a real filesystem and is checked by
    :func:`load_pack_dir` (``ITEM_PATH_ESCAPE``).
    """
    if not isinstance(path, str):
        return "ITEM_PATH_INVALID"
    if "\\" in path or path.startswith("/") or PureWindowsPath(path).drive:
        return "ITEM_PATH_ABSOLUTE"
    if ".." in path.split("/"):
        return "ITEM_PATH_TRAVERSAL"
    if not _ITEM_PATH_RE.match(path):
        return "ITEM_PATH_INVALID"
    return None


def load_pack_dir(pack_dir: Path) -> tuple[dict, dict[str, bytes], list[PackError]]:
    """Load an on-disk pack: the manifest plus its declared item files.

    Path safety runs **before** any file is read: hostile manifest paths
    are never opened (their typed error surfaces from
    :func:`validate_manifest`), and a declared path that resolves outside
    ``pack_dir`` — a symlink escape — is refused with ``ITEM_PATH_ESCAPE``
    instead of being read. Missing files are simply absent from the result
    and surface as ``ITEM_FILE_MISSING`` during validation. Raises
    ``FileNotFoundError`` when there is no manifest and propagates JSON
    decode errors to the caller.
    """
    manifest_path = pack_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"{manifest_path} not found — a pack is a directory with {MANIFEST_NAME}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files: dict[str, bytes] = {}
    errors: list[PackError] = []
    root = pack_dir.resolve()
    for item_file in manifest.get("item_files", []):
        path = item_file.get("path") if isinstance(item_file, dict) else None
        if not isinstance(path, str):
            continue
        if item_path_error(path) is not None:
            continue
        file_path = pack_dir / path
        resolved = file_path.resolve()
        if root != resolved and root not in resolved.parents:
            errors.append(
                PackError("ITEM_PATH_ESCAPE", path, "resolves outside the pack directory")
            )
            continue
        if file_path.is_file():
            files[path] = file_path.read_bytes()
    return manifest, files, errors


MANIFEST_KNOWN_FIELDS = frozenset(
    {
        "id",
        "schema_version",
        "version",
        "license",
        "scope",
        "priority",
        "min_app_version",
        "description",
        "replaces",
        "item_files",
    }
)
"""Every manifest field the contract defines (schema ``properties`` parity)."""

ITEM_FILE_KNOWN_FIELDS = frozenset({"path", "sha256"})
"""Every ``item_files[]`` entry field the contract defines.

The schema pins ``additionalProperties: false`` on the entry objects; the
validator is authoritative and repeats the rule as a typed error so an
unknown key cannot ride through into the apply engine.
"""


def validate_manifest(manifest: Mapping[str, object]) -> list[PackError]:
    """Structural + policy validation of the manifest object alone."""
    errors: list[PackError] = []
    for field in MANIFEST_REQUIRED:
        if field not in manifest:
            errors.append(PackError("MANIFEST_FIELD_MISSING", field, "required field absent"))
    for field in manifest:
        if field not in MANIFEST_KNOWN_FIELDS:
            errors.append(
                PackError("MANIFEST_UNKNOWN_FIELD", field, "not part of the pack contract")
            )

    pack_id = manifest.get("id")
    if pack_id is not None and not (isinstance(pack_id, str) and _PACK_ID_RE.match(pack_id)):
        errors.append(
            PackError("MANIFEST_ID_INVALID", "id", f"{pack_id!r} is not kebab-case 3-64 chars")
        )

    if manifest.get("schema_version") != PACK_SCHEMA_VERSION:
        errors.append(
            PackError(
                "SCHEMA_VERSION_UNSUPPORTED",
                "schema_version",
                f"expected {PACK_SCHEMA_VERSION}, got {manifest.get('schema_version')!r}",
            )
        )

    version = manifest.get("version")
    if version is not None and not (isinstance(version, str) and _SEMVER_RE.match(version)):
        errors.append(PackError("VERSION_INVALID", "version", f"{version!r} is not semver"))

    license_id = manifest.get("license")
    if license_id is not None and license_id not in LICENSE_ALLOWLIST:
        errors.append(
            PackError(
                "LICENSE_NOT_ALLOWED",
                "license",
                f"{license_id!r} not in {sorted(LICENSE_ALLOWLIST)}",
            )
        )

    scope = manifest.get("scope")
    priority = manifest.get("priority")
    if scope not in ("system", "tenant"):
        errors.append(PackError("SCOPE_INVALID", "scope", f"{scope!r} is not system|tenant"))
    if not isinstance(priority, int) or isinstance(priority, bool):
        errors.append(
            PackError(
                "PRIORITY_INVALID",
                "priority",
                f"{priority!r} is not an integer {PRIORITY_MIN}-{PRIORITY_MAX}",
            )
        )
    elif not PRIORITY_MIN <= priority <= PRIORITY_MAX:
        errors.append(
            PackError(
                "PRIORITY_INVALID",
                "priority",
                f"{priority} out of range {PRIORITY_MIN}-{PRIORITY_MAX}",
            )
        )
    elif scope == "system" and priority > SYSTEM_PRIORITY_MAX:
        errors.append(
            PackError(
                "PRIORITY_SCOPE_MISMATCH",
                "priority",
                f"system packs use {PRIORITY_MIN}-{SYSTEM_PRIORITY_MAX}"
                f" (bundled 0, curated 100); got {priority}",
            )
        )
    elif scope == "tenant" and priority < TENANT_PRIORITY_MIN:
        errors.append(
            PackError(
                "PRIORITY_SCOPE_MISMATCH",
                "priority",
                f"tenant overlays use {TENANT_PRIORITY_MIN}-{PRIORITY_MAX}; got {priority}",
            )
        )

    min_app = manifest.get("min_app_version")
    if min_app is not None and not (isinstance(min_app, str) and _SEMVER_RE.match(min_app)):
        errors.append(PackError("VERSION_INVALID", "min_app_version", f"{min_app!r} is not semver"))

    description = manifest.get("description")
    if description is not None and (
        not isinstance(description, str) or len(description) > MAX_DESCRIPTION_CHARS
    ):
        errors.append(
            PackError(
                "DESCRIPTION_INVALID",
                "description",
                f"must be a string of at most {MAX_DESCRIPTION_CHARS} chars",
            )
        )

    errors.extend(_validate_replaces(manifest.get("replaces")))
    errors.extend(_validate_item_file_entries(manifest.get("item_files")))

    # The item-type coverage matrix (ADR 0056): unknown and app-only item
    # types are typed errors; a tenant pack may not target global-only types.
    scope = manifest.get("scope")
    item_files = manifest.get("item_files")
    if scope in ("system", "tenant") and isinstance(item_files, list):
        from app.core.seed_pack.items import item_type_error

        for entry in item_files:
            if not isinstance(entry, Mapping):
                continue
            path = entry.get("path")
            if not isinstance(path, str) or item_path_error(path) is not None:
                continue
            code = item_type_error(path, scope)
            if code is not None:
                errors.append(
                    PackError(
                        code,
                        path,
                        f"scope {scope!r} may not carry this item type",
                    )
                )
    return errors


def _validate_replaces(replaces: object) -> Iterable[PackError]:
    if replaces is None:
        return
    if not isinstance(replaces, list):
        yield PackError("REPLACES_INVALID", "replaces", "must be a list of seed keys")
        return
    if len(replaces) > MAX_REPLACES_KEYS:
        yield PackError("REPLACES_INVALID", "replaces", f"more than {MAX_REPLACES_KEYS} seed keys")
    seen: set[str] = set()
    for key in replaces:
        if not isinstance(key, str) or not _SEED_KEY_RE.match(key):
            yield PackError("REPLACES_INVALID", "replaces", f"{key!r} is not a seed key")
            continue
        if key in seen:
            yield PackError("REPLACES_DUPLICATE", "replaces", f"{key!r} listed twice")
        seen.add(key)


def _validate_item_file_entries(item_files: object) -> Iterable[PackError]:
    if item_files is None:
        return
    if not isinstance(item_files, list) or not item_files:
        yield PackError("ITEM_FILES_INVALID", "item_files", "must be a non-empty list")
        return
    if len(item_files) > MAX_ITEM_FILES:
        yield PackError(
            "ITEM_FILES_INVALID", "item_files", f"more than {MAX_ITEM_FILES} item files"
        )
    seen: set[str] = set()
    for entry in item_files:
        if not isinstance(entry, Mapping):
            yield PackError("ITEM_FILE_INVALID", "item_files", "entries must be objects")
            continue
        for key in entry:
            if key not in ITEM_FILE_KNOWN_FIELDS:
                yield PackError(
                    "ITEM_FILE_UNKNOWN_FIELD",
                    "item_files",
                    f"{key!r} is not part of the pack contract (expected "
                    f"{sorted(ITEM_FILE_KNOWN_FIELDS)})",
                )
        path = entry.get("path")
        digest = entry.get("sha256")
        path_code = item_path_error(path)
        if path_code is not None:
            yield PackError(path_code, "item_files", f"{path!r} is not a pack-relative JSON path")
        elif isinstance(path, str):
            # item_path_error returned None, so path is a validated pack-relative
            # str — the isinstance narrows it for the duplicate check below.
            if path in seen:
                yield PackError("DUPLICATE_ITEM_PATH", "item_files", path)
            seen.add(path)
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            yield PackError(
                "ITEM_DIGEST_INVALID", "item_files", f"{digest!r} is not a sha256 hex digest"
            )


def validate_item_file(path: str, content: str | bytes) -> list[PackError]:
    """Envelope validation of one item file (JSON parse + shape).

    Envelopes follow ``backend/data/seeds/*.json``: a metadata object plus
    list-valued collections whose elements are objects — ``items[]`` for the
    entity files, ``units[]``/``biomarkers[]`` for the default catalog,
    ``races[]``/``ethnicities[]``/``languages[]`` for the OMB picklist (the
    Phase-2 pack normalization may consolidate these; until then every
    shipped shape must validate).
    """
    try:
        parsed = json.loads(content if isinstance(content, str) else content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [PackError("ITEM_FILE_UNPARSEABLE", path, str(exc))]
    if not isinstance(parsed, dict):
        return [PackError("ITEM_ENVELOPE_INVALID", path, "item file must be a JSON object")]
    metadata = parsed.get("metadata")
    if not isinstance(metadata, dict):
        return [PackError("ITEM_ENVELOPE_INVALID", path, "envelope must carry a metadata object")]
    collections = {key: value for key, value in parsed.items() if isinstance(value, list)}
    if not collections:
        return [
            PackError(
                "ITEM_ENVELOPE_INVALID",
                path,
                "envelope must carry at least one list collection "
                "(items[] / units[]+biomarkers[] / picklists)",
            )
        ]
    for key, value in collections.items():
        if not all(isinstance(item, dict) for item in value):
            return [PackError("ITEM_ENVELOPE_INVALID", path, f"{key} entries must be objects")]
    return []


def _scan_json_shape(path: str, node: object, depth: int = 0) -> tuple[list, int]:
    """Depth/node caps + control-character sanitization over parsed JSON.

    Returns ``(errors, node_count)``; the walk doubles as the size guard so a
    deeply-nested or bloated untrusted file never reaches the seeders.
    """
    errors: list[PackError] = []
    if depth > MAX_JSON_DEPTH:
        return [PackError("ITEM_JSON_TOO_DEEP", path, f"deeper than {MAX_JSON_DEPTH} levels")], 0
    if isinstance(node, str):
        if _CONTROL_CHARS_RE.search(node):
            errors.append(
                PackError("ITEM_FIELD_INVALID", path, "string carries control characters")
            )
        return errors, 1
    if isinstance(node, dict):
        count = 1
        for value in node.values():
            sub_errors, sub_count = _scan_json_shape(path, value, depth + 1)
            errors.extend(sub_errors)
            count += sub_count
            if count > MAX_JSON_NODES:
                errors.append(
                    PackError("ITEM_JSON_TOO_WIDE", path, f"more than {MAX_JSON_NODES} nodes")
                )
                return errors, count
        return errors, count
    if isinstance(node, list):
        count = 1
        for value in node:
            sub_errors, sub_count = _scan_json_shape(path, value, depth + 1)
            errors.extend(sub_errors)
            count += sub_count
            if count > MAX_JSON_NODES:
                errors.append(
                    PackError("ITEM_JSON_TOO_WIDE", path, f"more than {MAX_JSON_NODES} nodes")
                )
                return errors, count
        return errors, count
    return errors, 1


def _check_item_fields(path: str, items: Iterable[Mapping[str, object]]) -> Iterable[PackError]:
    """Field-level rules over collection items (sanitization / code format).

    ``color`` must be a hex triple (the render hints feed CSS); ``code`` /
    ``standard_code`` must be short, trimmed, printable strings (codes are
    attributes — never fabricated, never rich text).
    """
    for item in items:
        value = item.get("color")
        if value is not None and (not isinstance(value, str) or not _HEX_COLOR_RE.match(value)):
            yield PackError(
                "ITEM_FIELD_INVALID", path, f"color {value!r} is not a #rrggbb hex color"
            )
        for field_name in ("code", "standard_code"):
            value = item.get(field_name)
            if value is None:
                continue
            if not isinstance(value, str) or not _CODE_RE.match(value) or len(value) > 64:
                yield PackError(
                    "ITEM_FIELD_INVALID",
                    path,
                    f"{field_name} {value!r} must be a trimmed, printable string of 1-64 chars",
                )


def _check_seed_key_duplicates(
    seen: dict[str, dict[str, str]], path: str, key_space: str, seed_keys: Iterable[str]
) -> Iterable[PackError]:
    """Per-key-space ``seed_key`` uniqueness (within and across item files)."""
    known = seen.setdefault(key_space, {})
    for seed_key in seed_keys:
        first_path = known.get(seed_key)
        if first_path is not None:
            yield PackError(
                "DUPLICATE_SEED_KEY",
                path,
                f"{key_space} key {seed_key!r} already declared in {first_path}",
            )
        else:
            known[seed_key] = path


def validate_pack(
    manifest: Mapping[str, object],
    files: Mapping[str, str | bytes],
    *,
    app_version: str | None = None,
) -> list[PackError]:
    """Validate a whole pack: manifest, cross-refs, item files, matrix, caps.

    ``files`` maps pack-relative paths to their content so the same code
    validates in-memory fixtures and on-disk packs. ``app_version`` overrides
    the running app's version for the ``min_app_version`` check (tests pass a
    pinned value; production uses ``settings.VERSION``).
    """
    errors = validate_manifest(manifest)
    item_files = manifest.get("item_files")
    if not isinstance(item_files, list):
        return errors

    from app.core.seed_pack.items import classify, iter_seed_keys

    total_bytes = 0
    seen_keys: dict[str, dict[str, str]] = {}

    for entry in item_files:
        if not isinstance(entry, Mapping):
            continue
        path = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(path, str) or item_path_error(path) is not None:
            continue
        if path not in files:
            errors.append(
                PackError("ITEM_FILE_MISSING", path, "manifest references a file not in the pack")
            )
            continue
        actual = content_digest(files[path])
        if digest is None or actual != digest:
            errors.append(
                PackError(
                    "ITEM_DIGEST_MISMATCH",
                    path,
                    f"manifest pins {digest!r}, content hashes to {actual!r}",
                )
            )
            continue

        raw = files[path]
        size = len(raw.encode("utf-8") if isinstance(raw, str) else raw)
        total_bytes += size
        if size > MAX_ITEM_FILE_BYTES:
            errors.append(
                PackError(
                    "ITEM_FILE_TOO_LARGE",
                    path,
                    f"{size} bytes exceeds the {MAX_ITEM_FILE_BYTES} cap",
                )
            )
            continue

        envelope_errors = validate_item_file(path, files[path])
        errors.extend(envelope_errors)
        if envelope_errors:
            continue

        try:
            parsed = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        shape_errors, _ = _scan_json_shape(path, parsed)
        errors.extend(shape_errors)
        if shape_errors:
            continue

        item_type = classify(path)
        collections = (
            [key for key in item_type.collections if isinstance(parsed.get(key), list)]
            if item_type is not None
            else [key for key, value in parsed.items() if isinstance(value, list)]
        )
        for key in collections:
            errors.extend(_check_item_fields(path, parsed.get(key) or []))
        if item_type is not None:
            collections_map = {key: parsed[key] for key in collections}
            keys = [seed_key for _, seed_key in iter_seed_keys(collections_map)]
            errors.extend(_check_seed_key_duplicates(seen_keys, path, item_type.key_space, keys))

    if total_bytes > MAX_PACK_TOTAL_BYTES:
        errors.append(
            PackError(
                "PACK_TOO_LARGE",
                "item_files",
                f"{total_bytes} total bytes exceeds the {MAX_PACK_TOTAL_BYTES} cap",
            )
        )

    known_paths = {entry.get("path") for entry in item_files if isinstance(entry, Mapping)}
    for path in files:
        if path not in known_paths:
            errors.append(
                PackError(
                    "ITEM_FILE_UNDECLARED", path, "file present but not in manifest item_files"
                )
            )

    min_app = manifest.get("min_app_version")
    if isinstance(min_app, str):
        resolved = app_version if app_version is not None else current_app_version()
        want = semver_tuple(min_app)
        have = semver_tuple(resolved) if resolved else None
        if want is not None and have is not None and want > have:
            errors.append(
                PackError(
                    "MIN_APP_VERSION_UNMET",
                    "min_app_version",
                    f"pack needs app >= {min_app}, running {resolved}",
                )
            )
    return errors


def parse_pack(
    manifest: Mapping[str, object],
    files: Mapping[str, str | bytes],
    *,
    app_version: str | None = None,
) -> tuple[ParsedPack | None, list[PackError]]:
    """Validate a pack and dispatch it into a :class:`ParsedPack`.

    Returns ``(None, errors)`` when the pack does not validate; otherwise the
    parsed pack (per-stage item files with normalized collections) and an
    empty error list — the apply engine's input shape (program Phase 9B).
    """
    from app.core.seed_pack.items import ParsedPack, pack_digest, parse_item_file

    errors = validate_pack(manifest, files, app_version=app_version)
    if errors:
        return None, errors

    parsed_files: dict[str, ParsedItemFile] = {}
    declared_item_files = manifest.get("item_files", [])
    for entry in declared_item_files if isinstance(declared_item_files, list) else []:
        if not isinstance(entry, Mapping):
            continue
        path = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(path, str) or path not in files:
            continue
        parsed_file, file_errors = parse_item_file(path, files[path], digest or "")
        if file_errors:
            errors.extend(file_errors)
            continue
        if parsed_file is not None:
            parsed_files[path] = parsed_file
    if errors:
        return None, errors

    return (
        ParsedPack(
            manifest=dict(manifest),
            digest=pack_digest(manifest),
            files=parsed_files,
        ),
        [],
    )
