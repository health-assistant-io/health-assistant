"""Tests for the unauthenticated `GET /config/public` endpoint.

Covers the resolution of the mobile connect URL and the frontend/PWA origin:
`mobile.frontend_base_url` → `FRONTEND_URL` → `mobile.client_base_url` → `APP_URL`.
"""
import pytest

from app.core.config import settings


@pytest.mark.asyncio
async def test_public_config_defaults_to_app_url(async_client):
    # With no mobile settings stored, the connect URL resolves to APP_URL; the
    # frontend origin to the dedicated FRONTEND_URL (falls back to APP_URL only
    # when FRONTEND_URL itself is unset).
    res = await async_client.get("/api/v1/config/public")
    assert res.status_code == 200
    data = res.json()
    assert data["app_url"] == settings.APP_URL
    assert data["client_base_url"] == settings.APP_URL
    assert data["frontend_base_url"] == (
        settings.FRONTEND_URL or settings.APP_URL
    )
    assert "demo_mode" in data


@pytest.mark.asyncio
async def test_public_config_prefers_mobile_settings(async_client):
    from app.core.database import AsyncSessionLocal
    from app.models.system_setting import SystemSetting

    async with AsyncSessionLocal() as db:
        await SystemSetting.set_value(
            db, "mobile.client_base_url", "http://10.0.0.5:8000"
        )
        await SystemSetting.set_value(
            db, "mobile.frontend_base_url", "http://10.0.0.5:3000"
        )

    res = await async_client.get("/api/v1/config/public")
    data = res.json()

    assert data["client_base_url"] == "http://10.0.0.5:8000"
    assert data["frontend_base_url"] == "http://10.0.0.5:3000"


@pytest.mark.asyncio
async def test_frontend_base_url_falls_back_to_client(async_client):
    from app.core.config import settings as cfg
    from app.core.database import AsyncSessionLocal
    from app.models.system_setting import SystemSetting

    async with AsyncSessionLocal() as db:
        await SystemSetting.set_value(
            db, "mobile.client_base_url", "http://10.0.0.5:8000"
        )
        # Clear any frontend override from a prior test.
        await SystemSetting.set_value(db, "mobile.frontend_base_url", "")

    res = await async_client.get("/api/v1/config/public")
    data = res.json()

    # The frontend origin prefers the dedicated FRONTEND_URL over the client
    # connect URL — split dev (backend :8000, frontend :3000) must advertise the
    # frontend port, not the backend one.
    assert data["frontend_base_url"] == (cfg.FRONTEND_URL or "http://10.0.0.5:8000")
