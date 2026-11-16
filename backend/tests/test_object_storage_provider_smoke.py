"""Opt-in real-provider smoke: conformance against S3 or Azure Blob.

Never required for a normal PR. Set the provider env (and
``HA_TEST_PROVIDER_SMOKE=1``) to run:

    HA_TEST_PROVIDER_SMOKE=1 HA_OBJECT_STORAGE_BACKEND=s3 \
    HA_S3_BUCKET=... HA_S3_REGION=... HA_S3_PREFIX=test/smoke \
    ... pytest tests/test_object_storage_provider_smoke.py

Azure uses the same switch with ``HA_OBJECT_STORAGE_BACKEND=azure_blob`` and
the ``HA_AZURE_STORAGE_*`` variables. Without the opt-in and credentials the
test skips — cloud credentials are never a normal-CI dependency.
"""

from __future__ import annotations

import os

import pytest

from app.storage.bootstrap import (
    ENTRYPOINT_SERVER,
    StorageConfig,
    build_store,
)
from app.storage.object_storage import run_conformance


def _config(backend: str) -> StorageConfig:
    env = {key: value for key, value in os.environ.items() if key.startswith("HA_")}
    env["HA_OBJECT_STORAGE_BACKEND"] = backend
    return StorageConfig.from_env(env, "HA", entrypoint=ENTRYPOINT_SERVER)


def _opt_in(backend: str) -> None:
    if os.environ.get("HA_TEST_PROVIDER_SMOKE") != "1":
        pytest.skip("set HA_TEST_PROVIDER_SMOKE=1 to run the provider smoke")
    if backend == "s3" and not os.environ.get("HA_S3_BUCKET"):
        pytest.skip("HA_S3_BUCKET is not set")
    if backend == "azure_blob" and not (
        os.environ.get("HA_AZURE_STORAGE_CONTAINER")
        and (
            os.environ.get("HA_AZURE_STORAGE_CONNECTION_STRING")
            or os.environ.get("HA_AZURE_STORAGE_ACCOUNT_URL")
        )
    ):
        pytest.skip("Azure container/credentials are not set")


@pytest.mark.parametrize("backend", ["s3", "azure_blob"])
def test_provider_conformance(backend: str) -> None:
    _opt_in(backend)
    store = build_store(_config(backend))
    try:
        failures = run_conformance(store)
    finally:
        store.close()
    assert failures == []
