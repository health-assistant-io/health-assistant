"""Seed pack contract — fixtures validate against the app-owned schema (ADR 0056)."""

import json
from pathlib import Path

import pytest

from app.core.seed_pack import (
    _ITEM_PATH_RE,
    _PACK_ID_RE,
    _SEED_KEY_RE,
    _SEMVER_RE,
    _SHA256_RE,
    ERROR_CODES,
    ITEM_FILE_KNOWN_FIELDS,
    LICENSE_ALLOWLIST,
    MANIFEST_KNOWN_FIELDS,
    MANIFEST_REQUIRED,
    MAX_DESCRIPTION_CHARS,
    MAX_ITEM_FILES,
    MAX_REPLACES_KEYS,
    PACK_SCHEMA_VERSION,
    PRIORITY_MAX,
    PRIORITY_MIN,
    PackError,
    content_digest,
    item_path_error,
    load_pack_dir,
    validate_item_file,
    validate_manifest,
    validate_pack,
)

CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "contracts"
FIXTURES_PATH = CONTRACTS_DIR / "seed-pack.fixtures.json"
SCHEMA_PATH = CONTRACTS_DIR / "seed-pack.schema.json"
SEEDS_DIR = Path(__file__).resolve().parent.parent / "data" / "seeds"


@pytest.fixture(scope="module")
def fixtures() -> dict:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


def _resolve_case(case: dict) -> tuple[dict, dict[str, str]]:
    """Materialize a fixture case: compute the pinned digests it references."""
    files = {
        path: json.dumps(content, ensure_ascii=False)
        for path, content in case.get("files", {}).items()
    }
    manifest = json.loads(json.dumps(case["manifest"]))
    for entry in manifest.get("item_files", []):
        pinned = entry.get("sha256", "")
        if pinned.startswith("SHA256_"):
            content = files.get(entry["path"])
            entry["sha256"] = content_digest(content) if content is not None else "0" * 64
    return manifest, files


def test_fixtures_are_exhaustive(fixtures):
    names = [case["name"] for case in fixtures["cases"]]
    assert len(names) == len(set(names))
    valid = [case for case in fixtures["cases"] if case["expect"] == "valid"]
    invalid = [case for case in fixtures["cases"] if case["expect"] == "invalid"]
    assert valid and invalid
    assert all("error" in case for case in invalid)


@pytest.mark.parametrize(
    "case_name",
    sorted(case["name"] for case in json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["cases"]),
)
def test_case_runs(case_name):
    """Named per-case smoke test so a fixture regression names its case."""
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    case = next(case for case in fixtures["cases"] if case["name"] == case_name)
    manifest, files = _resolve_case(case)
    errors = validate_pack(manifest, files)
    if case["expect"] == "valid":
        assert not errors, f"{case_name}: {errors}"
    else:
        assert case["error"] in [error.code for error in errors], (
            f"{case_name}: expected {case['error']}, got {[error.code for error in errors]}"
        )


def test_valid_cases_pass(fixtures):
    failures = []
    for case in fixtures["cases"]:
        if case["expect"] != "valid":
            continue
        manifest, files = _resolve_case(case)
        errors = validate_pack(manifest, files)
        if errors:
            failures.append(f"{case['name']}: {errors}")
    assert failures == []


def test_invalid_cases_fail_with_the_typed_code(fixtures):
    failures = []
    for case in fixtures["cases"]:
        if case["expect"] != "invalid":
            continue
        manifest, files = _resolve_case(case)
        errors = validate_pack(manifest, files)
        codes = [error.code for error in errors]
        if case["error"] not in codes:
            failures.append(f"{case['name']}: expected {case['error']}, got {codes}")
    assert failures == []


def test_real_seed_files_satisfy_the_envelope_rule():
    """The contract tracks backend/data/seeds/*.json — every shipped seed file
    must parse as a valid item envelope (default_catalog's units/biomarkers
    envelope included)."""
    failures = []
    for seed_file in sorted(SEEDS_DIR.glob("*.json")):
        errors = validate_item_file(seed_file.name, seed_file.read_bytes())
        if errors:
            failures.append(f"{seed_file.name}: {errors}")
    assert failures == [], "\n".join(failures)


