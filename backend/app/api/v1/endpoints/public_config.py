"""Public, unauthenticated server configuration.

Surfaces non-sensitive values clients need before they have a credential —
notably the canonical base URL for the mobile-app onboarding QR. Resolution:
the SYSTEM-level ``mobile.client_base_url`` setting (SystemSettings admin page)
wins; otherwise the server's ``APP_URL`` env var. ``APP_URL`` itself is the OAuth
``iss`` token-issuer claim, so it is NOT admin-editable — the dedicated
``mobile.client_base_url`` setting is the presentation-only override. No secrets.
"""
from fastapi import APIRouter

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.system_setting import SystemSetting

router = APIRouter()


@router.get("/config/public")
async def get_public_config():
    client_base_url = ""
    try:
        async with AsyncSessionLocal() as db:
            client_base_url = await SystemSetting.get_value(
                db, "mobile.client_base_url", default=""
            )
    except Exception:
        # Settings table unavailable (e.g. DATABASE_AVAILABLE=False) → fall back.
        client_base_url = ""
    resolved = (client_base_url or "").strip() or settings.APP_URL
    return {
        "app_url": settings.APP_URL,
        "client_base_url": resolved,
        "demo_mode": getattr(settings, "DEMO_MODE", False),
    }
