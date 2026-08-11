"""Public, unauthenticated server configuration.

Surfaces non-sensitive values clients need before they have a credential —
notably the canonical base URL for the mobile-app onboarding QR and the
frontend/PWA origin for deep links. Resolution lives in
:func:`app.core.public_config.resolve_public_config` (shared with the bridge
``GET /status`` handler so the mobile app fetches the frontend origin over the
same ktor connection it already uses).

``APP_URL`` itself is the OAuth ``iss`` token-issuer claim, so it is NOT
admin-editable — the dedicated ``mobile.*`` settings are the presentation-only
overrides. No secrets.
"""
from fastapi import APIRouter

from app.core.database import AsyncSessionLocal
from app.core.public_config import resolve_public_config

router = APIRouter()


@router.get("/config/public")
async def get_public_config():
    try:
        async with AsyncSessionLocal() as db:
            return await resolve_public_config(db)
    except Exception:
        # Settings table unavailable (e.g. DATABASE_AVAILABLE=False) → env-only.
        return await resolve_public_config(None)
