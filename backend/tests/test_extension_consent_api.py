"""The consent surface API (plan 7 W3) — pending inbox, the 8C delta and
the echo-verified decision write, over HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient

from app.core.config import settings


@pytest_asyncio.fixture
async def lane_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "HA_APP_ENV", "development")
    monkeypatch.setattr(settings, "HA_EXTENSION_LOCAL_SOURCE_ROOTS", str(tmp_path))
    monkeypatch.setattr(settings, "HA_UPLOAD_DIR", str(tmp_path / "data"))
    return tmp_path


def _bundle(root: Path, version: str, capabilities: list[str]) -> Path:
    manifest = {
        "apiVersion": "extension/v1",
        "kindVersion": 1,
        "entry": "extension.py",
        "mcp": {"transport": "stdio"},
        "kind": "connector",
        "supportsIncremental": True,
        "id": "demo-t3-mcp",
        "version": version,
        "author": "Example Corp",
        "license": "MIT",
        "repository": "https://git.example.com/ext/demo-t3-mcp",
        "capabilities": capabilities,
        "itemSchema": {"type": "object", "properties": {"external-id": {"type": "string"}}},
        "i18n": {
            "defaultLocale": "en",
            "locales": {"en": {"title": "Demo T3 MCP", "summary": "A demo external MCP connector"}},
        },
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "extension.py").write_text("# demo entry\n", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


async def _approve_version(
    async_client: AsyncClient, headers: dict, bundle: Path, version: str, capabilities: list[str]
) -> dict:
    add = await async_client.post(
        "/api/v1/extensions/sources",
        headers=headers,
        json={
            "document": {
                "local": {"path": str(bundle)},
                "ref": {"strategy": "branch", "value": "main"},
            },
            "trust_tier": "T3",
        },
    )
    assert add.status_code == 200, add.text
    source = add.json()
    sync = await async_client.post(
        f"/api/v1/extensions/sources/{source['id']}/sync",
        headers=headers,
        json={"content_path": str(bundle)},
    )
    assert sync.status_code == 200, sync.text
    version_row = sync.json()["versions"][0]
    approve = await async_client.post(
        f"/api/v1/extensions/sources/versions/{version_row['id']}/approve", headers=headers
    )
    assert approve.status_code == 200, approve.text
    return approve.json()


async def test_pending_delta_decide_round_trip(
    async_client: AsyncClient, system_admin_headers: dict[str, str], lane_env
):
    headers = system_admin_headers
    v1 = await _approve_version(
        async_client, headers, _bundle(lane_env / "v1", "1.0.0", ["sync"]), "1.0.0", ["sync"]
    )

    pending = await async_client.get("/api/v1/integrations/consents/pending", headers=headers)
    assert pending.status_code == 200, pending.text
    assert any(row["versionId"] == v1["id"] for row in pending.json())
    first = next(row for row in pending.json() if row["versionId"] == v1["id"])
    assert first["disclosureKey"] == "t3PerimeterDisclosure"
    assert first["disclosure"].startswith("This extension runs as a separate process")

    delta = await async_client.get(
        f"/api/v1/integrations/consents/versions/{v1['id']}/delta", headers=headers
    )
    assert delta.status_code == 200, delta.text
    body = delta.json()
    assert body["capabilitiesAdded"] == ["sync"]
    assert body["requiresReConsent"] is True

    good_echo = {"digest": v1["digest"], "capabilities": ["sync"], "version": "1.0.0"}
    forged = {**good_echo, "digest": "0" * 64}
    forged_response = await async_client.post(
        f"/api/v1/integrations/consents/versions/{v1['id']}",
        headers=headers,
        json={"decisions": {"sync": "granted"}, "echo": forged},
    )
    assert forged_response.status_code == 409, forged_response.text
    assert forged_response.json()["detail"]["code"] == "invalid"

    ok = await async_client.post(
        f"/api/v1/integrations/consents/versions/{v1['id']}",
        headers=headers,
        json={"decisions": {"sync": "granted"}, "echo": good_echo},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["decisions"] == {"sync": "granted"}

    pending_after = await async_client.get("/api/v1/integrations/consents/pending", headers=headers)
    assert all(row["versionId"] != v1["id"] for row in pending_after.json())

    undeclared = await async_client.post(
        f"/api/v1/integrations/consents/versions/{v1['id']}",
        headers=headers,
        json={"decisions": {"net": "granted"}, "echo": good_echo},
    )
    assert undeclared.status_code == 409
    assert undeclared.json()["detail"]["code"] == "invalid"


async def test_secret_slots_endpoint(
    async_client: AsyncClient, system_admin_headers: dict[str, str], lane_env
):
    bundle = _bundle(lane_env / "secrets", "1.0.0", ["sync"])
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["secrets"] = [
        {"id": "api-token", "purpose": "Upstream token", "domains": ["api.example.com"]}
    ]
    manifest["configSchema"] = {
        "type": "object",
        "properties": {"board-token": {"type": "string", "title": "Board token", "x-secret": True}},
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    version = await _approve_version(async_client, system_admin_headers, bundle, "1.0.0", ["sync"])

    response = await async_client.get(
        f"/api/v1/integrations/consents/versions/{version['id']}/secrets",
        headers=system_admin_headers,
    )
    assert response.status_code == 200, response.text
    slots = response.json()["slots"]
    assert {slot["slot"] for slot in slots} == {"api-token", "board-token"}
    assert all("value" not in slot for slot in slots), "slot names + destinations, never values"
