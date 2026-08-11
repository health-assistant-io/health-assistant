"""Public, unauthenticated server configuration.

Surfaces non-sensitive values clients need before they have a credential —
notably the canonical base URL for the mobile-app onboarding QR and the
frontend/PWA origin for deep links. Resolution:
* ``client_base_url`` — the SYSTEM-level ``mobile.client_base_url`` setting
  (SystemSettings admin page) wins; otherwise the server's ``APP_URL`` env var.
* ``frontend_base_url`` — the SYSTEM-level ``mobile.frontend_base_url`` setting
  wins; otherwise ``client_base_url``; otherwise ``APP_URL``. This is where the
  web frontend/PWA is served (which may be a different host/port than the API
  backend), used by the mobile app's "Open full record in browser" deep links.

``APP_URL`` itself is the OAuth ``iss`` token-issuer claim, so it is NOT
admin-editable — the dedicated ``mobile.*`` settings are the presentation-only
overrides. No secrets.
"""
from fastapi import APIRouter

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.system_setting import SystemSetting

router = APIRouter()


@router.get("/config/public")
async def get_public_config():
    async def _setting(db, key: str) -> str:
        return (await SystemSetting.get_value(db, key, default="") or "").strip()

    try:
        async with AsyncSessionLocal() as db:
            client_base_url = await _setting(db, "mobile.client_base_url")
            frontend_base_url = await _setting(db, "mobile.frontend_base_url")
    except Exception:
        # Settings table unavailable (e.g. DATABASE_AVAILABLE=False) → fall back.
        client_base_url = ""
        frontend_base_url = ""

    resolved_client = client_base_url or settings.APP_URL
    resolved_frontend = frontend_base_url or resolved_client or settings.APP_URL
    return {
        "app_url": settings.APP_URL,
        "client_base_url": resolved_client,
        "frontend_base_url": resolved_frontend,
        "demo_mode": getattr(settings, "DEMO_MODE", False),
    }
