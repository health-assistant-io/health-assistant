"""Seed or expand the anatomy graph catalog.

Usage:
    # Re-seed the base anatomy catalog (ships with the app)
    python scripts/seed_anatomy.py

    # Import a custom anatomy expansion pack from a JSON file
    python scripts/seed_anatomy.py --file path/to/my-anatomy-pack.json

    # Import from a URL
    python scripts/seed_anatomy.py --url https://example.com/anatomy-pack.json

The script is idempotent: existing nodes are updated by slug, new nodes are
inserted, and duplicate edges are skipped. No data is ever deleted.

Docker usage:
    docker compose --env-file .env -f docker/docker-compose.standalone.yml \
        exec backend python scripts/seed_anatomy.py
"""
import asyncio
import sys
import os
import json
import argparse
from pathlib import Path

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.seed_service import seed_service
from app.services.anatomy_import_service import AnatomyImportService
from app.schemas.anatomy_import import AnatomyImportPayload
from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE


async def seed_base():
    if not DATABASE_AVAILABLE:
        print("❌ Error: Database is not available.")
        sys.exit(1)

    print("🫀 Starting anatomy graph catalog sync (base seed)...")
    stats = await seed_service.seed_body_parts()

    print("-" * 40)
    print("✅ Base anatomy sync complete!")
    print(f"  Nodes added:   {stats.get('added', 0)}")
    print(f"  Nodes updated: {stats.get('updated', 0)}")
    print(f"  Errors:        {stats.get('errors', 0)}")
    print("-" * 40)
    print("💡 The base catalog includes 54 structures (systems, organs,")
    print("   regions, joints) with SNOMED CT codes and 67 relationships.")


async def import_file(file_path: str):
    if not DATABASE_AVAILABLE:
        print("❌ Error: Database is not available.")
        sys.exit(1)

    p = Path(file_path)
    if not p.exists():
        print(f"❌ Error: File not found: {file_path}")
        sys.exit(1)

    print(f"📦 Importing anatomy pack from file: {file_path}")

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        await _import_payload(data)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON: {e}")
        sys.exit(1)


async def import_url(url: str):
    if not DATABASE_AVAILABLE:
        print("❌ Error: Database is not available.")
        sys.exit(1)

    print(f"🌐 Fetching anatomy pack from URL: {url}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
            data = response.json()
        await _import_payload(data)
    except httpx.HTTPError as e:
        print(f"❌ Error fetching URL: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON response: {e}")
        sys.exit(1)


async def _import_payload(data: dict):
    try:
        payload = AnatomyImportPayload.model_validate(data)
    except Exception as e:
        print(f"❌ Error: Invalid anatomy payload: {e}")
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        service = AnatomyImportService(session)
        stats = await service.import_graph(payload)

    print("-" * 40)
    print("✅ Anatomy pack import complete!")
    print(f"  Nodes added:   {stats.get('nodes_added', 0)}")
    print(f"  Nodes updated: {stats.get('nodes_updated', 0)}")
    print(f"  Edges added:   {stats.get('edges_added', 0)}")
    print(f"  Edges updated: {stats.get('edges_updated', 0)}")
    print(f"  Errors:        {stats.get('errors', 0)}")
    print("-" * 40)

    if stats.get("errors", 0) > 0:
        print("⚠️  Some edges could not be resolved (missing slug references).")
        print("   Check the backend logs for details.")


def main():
    parser = argparse.ArgumentParser(
        description="Seed or expand the anatomy graph catalog."
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Path to a custom anatomy JSON file to import (expansion pack).",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="URL to a custom anatomy JSON file to import (expansion pack).",
    )
    args = parser.parse_args()

    if args.file:
        asyncio.run(import_file(args.file))
    elif args.url:
        asyncio.run(import_url(args.url))
    else:
        asyncio.run(seed_base())


if __name__ == "__main__":
    main()
