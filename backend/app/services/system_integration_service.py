"""System-wide integration enablement lookups.

Integrations are **enabled by default** the moment they are discovered on the
filesystem. A :class:`~app.models.system_integration.SystemIntegration` row is
only written when a SYSTEM_ADMIN acts on it through the admin console — and
the meaningful state lives in its ``is_enabled`` column:

* no row → enabled (the default)
* row with ``is_enabled=True`` → enabled (admin re-enabled after disabling)
* row with ``is_enabled=False`` → disabled (the only "off" state)

Centralising the lookup here keeps the inverted semantic in one place — the
registry, the user-facing endpoints, and the admin console all ask the same
question ("is this domain explicitly disabled?") instead of re-deriving it.
"""
from __future__ import annotations

import logging
from typing import Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_integration import SystemIntegration

logger = logging.getLogger(__name__)


async def is_domain_disabled(db: AsyncSession, domain: str) -> bool:
    """Return ``True`` only when the domain is explicitly disabled.

    A missing row means "enabled by default" — this is the keystone of the
    default-on behaviour. Callers that need to gate on enablement should use
    this rather than querying ``is_enabled == True`` directly.
    """
    stmt = select(SystemIntegration).where(
        SystemIntegration.domain == domain,
        SystemIntegration.is_enabled == False,  # noqa: E712 — SQLAlchemy filter
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_disabled_domains(db: AsyncSession) -> Set[str]:
    """Return the set of domains a SYSTEM_ADMIN has explicitly disabled.

    Discovered domains **not** in this set are considered enabled. Use this
    for bulk listing paths (e.g. ``GET /integrations/available``) so the
    "default-on" semantic costs a single query regardless of how many
    integrations exist.
    """
    stmt = select(SystemIntegration).where(
        SystemIntegration.is_enabled == False  # noqa: E712 — SQLAlchemy filter
    )
    result = await db.execute(stmt)
    return {row.domain for row in result.scalars().all()}
