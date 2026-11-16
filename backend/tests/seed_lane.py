"""Shared helpers for the seed-lane tests (planner/applier/bundled — ADR 0056
program Phase 9B).

Builds validated :class:`~app.core.seed_pack.ParsedPack` objects in memory,
persists digest-approved ``(source, version)`` pairs, and offers small
assertion helpers over the provenance/run rows.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seed_pack.items import ParsedPack
from app.core.seed_pack.manifest import PACK_SCHEMA_VERSION, parse_pack
from app.models.enums import SeedSourceKind, SeedSourceScope, SeedVersionStatus
from app.models.seed_source_model import SeedItemProvenance, SeedSource, SeedSourceVersion


def make_pack(
    items_by_file: dict[str, list[dict]],
    *,
    pack_id: str = "lane-test-pack",
    priority: int = 100,
    scope: str = "system",
    replaces: list[str] | None = None,
    version: str = "1.0.0",
) -> ParsedPack:
    """An in-memory validated pack (manifest pins each file's bytes)."""
    manifest: dict[str, Any] = {
        "id": pack_id,
        "schema_version": PACK_SCHEMA_VERSION,
        "version": version,
        "license": "CC0-1.0",
        "scope": scope,
        "priority": priority,
        "item_files": [],
    }
    if replaces:
        manifest["replaces"] = replaces
    files: dict[str, bytes] = {}
    for filename, items in items_by_file.items():
        content = json.dumps(
            {"metadata": {"version": "1.0.0"}, "items": items},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        files[f"items/{filename}"] = content
        manifest["item_files"].append(
            {"path": f"items/{filename}", "sha256": hashlib.sha256(content).hexdigest()}
        )
    parsed, errors = parse_pack(manifest, files)
    assert parsed is not None, errors
    return parsed


async def make_source_version(
    session: AsyncSession,
    pack: ParsedPack,
    *,
    kind: SeedSourceKind = SeedSourceKind.FILE,
    scope: SeedSourceScope = SeedSourceScope.SYSTEM,
    tenant_id: Any | None = None,
    uri: str | None = None,
    approved: bool = True,
) -> tuple[SeedSource, SeedSourceVersion]:
    """Persist (commit) a source + the pack's digest-approved version.

    ``scope='tenant'`` sources need ``tenant_id`` (the
    ``ck_seed_sources_scope_tenant_match`` constraint).
    """
    uri = uri or f"test://{pack.manifest['id']}-{pack.digest[:12]}"
    source = SeedSource(
        kind=kind,
        uri=uri,
        priority=int(pack.manifest.get("priority", 0)),
        scope=scope,
        tenant_id=tenant_id,
        enabled=True,
    )
    session.add(source)
    await session.flush()
    status = SeedVersionStatus.APPROVED if approved else SeedVersionStatus.PENDING
    row_version = SeedSourceVersion(
        source_id=source.id,
        digest=pack.digest,
        manifest=pack.manifest,
        contract_version=str(pack.manifest.get("schema_version", "1.0")),
        status=status,
        reason="lane test",
    )
    session.add(row_version)
    await session.commit()
    await session.refresh(source)
    await session.refresh(row_version)
    return source, row_version


async def provenance_for(
    session: AsyncSession, entity_type: str, entity_id: Any
) -> SeedItemProvenance | None:
    """The provenance row for one entity, or ``None``."""
    from sqlalchemy import select

    return (
        await session.execute(
            select(SeedItemProvenance).where(
                SeedItemProvenance.entity_type == entity_type,
                SeedItemProvenance.entity_id == entity_id,
            )
        )
    ).scalar_one_or_none()