def test_real_seed_files_are_pinnable():
    """Every shipped seed file hashes to a stable digest (canonical pin)."""
    digests = {
        path.name: content_digest(path.read_bytes()) for path in sorted(SEEDS_DIR.glob("*.json"))
    }
    assert digests
    assert all(len(digest) == 64 for digest in digests.values())
    assert digests == {
        path.name: content_digest(path.read_bytes()) for path in sorted(SEEDS_DIR.glob("*.json"))
    }


def test_content_digest_is_a_byte_exact_pin():
    """The manifest digest pins exact bytes, not parsed JSON.

    Deterministic for the same bytes, sensitive to key order/whitespace
    (re-serialization changes the digest — the source lane pins versions by
    exact bytes), and to any semantic field change.
    """
    document = '{"metadata": {"version": "1.0.0"}, "items": [{"name": "A"}]}'
    reordered_keys = '{"items": [{"name": "A"}], "metadata": {"version": "1.0.0"}}'
    whitespace_variant = '{\n  "metadata": {"version": "1.0.0"},\n  "items": [{"name": "A"}]}\n'
    semantic_change = '{"metadata": {"version": "1.0.1"}, "items": [{"name": "A"}]}'

    assert content_digest(document) == content_digest(document)
    assert len(content_digest(document)) == 64
    assert content_digest(document) != content_digest(reordered_keys)
    assert content_digest(document) != content_digest(whitespace_variant)
    assert content_digest(document) != content_digest(semantic_change)


def test_error_vocabulary_is_closed():
    """The typed-error set is exact: fixtures and validator cannot drift."""
    assert {
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
    } == ERROR_CODES
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    fixture_codes = {case["error"] for case in fixtures["cases"] if case["expect"] == "invalid"}
    assert fixture_codes <= ERROR_CODES


def test_item_path_classifier_rejects_hostile_forms():
    """Absolute/drive/backslash/traversal forms get their typed code before
    any filesystem use (S3: the loader never opens them)."""
    assert item_path_error("items/medications.json") is None
    assert item_path_error(None) == "ITEM_PATH_INVALID"
    assert item_path_error(123) == "ITEM_PATH_INVALID"
    assert item_path_error("items/notes.txt") == "ITEM_PATH_INVALID"
    assert item_path_error("/etc/health/secrets.json") == "ITEM_PATH_ABSOLUTE"
    assert item_path_error("C:/users/public/secrets.json") == "ITEM_PATH_ABSOLUTE"
    assert item_path_error("items\\..\\secrets.json") == "ITEM_PATH_ABSOLUTE"
    assert item_path_error("\\\\server\\share\\secrets.json") == "ITEM_PATH_ABSOLUTE"
    assert item_path_error("items/../secrets.json") == "ITEM_PATH_TRAVERSAL"
    assert item_path_error("items/../../secrets.json") == "ITEM_PATH_TRAVERSAL"
    assert item_path_error("../secrets.json") == "ITEM_PATH_TRAVERSAL"


def test_manifest_only_validation_matches_whole_pack_for_manifest_errors():
    manifest = {"id": "x-pack", "schema_version": "1.0"}
    manifest_codes = {error.code for error in validate_manifest(manifest)}
    pack_codes = {error.code for error in validate_pack(manifest, {})}
    assert "MANIFEST_FIELD_MISSING" in manifest_codes
    assert "MANIFEST_FIELD_MISSING" in pack_codes
    assert all(isinstance(error, PackError) for error in validate_manifest(manifest))


