"""Pack parser/validator + bundled pack build (ADR 0056 Phase 2, program 9A).

Pins the Phase-2 behavior the fixtures don't carry: the coverage matrix and
its typed errors, the caps (bytes/depth/nodes), the field-level rules, the
per-key-space ``seed_key`` duplicates, the ``min_app_version`` gate, the
bundled-pack build/validate/determinism (no timestamps anywhere) and the
CLI ``pack``/``validate``/``digest`` round trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.seed_pack import (
    APP_ONLY_FILENAMES,
    GLOBAL_ONLY_STAGES,
    REGISTRY,
    PackError,
    SeedPackInvalid,
    build_bundled_pack,
    bundled_seeds_dir,
    load_bundled_pack,
    omb_picklist,
    pack_digest,
    parse_pack,
    validate_pack,
)
from scripts.seed_pack import cmd_digest, cmd_pack, cmd_validate

BACKEND = Path(__file__).resolve().parents[1]
SEEDS_DIR = BACKEND / "data" / "seeds"


def _manifest(**overrides) -> dict:
    manifest = {
        "id": "parser-pack",
        "schema_version": "1.0",
        "version": "1.0.0",
        "license": "MIT",
        "scope": "system",
        "priority": 0,
        "item_files": [
            {"path": "items/medications.json", "sha256": "0" * 64},
        ],
    }
    manifest.update(overrides)
    return manifest


def _envelope(items: list[dict]) -> str:
    return json.dumps({"metadata": {"version": "1.0.0"}, "items": items})


def _med_file(items: list[dict]) -> str:
    return _envelope(items)


def _catalog(units: list[dict], biomarkers: list[dict]) -> str:
    return json.dumps({"metadata": {"version": "1.0.0"}, "units": units, "biomarkers": biomarkers})


def _pack(manifest: dict, files: dict[str, object]):
    from app.core.seed_pack import content_digest

    resolved = {}
    for path, content in files.items():
        raw = content if isinstance(content, (str, bytes)) else json.dumps(content)
        resolved[path] = raw
        for entry in manifest.get("item_files", []):
            if entry["path"] == path:
                entry["sha256"] = content_digest(raw)
    return manifest, resolved


# --- coverage matrix -------------------------------------------------------


def test_coverage_matrix_covers_every_shipped_seed_file():
    """Every ``data/seeds/*.json`` is a registered pack item — the matrix is
    the whole truth; the bundled build refuses unregistered files."""
    shipped = {path.name for path in SEEDS_DIR.glob("*.json")}
    assert shipped == {item.filename for item in REGISTRY}


def test_coverage_matrix_tenant_capability_split():
    """Tenant packs may target the tenant-ownable types only; the global-only
    stages (units, states, taxonomy/concepts, anatomy, edges, OMB) are the
    documented reject set."""
    assert {
        "concepts",
        "diseases",
        "body_parts",
        "biomarker_states",
        "default_catalog",
        "concept_edges",
        "biomarker_panels",
        "omb_picklist",
    } == GLOBAL_ONLY_STAGES
    for item in REGISTRY:
        if item.tenant_ok:
            assert item.stage in {"medications", "vaccines", "allergies", "clinical_event_types"}


def test_app_only_stages_are_never_pack_items():
    assert {
        "retention_policies.json",
        "proposal_kinds.json",
        "ai_task_defs.json",
    } == APP_ONLY_FILENAMES
    assert all(item.filename not in APP_ONLY_FILENAMES for item in REGISTRY)


def test_matrix_typed_errors():
    manifest, files = _pack(
        _manifest(
            scope="tenant",
            priority=200,
            item_files=[{"path": "items/concepts.json", "sha256": "0" * 64}],
        ),
        {"items/concepts.json": {"metadata": {}, "items": []}},
    )
    assert "GLOBAL_ONLY_ITEM_TYPE" in [error.code for error in validate_pack(manifest, files)]

    manifest, files = _pack(
        _manifest(item_files=[{"path": "items/retention_policies.json", "sha256": "0" * 64}]),
        {"items/retention_policies.json": {"metadata": {}, "items": []}},
    )
    assert "APP_ONLY_ITEM_TYPE" in [error.code for error in validate_pack(manifest, files)]

    manifest, files = _pack(
        _manifest(item_files=[{"path": "items/glossary.json", "sha256": "0" * 64}]),
        {"items/glossary.json": {"metadata": {}, "items": []}},
    )
    assert "ITEM_TYPE_UNKNOWN" in [error.code for error in validate_pack(manifest, files)]


# --- item-level validation ---------------------------------------------------


def test_duplicate_seed_key_within_one_file():
    manifest, files = _pack(
        _manifest(),
        {
            "items/medications.json": _med_file(
                [
                    {"seed_key": "medication:aspirin", "name": "Aspirin"},
                    {"seed_key": "medication:aspirin", "name": "Aspirin again"},
                ]
            )
        },
    )
    codes = [error.code for error in validate_pack(manifest, files)]
    assert codes.count("DUPLICATE_SEED_KEY") == 1


def test_duplicate_seed_key_across_same_key_space():
    """``concepts.json`` and ``diseases.json`` share the concept key space —
    the same key in both files of one pack is a duplicate."""
    manifest, files = _pack(
        _manifest(
            item_files=[
                {"path": "items/concepts.json", "sha256": "0" * 64},
                {"path": "items/diseases.json", "sha256": "0" * 64},
            ]
        ),
        {
            "items/concepts.json": _envelope(
                [
                    {
                        "seed_key": "concept:cardiology",
                        "slug": "cardiology",
                        "name": "Cardiology",
                        "kind": "specialty",
                    }
                ]
            ),
            "items/diseases.json": _envelope(
                [
                    {
                        "seed_key": "concept:cardiology",
                        "slug": "cardiology",
                        "name": "Cardiology",
                        "kind": "disease",
                    }
                ]
            ),
        },
    )
    assert "DUPLICATE_SEED_KEY" in [error.code for error in validate_pack(manifest, files)]


def test_caps_reject_oversized_deep_and_wide_files():
    manifest, files = _pack(_manifest(), {"items/medications.json": "x" * (5 * 1024 * 1024 + 1)})
    assert "ITEM_FILE_TOO_LARGE" in [error.code for error in validate_pack(manifest, files)]

    deep_item: dict = {}
    node = deep_item
    for _ in range(20):
        node["a"] = {}
        node = node["a"]
    manifest, files = _pack(_manifest(), {"items/medications.json": _envelope([deep_item])})
    assert "ITEM_JSON_TOO_DEEP" in [error.code for error in validate_pack(manifest, files)]

    wide = _envelope([{"n": i} for i in range(100_001)])
    manifest, files = _pack(_manifest(), {"items/medications.json": wide})
    assert "ITEM_JSON_TOO_WIDE" in [error.code for error in validate_pack(manifest, files)]


def test_field_rules_color_code_and_control_characters():
    manifest, files = _pack(
        _manifest(),
        {
            "items/medications.json": _med_file(
                [
                    {"seed_key": "medication:a", "name": "A", "color": "red"},
                    {"seed_key": "medication:b", "name": "B", "code": " L "},
                ]
            )
        },
    )
    codes = [error.code for error in validate_pack(manifest, files)]
    assert codes.count("ITEM_FIELD_INVALID") == 2

    manifest, files = _pack(
        _manifest(),
        {"items/medications.json": _med_file([{"seed_key": "med:a", "name": "A\x00B"}])},
    )
    codes = [error.code for error in validate_pack(manifest, files)]
    assert "ITEM_FIELD_INVALID" in codes


def test_min_app_version_unmet_is_typed_and_env_overridable():
    manifest, files = _pack(
        _manifest(min_app_version="0.1.0"), {"items/medications.json": _envelope([])}
    )
    assert validate_pack(manifest, files, app_version="0.9.0") == []
    codes = [error.code for error in validate_pack(manifest, files, app_version="0.1.0")]
    assert codes == []
    codes = [error.code for error in validate_pack(manifest, files, app_version="0.0.9")]
    assert "MIN_APP_VERSION_UNMET" in codes


# --- dispatch / parse --------------------------------------------------------


def test_parse_pack_dispatches_stages_and_collections():
    manifest, files = _pack(
        _manifest(
            item_files=[
                {"path": "items/medications.json", "sha256": "0" * 64},
                {"path": "items/default_catalog.json", "sha256": "0" * 64},
            ]
        ),
        {
            "items/medications.json": _med_file(
                [{"seed_key": "medication:aspirin", "name": "Aspirin"}]
            ),
            "items/default_catalog.json": _catalog(
                units=[
                    {"symbol": "mg/dL", "name": "mg per dL", "quantity_type": "MASS_CONCENTRATION"}
                ],
                biomarkers=[],
            ),
        },
    )
    parsed, errors = parse_pack(manifest, files, app_version="9.9.9")
    assert errors == []
    assert parsed.stage_items("medications")[0]["seed_key"] == "medication:aspirin"
    assert parsed.stage_items("default_catalog") == []
    assert set(parsed.stage_data("default_catalog")) == {"units", "biomarkers"}
    assert parsed.digest == pack_digest(manifest)
    assert parsed.files["items/medications.json"].stage == "medications"


def test_parse_pack_refuses_invalid_input():
    parsed, errors = parse_pack(_manifest(), {})
    assert parsed is None
    assert "ITEM_FILE_MISSING" in [error.code for error in errors]


# --- bundled pack ------------------------------------------------------------


def test_bundled_pack_builds_and_validates_with_no_timestamps():
    manifest, files = build_bundled_pack(SEEDS_DIR)
    errors = validate_pack(manifest, files)
    assert errors == [], errors
    assert manifest["scope"] == "system" and manifest["priority"] == 0
    assert {entry["path"] for entry in manifest["item_files"]} == {
        f"items/{item.filename}" for item in REGISTRY
    }
    serialized = json.dumps(manifest)
    assert "last_updated" not in serialized
    assert "generated_at" not in serialized


def test_bundled_build_is_deterministic_and_digest_stable():
    first_manifest, first_files = build_bundled_pack(SEEDS_DIR)
    second_manifest, second_files = build_bundled_pack(SEEDS_DIR)
    assert json.dumps(first_manifest, sort_keys=True) == json.dumps(second_manifest, sort_keys=True)
    assert first_files == second_files
    assert pack_digest(first_manifest) == pack_digest(second_manifest)


def test_bundled_load_parses_every_stage():
    parsed = load_bundled_pack(SEEDS_DIR)
    stages = {parsed_file.stage for parsed_file in parsed.files.values()}
    assert "omb_picklist" in stages
    assert parsed.stage_items("medications")
    assert all("seed_key" in item for item in parsed.stage_items("medications"))
    assert set(omb_picklist(SEEDS_DIR)) == {"races", "ethnicities", "languages"}
    assert parsed.digest == pack_digest(parsed.manifest)


def test_bundled_build_refuses_unregistered_and_missing_files(tmp_path):
    fake = tmp_path / "seeds"
    fake.mkdir()
    with pytest.raises(SeedPackInvalid) as missing:
        build_bundled_pack(fake)
    assert any(error.code == "ITEM_FILE_MISSING" for error in missing.value.errors)

    import shutil

    shutil.copytree(SEEDS_DIR, fake, dirs_exist_ok=True)
    (fake / "glossary.json").write_text('{"metadata": {}, "items": []}', encoding="utf-8")
    with pytest.raises(SeedPackInvalid) as stray:
        build_bundled_pack(fake)
    assert stray.value.errors[0].code == "ITEM_TYPE_UNKNOWN"


def test_bundled_default_seeds_dir_is_the_repo_copy():
    assert bundled_seeds_dir() == SEEDS_DIR


# --- CLI ---------------------------------------------------------------------


def test_cli_pack_validate_digest_round_trip(tmp_path, capsys):
    out = tmp_path / "bundled"
    assert cmd_pack(out, SEEDS_DIR, check=False, against=None) == 0
    pack_digest_out = capsys.readouterr().out.splitlines()[0]

    assert cmd_validate(out) == 0
    capsys.readouterr()

    assert cmd_digest(out) == 0
    assert capsys.readouterr().out.splitlines()[0] == pack_digest_out

    second = tmp_path / "bundled-again"
    assert cmd_pack(second, SEEDS_DIR, check=False, against=None) == 0
    capsys.readouterr()
    assert (second / "manifest.json").read_bytes() == (out / "manifest.json").read_bytes()

    assert cmd_pack(out, SEEDS_DIR, check=True, against=None) == 0
    capsys.readouterr()


def test_cli_validate_reports_typed_errors(tmp_path, capsys):
    pack_dir = tmp_path / "bad-pack"
    (pack_dir / "items").mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps(
            {
                "id": "bad-pack",
                "schema_version": "1.0",
                "version": "1.0.0",
                "license": "MIT",
                "scope": "tenant",
                "priority": 200,
                "item_files": [{"path": "items/concepts.json", "sha256": "0" * 64}],
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "items" / "concepts.json").write_text(
        '{"metadata": {}, "items": []}', encoding="utf-8"
    )
    assert cmd_validate(pack_dir) == 1
    assert "GLOBAL_ONLY_ITEM_TYPE" in capsys.readouterr().out


def test_pack_error_repr_carries_code_and_location():
    error = PackError("DUPLICATE_SEED_KEY", "items/medications.json", "medication:aspirin twice")
    assert "DUPLICATE_SEED_KEY" in str(error)
    assert "items/medications.json" in str(error)
