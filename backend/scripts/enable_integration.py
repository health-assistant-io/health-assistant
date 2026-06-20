#!/usr/bin/env python3
"""
Enable or disable a system integration domain.

Integrations are invisible until a SYSTEM_ADMIN enables them. This script is a
headless equivalent of the Admin UI toggle (``/admin/system/integrations``) —
useful for dev/CI where you want to turn on an integration (e.g. ``fhir_server``)
without a browser.

Usage:
    python scripts/enable_integration.py <domain> [--disable]
    python scripts/enable_integration.py fhir_server
    python scripts/enable_integration.py fhir_server --disable

After enabling, restart the backend (or hit POST /admin/system/integrations/<domain>/enable)
so the registry reloads the provider.
"""
import argparse
import asyncio
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE
from app.models.system_integration import SystemIntegration


async def toggle(domain: str, enable: bool) -> None:
    if not DATABASE_AVAILABLE:
        print("Database is not available — check DATABASE_URL.")
        return

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(SystemIntegration).where(SystemIntegration.domain == domain))
        ).scalar_one_or_none()
        if existing:
            existing.is_enabled = enable
        else:
            db.add(SystemIntegration(domain=domain, is_enabled=enable))
        await db.commit()

    state = "enabled" if enable else "disabled"
    print(f"Integration '{domain}' is now {state}. Restart the backend to load the provider.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Enable/disable a system integration.")
    parser.add_argument("domain", help="Integration domain, e.g. 'fhir_server'")
    parser.add_argument("--disable", action="store_true", help="Disable instead of enable")
    args = parser.parse_args()
    asyncio.run(toggle(args.domain, enable=not args.disable))


if __name__ == "__main__":
    main()