def test_schema_and_validator_stay_in_parity():
    """The JSON contract and the app validator must not diverge (S6).

    Every constraint expressed in ``seed-pack.schema.json`` is compared
    against the validator's constants; changing one side without the other
    fails this test.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    properties = schema["properties"]

    assert set(schema["required"]) == set(MANIFEST_REQUIRED)
    assert set(properties) == set(MANIFEST_KNOWN_FIELDS)
    assert properties["schema_version"]["enum"] == [PACK_SCHEMA_VERSION]
    assert properties["id"]["pattern"] == _PACK_ID_RE.pattern
    assert properties["version"]["pattern"] == _SEMVER_RE.pattern
    assert properties["min_app_version"]["pattern"] == _SEMVER_RE.pattern
    assert properties["license"]["enum"] == sorted(LICENSE_ALLOWLIST)
    assert properties["scope"]["enum"] == ["system", "tenant"]
    assert properties["priority"]["minimum"] == PRIORITY_MIN
    assert properties["priority"]["maximum"] == PRIORITY_MAX
    assert properties["description"]["maxLength"] == MAX_DESCRIPTION_CHARS
    assert properties["replaces"]["items"]["pattern"] == _SEED_KEY_RE.pattern
    assert properties["replaces"]["maxItems"] == MAX_REPLACES_KEYS
    assert properties["replaces"]["uniqueItems"] is True
    item_files = properties["item_files"]
    assert item_files["minItems"] == 1
    assert item_files["maxItems"] == MAX_ITEM_FILES
    assert item_files["items"]["required"] == ["path", "sha256"]
    assert item_files["items"]["additionalProperties"] is False
    assert set(item_files["items"]["properties"]) == set(ITEM_FILE_KNOWN_FIELDS)
    assert item_files["items"]["properties"]["path"]["pattern"] == _ITEM_PATH_RE.pattern
    assert item_files["items"]["properties"]["sha256"]["pattern"] == _SHA256_RE.pattern


def test_replaces_is_capped_like_the_schema():
    manifest = {
        "id": "replace-all-pack",
        "schema_version": "1.0",
        "version": "1.0.0",
        "license": "MIT",
        "scope": "tenant",
        "priority": 200,
        "replaces": [f"key-{index:04d}" for index in range(MAX_REPLACES_KEYS + 1)],
        "item_files": [{"path": "items/medications.json", "sha256": "0" * 64}],
    }
    assert "REPLACES_INVALID" in [error.code for error in validate_manifest(manifest)]


def test_load_pack_dir_refuses_symlink_escape_before_reading(tmp_path):
    """A declared path that resolves outside the pack (symlinked folder or
    file) is never read: ITEM_PATH_ESCAPE, and the outside bytes do not
    enter validation (S3)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "medications.json").write_text('{"metadata": {}, "items": []}', encoding="utf-8")
    manifest = {
        "id": "escape-pack",
        "schema_version": "1.0",
        "version": "1.0.0",
        "license": "MIT",
        "scope": "system",
        "priority": 0,
        "item_files": [{"path": "items/medications.json", "sha256": "0" * 64}],
    }
    pack_dir = tmp_path / "pack"
    (pack_dir / "items").mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack_dir / "items" / "medications.json").symlink_to(outside / "medications.json")

    loaded_manifest, files, errors = load_pack_dir(pack_dir)

    assert loaded_manifest == manifest
    assert files == {}
    assert [error.code for error in errors] == ["ITEM_PATH_ESCAPE"]


def test_load_pack_dir_never_reads_hostile_manifest_paths(tmp_path):
    """Absolute and traversal paths from the manifest are not opened even
    when the files exist at those locations."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "secrets.json").write_text(
        '{"metadata": {}, "items": []}', encoding="utf-8"
    )
    manifest = {
        "id": "hostile-pack",
        "schema_version": "1.0",
        "version": "1.0.0",
        "license": "MIT",
        "scope": "system",
        "priority": 0,
        "item_files": [
            {"path": "/etc/secrets.json", "sha256": "0" * 64},
            {"path": "../etc/secrets.json", "sha256": "0" * 64},
        ],
    }
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    loaded_manifest, files, errors = load_pack_dir(pack_dir)

    assert files == {}
    assert errors == []
    codes = {error.code for error in validate_pack(loaded_manifest, files)}
    assert "ITEM_PATH_ABSOLUTE" in codes
    assert "ITEM_PATH_TRAVERSAL" in codes


def test_cli_validate_reports_escape_as_failure(tmp_path, capsys):
    """End to end through the CLI: a symlink-escaping pack fails with exit 1
    and the typed code on stdout."""
    from scripts.seed_pack import cmd_validate

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "medications.json").write_text('{"metadata": {}, "items": []}', encoding="utf-8")
    manifest = {
        "id": "cli-escape-pack",
        "schema_version": "1.0",
        "version": "1.0.0",
        "license": "MIT",
        "scope": "system",
        "priority": 0,
        "item_files": [{"path": "items/medications.json", "sha256": "0" * 64}],
    }
    pack_dir = tmp_path / "pack"
    (pack_dir / "items").mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack_dir / "items" / "medications.json").symlink_to(outside / "medications.json")

    exit_code = cmd_validate(pack_dir)
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "ITEM_PATH_ESCAPE" in out
