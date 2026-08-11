"""Resolution of the server's public, client-facing URLs.

Shared by the unauthenticated ``GET /api/v1/config/public`` endpoint and the
bridge ``GET /status`` handler so the mobile app can learn the frontend/PWA
origin over the same ktor connection it already uses (no second network stack,
no separate cleartext-policy surface). No secrets.
"""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.system_setting import SystemSetting


async def _setting(db: AsyncSession, key: str) -> str:
    return (await SystemSetting.get_value(db, key, default="") or "").strip()


async def resolve_public_config(db: Optional[AsyncSession]) -> dict:
    """Resolve the public URLs the mobile app needs.

    * ``client_base_url`` — the SYSTEM ``mobile.client_base_url`` setting →
      ``APP_URL``.
    * ``frontend_base_url`` — the SYSTEM ``mobile.frontend_base_url`` setting →
      the dedicated ``FRONTEND_URL`` env → ``client_base_url`` → ``APP_URL``.
      ``FRONTEND_URL`` (not ``APP_URL``) is the source of truth for the
      frontend/PWA origin in split dev (backend :8000 / frontend :3000):
      ``APP_URL`` is the OAuth issuer / backend URL.

    ``db`` may be ``None`` (or the settings table unavailable) — the resolution
    then falls back to env vars only.
    """
    client_base_url = ""
    frontend_base_url = ""
    if db is not None:
        try:
            client_base_url = await _setting(db, "mobile.client_base_url")
            frontend_base_url = await _setting(db, "mobile.frontend_base_url")
        except Exception:
            client_base_url = ""
            frontend_base_url = ""

    resolved_client = client_base_url or settings.APP_URL
    resolved_frontend = (
        frontend_base_url
        or settings.FRONTEND_URL
        or resolved_client
        or settings.APP_URL
    )
    return {
        "app_url": settings.APP_URL,
        "client_base_url": resolved_client,
        "frontend_base_url": resolved_frontend,
        "demo_mode": getattr(settings, "DEMO_MODE", False),
    }
