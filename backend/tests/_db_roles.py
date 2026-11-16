"""App-role connection helpers for the tests that connect as
``neuronection_health_app`` (ADR-0054 D4/D6, ADR-0064).

The ADR-0064 credential split gave the runtime role its OWN secret
(``POSTGRES_APP_PASSWORD``, distinct from the owner's
``POSTGRES_PASSWORD``). Tests that swap only the username in the owner DSN
break under the split — use these helpers so the app-role credential comes
from one place.
"""

from __future__ import annotations

from urllib.parse import quote

from app.core.config import settings

APP_ROLE = "neuronection_health_app"

_legacy_warned = False


def app_role_password() -> str:
    """The app role's secret (ADR-0064). Falls back to POSTGRES_PASSWORD so
    dev/test volumes created before the split (shared-password legacy
    state) keep working, with a one-time warning."""
    global _legacy_warned
    password = settings.POSTGRES_APP_PASSWORD or settings.POSTGRES_PASSWORD
    if not settings.POSTGRES_APP_PASSWORD and not _legacy_warned:
        _legacy_warned = True
        print(
            "db_roles: POSTGRES_APP_PASSWORD unset — using the legacy shared "
            "password for the app role (pre-split volume; ADR-0064 wants the "
            "split everywhere)."
        )
    return password


def db_url(name: str, *, role: str | None = None, driver: str = "asyncpg") -> str:
    """A DSN for ``name``; ``role=APP_ROLE`` carries the app secret."""
    user = quote(role or settings.POSTGRES_USER, safe="")
    password = quote(
        app_role_password() if role == APP_ROLE else (settings.POSTGRES_PASSWORD or ""),
        safe="",
    )
    return (
        f"postgresql+{driver}://{user}:{password}@"
        f"{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{name}"
    )
