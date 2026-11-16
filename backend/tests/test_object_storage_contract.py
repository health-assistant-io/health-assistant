"""Vendored family object-storage contract wall (ADR-0057 / plan 55 P1).

Every vendored copy must satisfy the same fixture table; the copy is
byte-identical to the family canonical and never edited in-repo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.storage import object_storage as contract

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "object_storage.fixtures.json"
FIXTURES: dict[str, Any] = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


def _assert_error(call: Any, row: dict[str, Any]) -> None:
    with pytest.raises(Exception) as caught:
        call()
    assert type(caught.value).__name__ == row["error"], (
        f"{row['name']}: expected {row['error']}, got {type(caught.value).__name__}"
    )


def test_fixture_table_is_complete() -> None:
    expected = {
        "defaults",
        "keys",
        "joins",
        "contentKeys",
        "tenantKeys",
        "extensions",
        "filenameExtensions",
        "profiles",
        "backendResolution",
        "presignTtl",
        "errors",
        "envNames",
        "redaction",
        "configs",
    }
    assert expected <= set(FIXTURES)
    assert FIXTURES["contract"] == contract.CONTRACT_VERSION
    for group in expected:
        assert FIXTURES[group], f"{group} must not be empty"


def test_defaults() -> None:
    rows = FIXTURES["defaults"]
    assert rows["presignTtl"] == contract.DEFAULT_PRESIGN_TTL_SECONDS
    assert rows["presignMin"] == contract.MIN_PRESIGN_TTL_SECONDS
    assert rows["presignMax"] == contract.MAX_PRESIGN_TTL_SECONDS
    assert rows["maxObjectMb"] == contract.DEFAULT_MAX_OBJECT_MB
    assert rows["hardMaxObjectMb"] == contract.HARD_MAX_OBJECT_MB
    assert rows["maxKeyLength"] == contract.MAX_KEY_LENGTH
    assert rows["backends"] == list(contract.BACKENDS)
    assert rows["objectBackends"] == list(contract.OBJECT_BACKENDS)
    assert rows["entrypoints"] == list(contract.ENTRYPOINTS)
    assert rows["cloudProfiles"] == list(contract.CLOUD_PROFILES)
    assert rows["sseModes"] == list(contract.SSE_MODES)
    assert rows["cloudRequiredSse"] == list(contract.CLOUD_REQUIRED_SSE)


@pytest.mark.parametrize("row", FIXTURES["keys"], ids=lambda row: row["name"])
def test_key_law(row: dict[str, Any]) -> None:
    if "error" in row:
        _assert_error(lambda: contract.normalize_key(row["key"]), row)
    else:
        assert contract.normalize_key(row["key"]) == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["joins"], ids=lambda row: row["name"])
def test_join_key(row: dict[str, Any]) -> None:
    if "error" in row:
        _assert_error(lambda: contract.join_key(*row["parts"]), row)
    else:
        assert contract.join_key(*row["parts"]) == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["contentKeys"], ids=lambda row: row["name"])
def test_content_key(row: dict[str, Any]) -> None:
    if "error" in row:
        _assert_error(lambda: contract.content_key(row["sha256"], row.get("prefix", "")), row)
    else:
        assert contract.content_key(row["sha256"], row.get("prefix", "")) == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["tenantKeys"], ids=lambda row: row["name"])
def test_tenant_key(row: dict[str, Any]) -> None:
    def call() -> str:
        return contract.tenant_key(
            row["tenant"],
            row["category"],
            row["objectId"],
            row.get("extension", ""),
            row.get("prefix", ""),
        )

    if "error" in row:
        _assert_error(call, row)
    else:
        assert call() == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["extensions"], ids=lambda row: row["name"])
def test_safe_extension(row: dict[str, Any]) -> None:
    assert contract.safe_extension(row["value"]) == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["filenameExtensions"], ids=lambda row: row["name"])
def test_filename_extension(row: dict[str, Any]) -> None:
    assert contract.filename_extension(row["value"]) == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["profiles"], ids=lambda row: row["name"])
def test_cloud_profile(row: dict[str, Any]) -> None:
    if "error" in row:
        _assert_error(lambda: contract.normalize_cloud_profile(row["value"]), row)
    else:
        assert contract.normalize_cloud_profile(row["value"]) == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["backendResolution"], ids=lambda row: row["name"])
def test_backend_resolution(row: dict[str, Any]) -> None:
    def call() -> str:
        return contract.resolve_backend(
            row["entrypoint"],
            row.get("requested"),
            row.get("profile"),
            require_object_storage=bool(row.get("required", False)),
        )

    if "error" in row:
        _assert_error(call, row)
    else:
        assert call() == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["presignTtl"], ids=lambda row: row["name"])
def test_presign_ttl(row: dict[str, Any]) -> None:
    if "error" in row:
        _assert_error(lambda: contract.clamp_presign_ttl(row["ttl"]), row)
    else:
        assert contract.clamp_presign_ttl(row["ttl"]) == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["errors"], ids=lambda row: row["name"])
def test_backend_error_classification(row: dict[str, Any]) -> None:
    assert contract.classify_backend_error(row["backend"], row["code"]) == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["envNames"], ids=lambda row: row["name"])
def test_env_name(row: dict[str, Any]) -> None:
    if "error" in row:
        _assert_error(lambda: contract.env_name(row["prefix"], row["env"]), row)
    else:
        assert contract.env_name(row["prefix"], row["env"]) == row["expected"]


@pytest.mark.parametrize("row", FIXTURES["redaction"], ids=lambda row: row["name"])
def test_redaction(row: dict[str, Any]) -> None:
    config: contract.S3Config | contract.AzureBlobConfig
    if row["backend"] == "s3":
        config = contract.S3Config(
            bucket=row["bucket"],
            access_key_id=row.get("accessKey"),
            secret_access_key=row.get("secretKey"),
        )
        actual = config.redacted()["secret_access_key"]
    else:
        config = contract.AzureBlobConfig(
            container=row["container"],
            connection_string=row.get("connectionString"),
        )
        actual = config.redacted()["connection_string"]
    assert actual == row["expectedSecret"]


@pytest.mark.parametrize("row", FIXTURES["configs"], ids=lambda row: row["name"])
def test_boot_config_validation(row: dict[str, Any], tmp_path: Path) -> None:
    def call() -> contract.StorageConfig:
        return contract.StorageConfig.from_env(
            row["env"],
            "APP",
            entrypoint=row["entrypoint"],
            default_local_root=(None if row.get("withoutDefaultRoot") else str(tmp_path / "blobs")),
        )

    if "error" in row:
        _assert_error(call, row)
        return
    config = call()
    assert config.backend == row["expectedBackend"]
    assert config.required == row["expectedRequired"]
    if "expectedSse" in row:
        assert config.s3 is not None
        assert config.s3.sse == row["expectedSse"]


def test_local_conformance(tmp_path: Path) -> None:
    config = contract.StorageConfig(
        backend=contract.BACKEND_LOCAL,
        entrypoint=contract.ENTRYPOINT_SERVER,
        local_root=tmp_path / "store",
        max_object_mb=1,
        presign_ttl_seconds=contract.DEFAULT_PRESIGN_TTL_SECONDS,
    ).validate()
    store = contract.build_store(config)
    try:
        assert contract.run_conformance(store) == []
    finally:
        store.close()
