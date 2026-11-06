"""System-admin integration availability surface (``/admin/integrations``).

The admin page is the same extension registry seen through an
availability lens: the list must carry the wire-manifest identity fields
(capabilities, categories, kind) so the UI can render the kit card, mark
rows that no longer exist on disk, and let a SYSTEM_ADMIN flip global
availability.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal
from app.extensions.registry import integration_registry
from app.models.system_integration import SystemIntegration


def _ensure_registry_loaded() -> None:
    if not integration_registry.get_all_manifests():
        integration_registry._load_manifests()


@pytest.mark.asyncio
async def test_list_carries_wire_manifest_identity(
    async_client: AsyncClient, system_admin_headers: dict[str, str]
):
    _ensure_registry_loaded()
    response = await async_client.get("/api/v1/admin/integrations", headers=system_admin_headers)

    assert response.status_code == 200, response.text
    rows = {row["domain"]: row for row in response.json()}
    assert rows, "the admin list must include the discovered extensions"

    fhir = rows["fhir_server"]
    assert fhir["on_filesystem"] is True
    assert fhir["is_enabled"] is True
    assert "sync" in fhir["capabilities"]
    assert isinstance(fhir["categories"], list)
    assert "document" in (fhir.get("description") or "") or fhir["capabilities"]


@pytest.mark.asyncio
async def test_disable_hides_from_users_but_stays_on_the_admin_list(
    async_client: AsyncClient, system_admin_headers: dict[str, str]
):
    _ensure_registry_loaded()
    disabled = await async_client.post(
        "/api/v1/admin/integrations/webhook/disable", headers=system_admin_headers
    )
    assert disabled.status_code == 200, disabled.text

    admin_rows = {
        row["domain"]: row
        for row in (
            await async_client.get("/api/v1/admin/integrations", headers=system_admin_headers)
        ).json()
    }
    assert admin_rows["webhook"]["is_enabled"] is False

    available = await async_client.get(
        "/api/v1/integrations/available", headers=system_admin_headers
    )
    assert available.status_code == 200, available.text
    assert "webhook" not in {row["domain"] for row in available.json()}

    enabled = await async_client.post(
        "/api/v1/admin/integrations/webhook/enable", headers=system_admin_headers
    )
    assert enabled.status_code == 200, enabled.text
    assert integration_registry.get_manifest("webhook") is not None

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(SystemIntegration).where(SystemIntegration.domain == "webhook")
        )
        await session.commit()


@pytest.mark.asyncio
async def test_non_admin_is_refused(async_client: AsyncClient):
    from app.models.enums import Role
    from tests._auth_helpers import make_user_headers

    _user, headers = await make_user_headers(role=Role.USER)
    response = await async_client.get("/api/v1/admin/integrations", headers=headers)
    assert response.status_code == 403
