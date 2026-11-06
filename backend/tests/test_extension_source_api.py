"""The extension source lane API — admin surface (plan 7 W2).

The HTTP seam drives the same lifecycle functions: register → sync →
approve, posture rows read back, and the bundled registration-failure
surface. Non-SYSTEM_ADMIN callers are refused.
"""

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


def _t3_bundle(root: Path, version: str = "1.0.0") -> Path:
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
        "capabilities": ["sync"],
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


async def test_lane_end_to_end_through_the_api(
    async_client: AsyncClient, system_admin_headers: dict[str, str], lane_env
):
    bundle = _t3_bundle(lane_env / "bundle")
    headers = system_admin_headers

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
    assert source["kind"] == "file"
    assert source["trust_tier"] == "T3"

    sync = await async_client.post(
        f"/api/v1/extensions/sources/{source['id']}/sync",
        headers=headers,
        json={"content_path": str(bundle)},
    )
    assert sync.status_code == 200, sync.text
    versions = sync.json()["versions"]
    assert len(versions) == 1
    version = versions[0]
    assert version["status"] == "pending"

    posture = await async_client.get(
        f"/api/v1/extensions/sources/versions/{version['id']}/posture", headers=headers
    )
    assert posture.status_code == 200, posture.text
    rows = posture.json()
    assert rows[0]["derived_tier"] == "T3"
    assert rows[0]["posture"]["probed_from"] == "detection"

    approve = await async_client.post(
        f"/api/v1/extensions/sources/versions/{version['id']}/approve", headers=headers
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "approved"

    listed = await async_client.get("/api/v1/extensions/sources", headers=headers)
    assert listed.status_code == 200, listed.text
    mine = next(s for s in listed.json() if s["id"] == source["id"])
    assert mine["active_digest"] == version["digest"]


async def test_lane_requires_system_admin(
    async_client: AsyncClient, user_headers: dict[str, str], lane_env
):
    response = await async_client.get("/api/v1/extensions/sources", headers=user_headers)
    assert response.status_code == 403


async def test_invalid_descriptor_is_a_typed_409(
    async_client: AsyncClient, system_admin_headers: dict[str, str], lane_env
):
    response = await async_client.post(
        "/api/v1/extensions/sources",
        headers=system_admin_headers,
        json={
            "document": {
                "url": "ftp://not-https.example.com/x",
                "ref": {"strategy": "branch", "value": "main"},
            },
            "trust_tier": "T3",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "invalid"


async def test_registration_failures_surface(
    async_client: AsyncClient, system_admin_headers: dict[str, str]
):
    response = await async_client.get(
        "/api/v1/admin/integrations/registration-failures", headers=system_admin_headers
    )
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), dict)


@pytest_asyncio.fixture
async def user_headers() -> dict[str, str]:
    from app.models.enums import Role
    from tests._auth_helpers import auth_headers, create_user

    user = await create_user(role=Role.USER, full_name="Plain User")
    return await auth_headers(user)
